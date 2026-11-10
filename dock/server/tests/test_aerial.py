"""Les vidéos de l'économiseur téléchargées par le dock : Apple, adresses saisies, listes (réseau simulé, vrai ffmpeg)."""

from __future__ import annotations

import asyncio
import io
import shutil
import subprocess
from pathlib import Path

import pytest

from mydock_dock import aerial
from mydock_dock.aerial import (
    APPLE_ID,
    CATALOG,
    AerialError,
    AerialFetcher,
    Item,
    is_list_url,
    parse_list,
    short_label,
    target_name,
    title_from_url,
)
from mydock_dock.screensaver import valid_source_url

HAVE_FFMPEG = bool(shutil.which("ffmpeg") and shutil.which("ffprobe"))
needs_ffmpeg = pytest.mark.skipif(not HAVE_FFMPEG, reason="ffmpeg absent")


class FakeResponse(io.BytesIO):
    def __init__(self, body: bytes, kind: str = "video/mp4"):
        super().__init__(body)
        self.headers = {"Content-Type": kind, "Content-Length": str(len(body))}

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False


class FakeWeb:
    """Un faux réseau : adresse -> (contenu, type) ; toute autre adresse échoue comme un serveur absent."""

    def __init__(self, pages: dict[str, tuple[bytes, str]] | None = None):
        self.pages = dict(pages or {})
        self.asked: list[str] = []

    def install(self, monkeypatch):
        def fake_open(opener, request, timeout=None):
            self.asked.append(request.full_url)
            if request.full_url not in self.pages:
                raise OSError("introuvable")
            body, kind = self.pages[request.full_url]
            return FakeResponse(body, kind)

        monkeypatch.setattr("urllib.request.OpenerDirector.open", fake_open)


def fetcher(tmp_path, *, enabled=True, apple=True, sources=(), which=lambda _n: "/usr/bin/x"):
    return AerialFetcher(
        tmp_path / "saver",
        lambda: enabled,
        apple=lambda: apple,
        sources=lambda: list(sources),
        which=which,
        start_delay=0,
        retry_delay=0.01,
    )


@pytest.fixture(scope="module")
def clips(tmp_path_factory):
    """De vrais petits films : H.264 en MP4 léger, H.264 trop large, WebM VP8, MPEG-4 en AVI."""
    if not HAVE_FFMPEG:
        return {}
    root = tmp_path_factory.mktemp("clips")

    def make(name: str, size: str, *codec: str) -> bytes:
        path = root / name
        subprocess.run(
            ["ffmpeg", "-loglevel", "error", "-y", "-f", "lavfi", "-i", f"testsrc2=size={size}:rate=12:duration=1", *codec, str(path)],
            check=True,
        )  # fmt: skip
        return path.read_bytes()

    return {
        "small.mp4": make("small.mp4", "320x180", "-c:v", "libx264", "-pix_fmt", "yuv420p"),
        "wide.mp4": make("wide.mp4", "1920x1080", "-c:v", "libx264", "-pix_fmt", "yuv420p"),
        "small.webm": make("small.webm", "320x180", "-c:v", "libvpx"),
        "old.avi": make("old.avi", "320x180", "-c:v", "mpeg4"),
    }


def _shape(path: Path) -> str:
    return subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=codec_name,width,height", "-of", "csv=p=0", str(path)],
        capture_output=True, text=True, check=True,
    ).stdout.strip()  # fmt: skip


# --- les fonctions pures -----------------------------------------------------------------------------------------


def test_the_apple_catalogue_is_ten_https_films_five_by_day_and_five_by_night():
    assert len(CATALOG) == 10
    assert aerial.BASE_URL.startswith("https://sylvan.apple.com/")
    assert len({t for _, t in CATALOG}) == 10
    for name, title in CATALOG:
        assert name.endswith(".mov")
        assert "/" not in name
        assert target_name(title).endswith(".mp4")
    assert sum("jour" in t for _, t in CATALOG) == 5
    assert all(i.url.startswith("https://") and i.source == APPLE_ID for i in aerial.apple_items())


