"""Freetv : la liste des chaînes de la Freebox (M3U), lue sur le réseau local seulement."""

from __future__ import annotations

import contextlib
import http.server
import socket
import threading

import pytest

from mydock_dock import freebox
from mydock_dock.freebox import Channel, ChannelList, FreeboxError, parse_playlist

PLAYLIST = """#EXTM3U
#EXTINF:0,1 - TF1
#EXTVLCOPT:network-caching=1000
rtsp://mafreebox.freebox.fr/fbxtv_pub/stream?namespace=1&service=201&flavour=ld
#EXTINF:0,1 - TF1 (SD)
rtsp://mafreebox.freebox.fr/fbxtv_pub/stream?namespace=1&service=201&flavour=sd
#EXTINF:0,1 - TF1 (HD)
rtsp://mafreebox.freebox.fr/fbxtv_pub/stream?namespace=1&service=201&flavour=hd
#EXTINF:0,2 - France 2
rtsp://mafreebox.freebox.fr/fbxtv_pub/stream?namespace=1&service=2&flavour=sd
#EXTINF:0,Radio Classique
http://mafreebox.freebox.fr/radio/classique.mp3
"""


# --- la liste ------------------------------------------------------------------------------------------------------


def test_a_channel_offered_in_several_qualities_is_one_channel():
    channels = parse_playlist(PLAYLIST)
    assert [(c.number, c.name) for c in channels] == [
        (1, "TF1"),
        (2, "France 2"),
        (None, "Radio Classique"),
    ]
    tf1 = channels[0]
    assert set(tf1.variants) == {"ld", "sd", "hd"}
    assert tf1.public()["qualities"] == ["ld", "sd", "hd"]


def test_the_screen_never_gets_a_stream_address():
    public = parse_playlist(PLAYLIST)[0].public()
    assert set(public) == {"id", "number", "name", "qualities"}
    assert "rtsp" not in repr(public)


def test_identifiers_are_stable_and_distinct():
    first, second = parse_playlist(PLAYLIST), parse_playlist(PLAYLIST)
    assert [c.id for c in first] == [c.id for c in second]
    assert len({c.id for c in first}) == len(first)
    assert all(len(c.id) == 10 and c.id.isalnum() for c in first)


def test_the_standard_quality_is_preferred_and_hd_on_request():
    tf1 = parse_playlist(PLAYLIST)[0]
    assert tf1.stream()[0] == "sd"
    assert tf1.stream(hd=True)[0] == "hd"
    only_hd = Channel("x", 5, "Chaîne", {"hd": "rtsp://h/hd"})
    assert only_hd.stream() == ("hd", "rtsp://h/hd")  # la seule qualité qu'elle a
    low = Channel("y", 6, "Autre", {"ld": "rtsp://h/ld", "auto": "rtsp://h/a"})
    assert low.stream()[0] == "auto"


def test_a_playlist_without_qualities_still_gives_playable_channels():
    text = "#EXTM3U\n#EXTINF:-1,7 - Arte\nrtsp://h/arte\n#EXTINF:-1,8 - M6\nrtsp://h/m6\n"
    channels = parse_playlist(text)
    assert [(c.number, c.name, list(c.variants)) for c in channels] == [
        (7, "Arte", ["auto"]),
        (8, "M6", ["auto"]),
    ]


@pytest.mark.parametrize(
    ("title", "expected"),
    [
        ("12 - Chaîne 12", (12, "Chaîne 12")),
        ("3. Gulli", (3, "Gulli")),
        ("4 : Canal+", (4, "Canal+")),
        ("Sans numéro", (None, "Sans numéro")),
    ],
)
def test_titles_with_or_without_a_number(title, expected):
    channel = parse_playlist(f"#EXTM3U\n#EXTINF:0,{title}\nrtsp://h/x\n")[0]
    assert (channel.number, channel.name) == expected


@pytest.mark.parametrize(
    "line",
    [
        "file:///etc/passwd",
        "javascript:alert(1)",
        "rtsp://h/x y",
        "ftp://h/x",
        "/chemin/local.ts",
        "rtsp://" + "a" * 600,
    ],
)
def test_only_stream_addresses_are_kept(line):
    assert parse_playlist(
        f"#EXTM3U\n#EXTINF:0,1 - X\n{line}\n#EXTINF:0,2 - Bonne\nrtsp://h/ok\n"
    ) == [Channel(parse_playlist("#EXTM3U\n#EXTINF:0,2 - Bonne\nrtsp://h/ok\n")[0].id, 2, "Bonne")]


def test_garbage_gives_no_channel():
    assert parse_playlist("") == []
    assert parse_playlist("<html>pas une liste</html>") == []
    assert parse_playlist("\x00\x01\x02" * 50) == []


def test_a_very_long_name_is_cut():
    channel = parse_playlist(f"#EXTM3U\n#EXTINF:0,1 - {'x' * 500}\nrtsp://h/x\n")[0]
    assert len(channel.name) == 80


# --- l'adresse de la liste ---------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "url",
    [
        "http://mafreebox.freebox.fr/freeboxtv/playlist.m3u",
        "https://192.168.0.254/l.m3u",
        "http://h:8080/x?a=1",
    ],
)
def test_valid_addresses(url):
    assert freebox.valid_playlist_url(url) == url


@pytest.mark.parametrize(
    "url",
    [
        "",
        "   ",
        None,
        12,
        "ftp://h/x.m3u",
        "file:///etc/passwd",
        "http://user:pw@h/x",
        "http://h/x y",
        "http:///x",
        "h/x.m3u",
        "http://h/" + "a" * 400,
    ],
)
def test_invalid_addresses(url):
    with pytest.raises(ValueError, match=r"liste|http"):
        freebox.valid_playlist_url(url)


# --- la lecture sur le réseau local ---------------------------------------------------------------------------------


class _Handler(http.server.BaseHTTPRequestHandler):
    routes: dict[str, tuple[int, dict[str, str], bytes]] = {}

    def do_GET(self):
        status, headers, body = self.routes.get(self.path, (404, {}, b""))
        self.send_response(status)
        for name, value in headers.items():
            self.send_header(name, value)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_):
        pass


@pytest.fixture
def web():
    """Un faux serveur de la Freebox, sur 127.0.0.1 (une adresse locale)."""
    routes: dict = {}
    handler = type("H", (_Handler,), {"routes": routes})
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield type(
        "W",
        (),
        {
            "routes": routes,
            "url": lambda self, path: f"http://127.0.0.1:{server.server_port}{path}",
        },
    )()
    server.shutdown()
    server.server_close()


def test_the_playlist_is_read_from_the_local_network(web):
    web.routes["/playlist.m3u"] = (200, {"Content-Type": "audio/x-mpegurl"}, PLAYLIST.encode())
    assert "TF1" in freebox.fetch_playlist(web.url("/playlist.m3u"))


def test_a_redirect_is_never_followed(web):
    web.routes["/ancienne"] = (302, {"Location": "http://example.org/p.m3u"}, b"")
    with pytest.raises(FreeboxError, match="renvoie vers une autre adresse"):
        freebox.fetch_playlist(web.url("/ancienne"))


def test_an_error_status_is_said(web):
    with pytest.raises(FreeboxError, match="404"):
        freebox.fetch_playlist(web.url("/absente"))


def test_a_web_page_is_not_a_playlist(web):
    web.routes["/page"] = (200, {}, b"<html><body>Bonjour</body></html>")
    with pytest.raises(FreeboxError, match="pas une liste"):
        freebox.fetch_playlist(web.url("/page"))


def test_a_huge_file_is_refused(web):
    web.routes["/gros"] = (200, {}, b"#EXTM3U\n" + b"x" * (freebox.MAX_BYTES + 10))
    with pytest.raises(FreeboxError, match="trop longue"):
        freebox.fetch_playlist(web.url("/gros"))


def test_an_address_outside_the_local_network_is_refused(monkeypatch):
    """Le dock ne lit la liste que chez toi : un nom qui mène à une adresse publique n'est jamais contacté."""
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *a, **k: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 80))],
    )
    with pytest.raises(FreeboxError, match="pas sur le réseau local"):
        freebox.fetch_playlist("http://exemple.test/playlist.m3u")