@pytest.mark.parametrize(
    ("url", "title"),
    [
        ("https://exemple.org/videos/Mer%20calme.mp4", "Mer calme"),
        ("https://exemple.org/a/b/lac_du_soir.webm?token=1", "lac_du_soir"),
        ("https://exemple.org/", "exemple.org"),
        ("https://exemple.org/%E2%98%83.mp4", "video"),
        ("https://exemple.org/a%2Fb<c>.mov", "b_c"),
    ],
)
def test_the_caption_of_a_film_comes_from_its_file_name(url, title):
    assert title_from_url(url) == title


def test_a_very_long_name_is_cut():
    assert len(title_from_url("https://x.org/" + "a" * 400 + ".mp4")) <= 100


def test_a_text_file_is_a_list_and_the_rest_is_a_video():
    assert is_list_url("https://x.org/mes-films.txt")
    assert is_list_url("https://x.org/films.M3U?x=1")
    assert is_list_url("https://x.org/films.list")
    assert not is_list_url("https://x.org/film.mp4")
    assert not is_list_url("https://x.org/")


def test_a_list_keeps_the_valid_https_addresses_once_and_skips_the_rest():
    text = """# mes films
https://x.org/a.mp4

http://x.org/clair.mp4
https://x.org/a.mp4
  https://x.org/b.webm
ftp://x.org/c.mp4
pas une adresse
https://user:secret@x.org/d.mp4
"""
    assert parse_list(text) == ["https://x.org/a.mp4", "https://x.org/b.webm"]


def test_a_list_is_capped():
    text = "\n".join(f"https://x.org/{i}.mp4" for i in range(200))
    assert len(parse_list(text)) == aerial.MAX_LIST_ITEMS


def test_a_long_address_is_shortened_for_the_screen():
    assert short_label("https://x.org/a.mp4") == "x.org/a.mp4"
    long = "https://exemple.org/" + "dossier/" * 20 + "film.mp4"
    shortened = short_label(long)
    assert len(shortened) <= 46
    assert shortened.endswith("film.mp4")
    assert "…" in shortened


@pytest.mark.parametrize(
    "url",
    [
        "https://x.org/a.mp4",
        "https://x.org:8443/a.mp4?b=1",
        "https://192.168.1.5/films/a.mp4",
        "https://nas.local/l.txt",
    ],
)
def test_valid_addresses(url):
    assert valid_source_url(url)


@pytest.mark.parametrize(
    "url",
    [
        "http://x.org/a.mp4",
        "ftp://x.org/a.mp4",
        "file:///etc/passwd",
        "https://",
        "https://user:pw@x.org/a.mp4",
        "https://x.org/a b.mp4",
        "https://x.org/a\n.mp4",
        "https://x.org:99999/a.mp4",
        "https://x.org/" + "a" * 600,
        "x.org/a.mp4",
        "",
        None,
        42,
    ],
)
def test_invalid_addresses(url):
    assert not valid_source_url(url)


# --- l'état montré à l'écran ---------------------------------------------------------------------------------------


def test_status_counts_apple_and_each_address(tmp_path):
    a, b = "https://x.org/a.mp4", "https://x.org/liste.txt"
    f = fetcher(tmp_path, sources=[a, b])
    status = f.status()
    assert [r["id"] for r in status["sources"]] == [APPLE_ID, a, b]
    apple, one, listed = status["sources"]
    assert (apple["total"], apple["done"], apple["builtin"]) == (10, 0, True)
    assert (one["total"], one["done"], one["label"]) == (1, 0, "x.org/a.mp4")
    assert listed["total"] is None  # une liste qu'on n'a pas encore lue
    assert status["total"] == 11
    saver = tmp_path / "saver"
    saver.mkdir()
    (saver / target_name(CATALOG[0][1])).write_bytes(b"x")
    (saver / "a.webm").write_bytes(b"x")
    status = f.status()
    assert (status["done"], status["enabled"], status["active"]) == (2, True, False)