def test_a_name_with_one_public_address_among_local_ones_is_refused(monkeypatch):
    infos = [
        (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("192.168.0.254", 80)),
        (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 80)),
    ]
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **k: infos)
    with pytest.raises(FreeboxError, match="pas sur le réseau local"):
        freebox.fetch_playlist("http://mixte.test/playlist.m3u")


def test_an_unknown_name_says_the_freebox_may_be_elsewhere(monkeypatch):
    def refuse(*a, **k):
        raise socket.gaierror("inconnu")

    monkeypatch.setattr(socket, "getaddrinfo", refuse)
    with pytest.raises(FreeboxError, match="même réseau"):
        freebox.fetch_playlist("http://mafreebox.freebox.fr/freeboxtv/playlist.m3u")


def test_nothing_listens(monkeypatch):
    with contextlib.closing(socket.socket()) as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]  # libre et fermé juste après : personne n'écoute
    with pytest.raises(FreeboxError, match="injoignable"):
        freebox.fetch_playlist(f"http://127.0.0.1:{port}/playlist.m3u", timeout=2)


# --- la mémoire ------------------------------------------------------------------------------------------------------


async def test_the_list_is_kept_ten_minutes_then_read_again(web):
    web.routes["/p.m3u"] = (200, {}, PLAYLIST.encode())
    now = [1000.0]
    channels = ChannelList(lambda: web.url("/p.m3u"), clock=lambda: now[0])
    first, warning = await channels.get()
    assert len(first) == 3
    assert warning is None
    web.routes["/p.m3u"] = (200, {}, b"#EXTM3U\n#EXTINF:0,9 - Nouvelle\nrtsp://h/n\n")
    now[0] += 120
    assert [c.name for c in (await channels.get())[0]] == [
        "TF1",
        "France 2",
        "Radio Classique",
    ]  # encore la mémoire
    now[0] += 600
    assert [c.name for c in (await channels.get())[0]] == ["Nouvelle"]
    web.routes["/p.m3u"] = (200, {}, PLAYLIST.encode())
    assert len((await channels.get(refresh=True))[0]) == 3  # sur demande, tout de suite


async def test_the_known_list_survives_a_failed_reading_and_says_so(web):
    web.routes["/p.m3u"] = (200, {}, PLAYLIST.encode())
    now = [0.0]
    channels = ChannelList(lambda: web.url("/p.m3u"), clock=lambda: now[0])
    await channels.get()
    web.routes.clear()  # la Freebox ne répond plus
    kept, warning = await channels.get(refresh=True)
    assert len(kept) == 3
    assert "404" in warning
    assert "gardée" in warning


async def test_without_a_known_list_a_failure_is_an_error(web):
    channels = ChannelList(lambda: web.url("/absente"))
    with pytest.raises(FreeboxError, match="404"):
        await channels.get()


async def test_a_new_address_is_not_served_from_the_old_memory(web):
    web.routes["/a.m3u"] = (200, {}, PLAYLIST.encode())
    web.routes["/b.m3u"] = (200, {}, b"#EXTM3U\n#EXTINF:0,5 - Autre\nrtsp://h/o\n")
    current = [web.url("/a.m3u")]
    channels = ChannelList(lambda: current[0])
    assert len((await channels.get())[0]) == 3
    current[0] = web.url("/b.m3u")
    assert [c.name for c in (await channels.get())[0]] == ["Autre"]


async def test_a_playlist_without_any_channel_is_an_error(web):
    web.routes["/vide.m3u"] = (200, {}, b"#EXTM3U\n# rien\n")
    with pytest.raises(FreeboxError, match="aucune chaîne"):
        await ChannelList(lambda: web.url("/vide.m3u")).get()


async def test_find_returns_the_channel_by_its_identifier(web):
    web.routes["/p.m3u"] = (200, {}, PLAYLIST.encode())
    channels = ChannelList(lambda: web.url("/p.m3u"))
    listed, _ = await channels.get()
    assert channels.find(listed[1].id) == listed[1]
    assert channels.find("inconnue") is None