def test_status_without_apple_lists_only_the_addresses(tmp_path):
    f = fetcher(tmp_path, apple=False, sources=["https://x.org/a.mp4"])
    assert [r["id"] for r in f.status()["sources"]] == ["https://x.org/a.mp4"]
    assert fetcher(tmp_path, apple=False).status()["total"] == 0


# --- le téléchargement ----------------------------------------------------------------------------------------------


async def test_without_ffmpeg_nothing_is_downloaded_and_the_reason_is_given(tmp_path, monkeypatch):
    web = FakeWeb()
    web.install(monkeypatch)
    f = fetcher(tmp_path, which=lambda _n: None)
    with pytest.raises(AerialError, match="ffmpeg"):
        await f.fetch_missing()
    assert web.asked == []


async def test_nothing_to_do_means_no_network_and_no_ffmpeg_needed(tmp_path, monkeypatch):
    FakeWeb().install(monkeypatch)
    await fetcher(tmp_path, apple=False, which=lambda _n: None).fetch_missing()


async def test_each_apple_film_is_downloaded_converted_and_published(tmp_path, monkeypatch):
    web = FakeWeb({i.url: (b"mov" * 100, "video/quicktime") for i in aerial.apple_items()})
    web.install(monkeypatch)
    f = fetcher(tmp_path)

    async def fake_probe(_path):
        return {"codec": "hevc", "width": 1920, "format": "mov,mp4,m4a,3gp,3g2,mj2"}

    async def fake_convert(source, item):
        (f.directory / target_name(item.title)).write_bytes(b"mp4")

    monkeypatch.setattr(f, "_probe", fake_probe)
    monkeypatch.setattr(f, "_convert", fake_convert)
    await f.fetch_missing()
    assert web.asked == [i.url for i in aerial.apple_items()]
    assert f.status()["done"] == 10
    assert list((f.directory / aerial.WORK).glob("dl-*")) == []  # le brut est retiré
    assert f.current is None


async def test_a_disabled_fetcher_downloads_nothing(tmp_path, monkeypatch):
    web = FakeWeb()
    web.install(monkeypatch)
    await fetcher(tmp_path, enabled=False).fetch_missing()
    assert web.asked == []


async def test_one_missing_film_does_not_block_the_others(tmp_path, monkeypatch):
    items = aerial.apple_items()
    web = FakeWeb({i.url: (b"mov" * 100, "video/quicktime") for i in items[1:]})
    web.install(monkeypatch)
    f = fetcher(tmp_path)

    async def fake_probe(_path):
        return {"codec": "hevc", "width": 1920, "format": "mov"}

    async def fake_convert(source, item):
        (f.directory / target_name(item.title)).write_bytes(b"mp4")

    monkeypatch.setattr(f, "_probe", fake_probe)
    monkeypatch.setattr(f, "_convert", fake_convert)
    with pytest.raises(AerialError, match="1 film"):
        await f.fetch_missing()
    assert f.status()["done"] == 9
    assert "introuvable" in (f.status()["sources"][0]["error"] or "")


async def test_network_failure_is_reported_then_retried(tmp_path, monkeypatch):
    web = FakeWeb()
    web.install(monkeypatch)
    f = fetcher(tmp_path)
    task = asyncio.create_task(f.run())
    for _ in range(200):
        await asyncio.sleep(0.02)
        if len(web.asked) >= 11 and f.error:
            break
    task.cancel()
    assert len(web.asked) >= 11  # tous les films essayés, puis un nouvel essai
    assert "Téléchargement impossible" in (f.error or "")


async def test_an_html_answer_is_not_a_video(tmp_path, monkeypatch):
    FakeWeb({"https://x.org/a.mp4": (b"<html>", "text/html")}).install(monkeypatch)
    f = fetcher(tmp_path, apple=False, sources=["https://x.org/a.mp4"])
    with pytest.raises(AerialError, match="1 film"):
        await f.fetch_missing()
    assert "page" in (f.status()["sources"][0]["error"] or "")


async def test_a_full_card_stops_before_downloading(tmp_path, monkeypatch):
    web = FakeWeb()
    web.install(monkeypatch)
    monkeypatch.setattr("shutil.disk_usage", lambda _p: type("U", (), {"free": 10})())
    with pytest.raises(AerialError, match="place"):
        await fetcher(tmp_path).fetch_missing()
    assert web.asked == [] or len(web.asked) == 1  # la première demande s'arrête avant d'écrire


async def test_a_source_removed_while_downloading_is_not_published(tmp_path, monkeypatch):
    url = "https://x.org/a.mp4"
    FakeWeb({url: (b"x" * 50, "video/mp4")}).install(monkeypatch)
    sources = [url]
    f = AerialFetcher(
        tmp_path / "saver", lambda: True, apple=lambda: False, sources=lambda: list(sources),
        which=lambda _n: "/usr/bin/x", start_delay=0,
    )  # fmt: skip

    async def fake_probe(_path):
        sources.clear()  # l'adresse est retirée pendant l'examen du fichier
        return {"codec": "h264", "width": 640, "format": "mov,mp4,m4a"}

    monkeypatch.setattr(f, "_probe", fake_probe)
    await f.fetch_missing()
    assert f.status()["done"] == 0
    assert not list(f.directory.glob("*.mp4"))


# --- de vrais fichiers, de vrais ffmpeg et ffprobe ------------------------------------------------------------------


@needs_ffmpeg
async def test_a_light_h264_mp4_is_kept_as_is(tmp_path, monkeypatch, clips):
    url = "https://x.org/films/Mer_calme.mp4"
    FakeWeb({url: (clips["small.mp4"], "video/mp4")}).install(monkeypatch)
    f = fetcher(tmp_path, apple=False, sources=[url], which=shutil.which)
    await f.fetch_missing()
    assert (f.directory / "Mer_calme.mp4").read_bytes() == clips[
        "small.mp4"
    ]  # ni converti ni recompressé


@needs_ffmpeg
async def test_a_wide_film_is_converted_to_the_screen_size(tmp_path, monkeypatch, clips):
    url = "https://x.org/grand.mp4"
    FakeWeb({url: (clips["wide.mp4"], "video/mp4")}).install(monkeypatch)
    f = fetcher(tmp_path, apple=False, sources=[url], which=shutil.which)
    await f.fetch_missing()
    out = f.directory / "grand.mp4"
    shape = await asyncio.to_thread(_shape, out)
    assert shape == "h264,1024,600"


@needs_ffmpeg
async def test_a_light_webm_is_kept_as_webm(tmp_path, monkeypatch, clips):
    url = "https://x.org/lac.webm"
    FakeWeb({url: (clips["small.webm"], "video/webm")}).install(monkeypatch)
    f = fetcher(tmp_path, apple=False, sources=[url], which=shutil.which)
    await f.fetch_missing()
    assert (f.directory / "lac.webm").read_bytes() == clips["small.webm"]
    assert f.status()["done"] == 1  # présent sous .webm : compté


@needs_ffmpeg
async def test_another_format_is_converted_to_mp4(tmp_path, monkeypatch, clips):
    url = "https://x.org/ancien.avi"
    FakeWeb({url: (clips["old.avi"], "video/x-msvideo")}).install(monkeypatch)
    f = fetcher(tmp_path, apple=False, sources=[url], which=shutil.which)
    await f.fetch_missing()
    assert (f.directory / "ancien.mp4").stat().st_size > 0
    assert f.status()["done"] == 1


@needs_ffmpeg
async def test_a_file_that_is_not_a_video_is_refused_and_leaves_nothing(tmp_path, monkeypatch):
    url = "https://x.org/faux.mp4"
    FakeWeb({url: (b"ceci n'est pas une video" * 50, "application/octet-stream")}).install(
        monkeypatch
    )
    f = fetcher(tmp_path, apple=False, sources=[url], which=shutil.which)
    with pytest.raises(AerialError, match="1 film"):
        await f.fetch_missing()
    assert "pas une vidéo" in (f.status()["sources"][0]["error"] or "")
    assert not list(f.directory.glob("*.mp4"))
    assert list((f.directory / aerial.WORK).glob("*")) == []


@needs_ffmpeg
async def test_a_list_gives_several_films_and_a_bad_line_does_not_stop_the_others(
    tmp_path, monkeypatch, clips
):
    lst = "https://x.org/mes-films.txt"
    a, b, c = "https://x.org/a.webm", "https://x.org/absent.webm", "https://x.org/c.mp4"
    text = f"# mes films\n{a}\n{b}\n{c}\n".encode()
    FakeWeb(
        {
            lst: (text, "text/plain"),
            a: (clips["small.webm"], "video/webm"),
            c: (clips["small.mp4"], "video/mp4"),
        }
    ).install(monkeypatch)
    f = fetcher(tmp_path, apple=False, sources=[lst], which=shutil.which)  # fmt: skip
    with pytest.raises(AerialError, match="1 film"):
        await f.fetch_missing()
    row = f.status()["sources"][0]
    assert (row["total"], row["done"]) == (3, 2)
    assert "absent" in (row["error"] or "")
    assert (f.directory / "a.webm").exists()
    assert (f.directory / "c.mp4").exists()


async def test_a_list_that_cannot_be_read_says_so_on_its_line(tmp_path, monkeypatch):
    lst = "https://x.org/liste.txt"
    FakeWeb().install(monkeypatch)
    f = fetcher(tmp_path, apple=False, sources=[lst])
    await (
        f.fetch_missing()
    )  # rien à télécharger : pas d'exception, l'erreur est sur la ligne de la liste
    assert "injoignable" in (f.status()["sources"][0]["error"] or "")


async def test_an_empty_list_is_reported(tmp_path, monkeypatch):
    lst = "https://x.org/liste.txt"
    FakeWeb({lst: (b"# rien\npas une adresse\n", "text/plain")}).install(monkeypatch)
    f = fetcher(tmp_path, apple=False, sources=[lst])
    await f.fetch_missing()
    row = f.status()["sources"][0]
    assert "aucune adresse" in (row["error"] or "")
    assert row["total"] == 0


async def test_a_list_that_is_too_long_is_refused(tmp_path, monkeypatch):
    lst = "https://x.org/liste.txt"
    FakeWeb({lst: (b"https://x.org/a.mp4\n" * 10_000, "text/plain")}).install(monkeypatch)
    f = fetcher(tmp_path, apple=False, sources=[lst])
    await f.fetch_missing()
    assert "trop longue" in (f.status()["sources"][0]["error"] or "")


# --- retirer une source ---------------------------------------------------------------------------------------------


@needs_ffmpeg
async def test_forgetting_an_address_erases_its_films_and_only_its_films(
    tmp_path, monkeypatch, clips
):
    a, b = "https://x.org/a.mp4", "https://x.org/b.mp4"
    FakeWeb({a: (clips["small.mp4"], "video/mp4"), b: (clips["small.mp4"], "video/mp4")}).install(
        monkeypatch
    )
    sources = [a, b]
    f = AerialFetcher(
        tmp_path / "saver", lambda: True, apple=lambda: False, sources=lambda: list(sources),
        which=shutil.which, start_delay=0,
    )  # fmt: skip
    await f.fetch_missing()
    assert {p.name for p in f.directory.glob("*.mp4")} == {"a.mp4", "b.mp4"}
    sources.remove(a)
    assert f.forget(a) == 1
    assert {p.name for p in f.directory.glob("*.mp4")} == {"b.mp4"}
    assert f.status()["done"] == 1


def test_forgetting_apple_erases_the_ten_films(tmp_path):
    f = fetcher(tmp_path)
    f.directory.mkdir()
    for _, title in CATALOG:
        (f.directory / target_name(title)).write_bytes(b"x")
    (f.directory / "autre.mp4").write_bytes(b"x")
    assert f.forget(APPLE_ID) == 10
    assert [p.name for p in f.directory.glob("*.mp4")] == ["autre.mp4"]


def test_forgetting_an_unknown_source_does_nothing(tmp_path):
    f = fetcher(tmp_path)
    f.directory.mkdir()
    (f.directory / "a.mp4").write_bytes(b"x")
    assert f.forget("https://x.org/jamais-vu.mp4") == 0
    assert (f.directory / "a.mp4").exists()


def test_an_item_is_hashable_and_keeps_its_source():
    item = Item("https://x.org/a.mp4", "a", "https://x.org/a.mp4")
    assert {item} == {Item("https://x.org/a.mp4", "a", "https://x.org/a.mp4")}


# --- le vrai chemin réseau : HTTP et TLS pour de bon, contre un petit serveur https local ----------------------------


@pytest.fixture
def tls_site(tmp_path, monkeypatch, clips):
    """Un serveur https local (certificat auto-signé, reconnu via SSL_CERT_FILE) qui sert de vrais films."""
    import http.server
    import ssl
    import threading

    if not (HAVE_FFMPEG and shutil.which("openssl")):
        pytest.skip("ffmpeg ou openssl absent")
    cert, key = tmp_path / "cert.pem", tmp_path / "key.pem"
    subprocess.run(
        ["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "1", "-subj", "/CN=localhost",
         "-addext", "subjectAltName=DNS:localhost,IP:127.0.0.1", "-keyout", str(key), "-out", str(cert)],
        check=True, capture_output=True,
    )  # fmt: skip
    routes: dict[str, tuple[int, dict[str, str], bytes]] = {
        "/mer.mp4": (200, {"Content-Type": "video/mp4"}, clips["small.mp4"]),
        "/liste.txt": (200, {"Content-Type": "text/plain"}, b"https://127.0.0.1:PORT/lac.webm\n"),
        "/lac.webm": (200, {"Content-Type": "video/webm"}, clips["small.webm"]),
        "/page.mp4": (200, {"Content-Type": "text/html"}, b"<html>connexion</html>"),
        "/clair.mp4": (302, {"Location": "http://127.0.0.1:1/clair.mp4"}, b""),
        "/absent.mp4": (404, {"Content-Type": "text/plain"}, b"non"),
    }

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            status, headers, body = routes.get(self.path, (404, {}, b""))
            body = body.replace(b"PORT", str(self.server.server_port).encode())
            self.send_response(status)
            for name, value in headers.items():
                self.send_header(name, value)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_):
            pass

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(cert, key)
    server.socket = context.wrap_socket(server.socket, server_side=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    monkeypatch.setenv("SSL_CERT_FILE", str(cert))
    yield f"https://127.0.0.1:{server.server_port}"
    server.shutdown()
    server.server_close()


@needs_ffmpeg
async def test_a_video_and_a_list_are_really_downloaded_over_https(tmp_path, tls_site):
    sources = [f"{tls_site}/mer.mp4", f"{tls_site}/liste.txt"]
    f = fetcher(tmp_path, apple=False, sources=sources, which=shutil.which)
    await f.fetch_missing()
    assert {
        p.name for p in f.directory.iterdir() if p.is_file() and not p.name.startswith(".")
    } == {"mer.mp4", "lac.webm"}
    status = f.status()
    assert (status["total"], status["done"]) == (2, 2)
    assert all(r["error"] is None for r in status["sources"])
    assert (f.directory / aerial.MAP_FILE).is_file()  # l'origine des films est notée
    assert list((f.directory / aerial.WORK).glob("*")) == []


async def test_real_errors_are_said_per_source(tmp_path, tls_site):
    sources = [f"{tls_site}/page.mp4", f"{tls_site}/absent.mp4", f"{tls_site}/clair.mp4"]
    f = fetcher(tmp_path, apple=False, sources=sources)
    with pytest.raises(AerialError, match="3 film"):
        await f.fetch_missing()
    page, absent, clair = (r["error"] or "" for r in f.status()["sources"])
    assert "page" in page
    assert "404" in absent
    assert clair  # la redirection vers du http est refusée : aucune connexion en clair
    assert not [p for p in f.directory.iterdir() if p.is_file() and not p.name.startswith(".")]


# --- un certificat que le Pi ne reconnaît pas : refusé, et dit clairement -----------------------------------------------


async def test_an_unknown_authority_is_refused_with_what_the_server_presents(
    tmp_path, pki, tls_server
):
    routes = {"/mer.mp4": (200, {"Content-Type": "video/mp4"}, b"x" * 100)}
    with tls_server(pki.leaf_pem, pki.leaf_key, routes) as port:
        url = f"https://127.0.0.1:{port}/mer.mp4"
        f = fetcher(tmp_path, apple=False, sources=[url])
        with pytest.raises(AerialError, match="1 film"):
            await f.fetch_missing()
    message = f.status()["sources"][0]["error"] or ""
    assert "Certificat de 127.0.0.1 refusé par le Pi" in message
    assert "unable to get local issuer certificate" in message
    assert (
        "sylvan.test ← Apple Test Root (Test Org)" in message
    )  # quelle autorité manque : de quoi corriger
    assert not [p for p in f.directory.iterdir() if p.is_file() and not p.name.startswith(".")]


async def test_the_apple_catalogue_gets_no_exception_to_the_certificate_check(
    tmp_path, pki, tls_server, monkeypatch
):
    with tls_server(pki.leaf_pem, pki.leaf_key) as port:
        monkeypatch.setattr(aerial, "BASE_URL", f"https://127.0.0.1:{port}/Aerials")
        f = fetcher(tmp_path)  # Apple + ffmpeg « présent »
        with pytest.raises(AerialError, match="10 film"):
            await f.fetch_missing()
    row = f.status()["sources"][0]
    assert row["done"] == 0
    assert "refusé par le Pi" in (row["error"] or "")


async def test_a_list_behind_an_unknown_authority_says_so_on_its_line(tmp_path, pki, tls_server):
    with tls_server(
        pki.leaf_pem,
        pki.leaf_key,
        {"/liste.txt": (200, {"Content-Type": "text/plain"}, b"https://x.org/a.mp4\n")},
    ) as port:
        f = fetcher(tmp_path, apple=False, sources=[f"https://127.0.0.1:{port}/liste.txt"])
        await f.fetch_missing()
    assert "refusé par le Pi" in (f.status()["sources"][0]["error"] or "")


async def test_a_known_authority_downloads_normally_and_a_wrong_name_does_not(
    tmp_path, pki, tls_server, monkeypatch, clips
):
    if not clips:
        pytest.skip("ffmpeg absent")
    monkeypatch.setenv("SSL_CERT_FILE", str(pki.ca_pem))
    routes = {"/mer.mp4": (200, {"Content-Type": "video/mp4"}, clips["small.mp4"])}
    with tls_server(pki.leaf_pem, pki.leaf_key, routes) as port:
        good = fetcher(
            tmp_path / "bon",
            apple=False,
            sources=[f"https://localhost:{port}/mer.mp4"],
            which=shutil.which,
        )
        await good.fetch_missing()
        assert (good.directory / "mer.mp4").read_bytes() == clips["small.mp4"]
    with tls_server(pki.wrong_pem, pki.wrong_key, routes) as port:
        bad = fetcher(
            tmp_path / "mauvais",
            apple=False,
            sources=[f"https://localhost:{port}/mer.mp4"],
            which=shutil.which,
        )
        with pytest.raises(AerialError, match="1 film"):
            await bad.fetch_missing()
    assert "refusé par le Pi" in (bad.status()["sources"][0]["error"] or "")
    assert not list(bad.directory.glob("*.mp4"))
