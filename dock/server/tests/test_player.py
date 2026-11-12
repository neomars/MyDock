"""Le lecteur du dock : ce que joue l'écran (Freetv, Kiosque), la télécommande, et l'assistant de lecture qui pilote VLC."""

from __future__ import annotations

import asyncio
import contextlib
import http.server
import json
import shutil
import stat
import textwrap
import threading
import types

import pytest

from mydock_dock import player as player_module
from mydock_dock.player import HelperConn, PlayerError, PlayerHub
from mydock_dock.testing import call, eventually, running_dock, snapshot_of

PLAYLIST = """#EXTM3U
#EXTINF:0,1 - TF1
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


class _Handler(http.server.BaseHTTPRequestHandler):
    routes: dict = {}

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


# --- le hub, seul ----------------------------------------------------------------------------------------------------


def hub():
    changes: list[int] = []
    hub = PlayerHub(lambda: changes.append(1))
    conn = HelperConn()
    hub.attach(conn)
    conn.info = {"vlc": True, "touch": True}
    return hub, conn, changes


def sent(conn: HelperConn) -> list[dict]:
    out = []
    while not conn.outbox.empty():
        out.append(conn.outbox.get_nowait())
    return out


def test_nothing_plays_at_first():
    snapshot = PlayerHub(lambda: None).snapshot()
    assert snapshot["state"] == "idle"
    assert snapshot["available"] is False
    assert snapshot["helper"] is None


def test_playing_needs_the_helper():
    with pytest.raises(PlayerError, match=r"assistant de lecture"):
        PlayerHub(lambda: None).play({"title": "x"}, "rtsp://h/x")


def test_playing_needs_vlc_and_a_way_back_to_the_dock():
    h, conn, _ = hub()
    conn.info = {"vlc": False, "touch": True}
    with pytest.raises(PlayerError, match=r"VLC est absent"):
        h.play({"title": "x"}, "rtsp://h/x")
    conn.info = {"vlc": True, "touch": False}
    with pytest.raises(PlayerError, match=r"swayidle"):
        h.play({"title": "x"}, "rtsp://h/x")
    assert h.state == "idle"


async def test_a_play_request_reaches_the_helper_and_the_screen_sees_it_start():
    h, conn, changes = hub()
    h.play({"kind": "tv", "title": "TF1", "subtitle": "1 · SD", "live": True}, "rtsp://h/tf1")
    (command,) = sent(conn)
    assert command == {
        "t": "cmd",
        "name": "play",
        "job": 1,
        "url": "rtsp://h/tf1",
        "live": True,
        "volume": 70,
    }
    snapshot = h.snapshot()
    assert (snapshot["state"], snapshot["title"], snapshot["live"], snapshot["front"]) == (
        "starting",
        "TF1",
        True,
        "video",
    )
    assert changes
    h.shutdown()


async def test_the_helper_reports_progress_and_the_end():
    h, conn, _ = hub()
    h.play({"kind": "file", "title": "Film", "live": False}, "smb://nas/x/film.mkv")
    h.on_message(
        conn,
        {
            "t": "state",
            "job": 1,
            "state": "playing",
            "position": 12.5,
            "length": 5400,
            "volume": 55,
            "front": "video",
        },
    )
    snapshot = h.snapshot()
    assert (snapshot["state"], snapshot["position"], snapshot["length"], snapshot["volume"]) == (
        "playing",
        12.5,
        5400.0,
        55,
    )
    h.on_message(conn, {"t": "ended", "job": 1})
    assert h.snapshot()["state"] == "idle"
    assert h.snapshot()["title"] is None
    assert h.front == "dock"


async def test_a_file_read_through_a_pipe_has_no_fast_forward_and_the_next_playback_starts_afresh():
    h, conn, _ = hub()
    assert h.snapshot()["seekable"] is True
    h.play({"kind": "file", "title": "Film", "live": False}, "smb://nas/x/film.mkv")
    h.on_message(
        conn,
        {
            "t": "state",
            "job": 1,
            "state": "playing",
            "position": 3,
            "length": 90,
            "seekable": False,
        },
    )
    assert h.snapshot()["seekable"] is False
    h.on_message(conn, {"t": "state", "job": 1, "state": "playing", "position": 4, "length": 90})
    assert h.snapshot()["seekable"] is True  # un rapport sans le mot : le cas général
    h.on_message(
        conn,
        {
            "t": "state",
            "job": 1,
            "state": "playing",
            "position": 5,
            "length": 90,
            "seekable": False,
        },
    )
    h.play({"kind": "file", "title": "Autre", "live": False}, "smb://nas/x/autre.mkv")
    assert h.snapshot()["seekable"] is True  # une nouvelle lecture repart de zéro


async def test_the_smb_source_is_passed_to_the_helper_but_never_to_the_screen():
    h, conn, _ = hub()
    media = {
        "kind": "file",
        "title": "F",
        "live": False,
        "smb": {"host": "nas", "share": "S", "path": "a/b.mkv"},
    }
    h.play(media, "smb://nas/S/a/b.mkv", secrets={"smb_user": "u", "smb_password": "p"})
    command = sent(conn)[-1]
    assert command["smb"] == {"host": "nas", "share": "S", "path": "a/b.mkv"}
    assert (command["smb_user"], command["smb_password"]) == ("u", "p")
    assert "smb" not in h.snapshot()
    h.play({"kind": "tv", "title": "TF1", "live": True}, "rtsp://box/x")
    assert "smb" not in sent(conn)[-1]  # une chaîne n'a pas de partage


async def test_an_error_from_the_helper_is_kept_for_the_screen():
    h, conn, _ = hub()
    h.play({"title": "x"}, "rtsp://h/x")
    h.on_message(conn, {"t": "ended", "job": 1, "error": "La chaîne ne répond pas."})
    snapshot = h.snapshot()
    assert (snapshot["state"], snapshot["error"]) == ("error", "La chaîne ne répond pas.")
    h.reset_error()
    assert h.snapshot()["state"] == "idle"


async def test_a_report_about_a_replaced_playback_is_ignored():
    h, conn, _ = hub()
    h.play({"title": "un"}, "rtsp://h/1")
    h.play({"title": "deux"}, "rtsp://h/2")
    h.on_message(conn, {"t": "ended", "job": 1, "error": "ancienne"})
    h.on_message(conn, {"t": "state", "job": 1, "state": "playing"})
    snapshot = h.snapshot()
    assert (snapshot["state"], snapshot["title"], snapshot["error"]) == ("starting", "deux", None)
    h.shutdown()


async def test_messages_from_an_old_helper_are_ignored():
    h, old, _ = hub()
    h.play({"title": "x"}, "rtsp://h/x")
    new = HelperConn()
    h.attach(new)  # le nouvel assistant ne connaît pas la lecture de l'ancien
    assert h.state == "error"
    h.on_message(old, {"t": "hello", "vlc": True})
    assert h.helper is new
    assert not new.info


@pytest.mark.parametrize("value", [True, "12", None, float("nan"), -1, 10**9])
async def test_absurd_numbers_from_the_helper_never_reach_the_screen(value):
    h, conn, _ = hub()
    h.play({"title": "x"}, "rtsp://h/x")
    h.on_message(
        conn,
        {
            "t": "state",
            "job": 1,
            "state": "playing",
            "position": value,
            "length": value,
            "volume": value,
        },
    )
    snapshot = h.snapshot()
    assert snapshot["position"] is None
    assert snapshot["length"] is None
    assert snapshot["volume"] == 70
    h.shutdown()


async def test_losing_the_helper_stops_the_playback_and_says_so():
    h, conn, _ = hub()
    h.play({"title": "x"}, "rtsp://h/x")
    h.detach(conn)
    snapshot = h.snapshot()
    assert (snapshot["state"], snapshot["available"]) == ("error", False)
    assert "interrompue" in snapshot["error"]


async def test_the_remote_control_is_validated_then_forwarded():
    h, conn, _ = hub()
    h.play({"title": "x"}, "rtsp://h/x")
    sent(conn)
    h.command("volume", value=35)
    h.command("seek", delta=-30)
    h.command("toggle")
    h.command("front", to="dock")
    assert [
        (c["name"], {k: v for k, v in c.items() if k not in ("t", "name", "job")})
        for c in sent(conn)
    ] == [
        ("volume", {"value": 35}),
        ("seek", {"delta": -30.0}),
        ("toggle", {}),
        ("front", {"to": "dock"}),
    ]
    assert h.volume == 35
    for bad in (
        ("volume", {"value": 101}),
        ("volume", {"value": True}),
        ("seek", {"delta": "x"}),
        ("front", {"to": "ailleurs"}),
        ("rm", {}),
    ):
        with pytest.raises(PlayerError):
            h.command(bad[0], **bad[1])
    h.shutdown()


async def test_stop_returns_to_idle_at_once():
    h, conn, _ = hub()
    h.play({"title": "x"}, "rtsp://h/x")
    h.command("stop")
    assert h.snapshot()["state"] == "idle"
    assert h.front == "dock"
    assert sent(conn)[-1]["name"] == "stop"


async def test_a_start_that_never_comes_is_declared_failed(monkeypatch):
    monkeypatch.setattr(player_module, "START_TIMEOUT", 0.05)
    h, conn, _ = hub()
    h.play({"title": "x"}, "rtsp://h/x")
    await asyncio.sleep(0.3)
    assert h.state == "error"
    assert "n'a pas démarré" in h.error
    assert sent(conn)[-1]["name"] == "stop"


async def test_the_dock_writes_down_why_it_stopped_a_playback_that_never_reported_playing(
    monkeypatch, caplog
):
    """Sans cette ligne dans dock.log, un arrêt à 40 s ressemblait à un plantage de VLC (constaté sur le Pi)."""
    monkeypatch.setattr(player_module, "START_TIMEOUT", 0.05)
    h, _conn, _ = hub()
    with caplog.at_level("WARNING", logger="mydock_dock.media"):
        h.play({"title": "x"}, "rtsp://h/x")
        await asyncio.sleep(0.3)
    assert any(
        "toujours « en démarrage »" in r.getMessage() and r.name == "mydock_dock.media"
        for r in caplog.records
    )


async def test_a_playback_that_starts_in_time_is_not_declared_failed(monkeypatch):
    monkeypatch.setattr(player_module, "START_TIMEOUT", 0.2)
    h, conn, _ = hub()
    h.play({"title": "x"}, "rtsp://h/x")
    h.on_message(conn, {"t": "state", "job": 1, "state": "playing"})
    await asyncio.sleep(0.4)
    assert h.state == "playing"
    h.shutdown()


# --- dans le dock, avec un assistant simulé ---------------------------------------------------------------------------


@contextlib.asynccontextmanager
async def helper(dock, **hello):
    """L'assistant de lecture de l'écran, simulé : il dit bonjour puis on lit ce que le dock lui commande."""
    async with dock.connect_player() as ws:
        await ws.send(
            json.dumps(
                {
                    "t": "hello",
                    "v": 1,
                    "vlc": True,
                    "live555": False,
                    "ffmpeg": True,
                    "touch": True,
                    "demo": False,
                    **hello,
                }
            )
        )
        await eventually(
            lambda: dock.server.player.helper is not None and bool(dock.server.player.helper.info)
        )
        yield ws


async def command_of(ws, timeout=3.0):
    return json.loads(await asyncio.wait_for(ws.recv(), timeout))


@pytest.fixture
def freebox_web():
    """Un faux serveur de la Freebox (la liste des chaînes), sur 127.0.0.1."""
    routes: dict = {"/freeboxtv/playlist.m3u": (200, {}, PLAYLIST.encode())}
    server = http.server.ThreadingHTTPServer(
        ("127.0.0.1", 0), type("H", (_Handler,), {"routes": routes})
    )
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield types.SimpleNamespace(
        routes=routes, url=f"http://127.0.0.1:{server.server_port}/freeboxtv/playlist.m3u"
    )
    server.shutdown()
    server.server_close()


@pytest.fixture
async def dock(tmp_path, freebox_web):
    async with running_dock(tmp_path) as running:
        running.config.set_media(playlist_url=freebox_web.url, smb_host="nas.local")
        yield running


async def test_a_browser_is_never_taken_for_the_helper(dock):
    with pytest.raises(Exception, match=r"403|1008|rejected|denied|HTTP"):
        async with dock.connect_player(origin=dock.origin):
            pass


async def test_the_screen_learns_when_the_helper_is_there(dock):
    async with dock.connect() as browser:
        assert (await snapshot_of(browser, "player"))["data"]["available"] is False
        async with helper(dock):
            seen = {
                "data": await asyncio.wait_for(
                    _until(browser, lambda d: d["helper"] is not None), 3
                )
            }
            assert seen["data"]["available"] is True
            assert seen["data"]["helper"] == {
                "vlc": True,
                "live555": False,
                "ffmpeg": True,
                "touch": True,
                "demo": False,
            }
        await eventually(lambda: dock.server.player.helper is None)


async def test_the_channels_come_without_their_stream_addresses(dock):
    async with dock.connect() as browser:
        ack = await call(browser, "l", "dock.tv.list")
    assert ack["ok"], ack
    assert [c["name"] for c in ack["r"]["channels"]] == ["TF1", "France 2", "Radio Classique"]
    assert "rtsp" not in json.dumps(ack["r"])


async def test_a_channel_starts_on_the_helper_in_the_chosen_quality(dock):
    async with dock.connect() as browser, helper(dock) as ws:
        channels = (await call(browser, "l", "dock.tv.list"))["r"]["channels"]
        tf1 = channels[0]["id"]
        ack = await call(browser, "p", "dock.player.play", {"kind": "tv", "id": tf1})
        assert ack["ok"], ack
        command = await command_of(ws)
        assert command["name"] == "play"
        assert command["url"].endswith("service=201&flavour=sd")
        assert command["live"] is True
        hd = await call(browser, "q", "dock.player.play", {"kind": "tv", "id": tf1, "hd": True})
        assert hd["ok"]
        assert (await command_of(ws))["url"].endswith("flavour=hd")
        assert (await call(browser, "r", "dock.ui.set", {"tv_hd": True}))["ok"]
        await call(browser, "s", "dock.player.play", {"kind": "tv", "id": tf1})
        assert (await command_of(ws))["url"].endswith("flavour=hd")  # le réglage « HD » du dock


async def test_playing_and_stopping_are_seen_on_the_screen_and_keep_it_awake(dock):
    import httpx

    async with dock.connect() as browser, helper(dock) as ws:
        tf1 = (await call(browser, "l", "dock.tv.list"))["r"]["channels"][0]["id"]
        await call(browser, "p", "dock.player.play", {"kind": "tv", "id": tf1})
        job = (await command_of(ws))["job"]
        await ws.send(
            json.dumps(
                {
                    "t": "state",
                    "job": job,
                    "state": "playing",
                    "position": None,
                    "length": None,
                    "volume": 70,
                    "front": "video",
                }
            )
        )
        seen = await asyncio.wait_for(_until(browser, lambda d: d["state"] == "playing"), 3)
        assert (seen["title"], seen["subtitle"], seen["live"]) == ("TF1", "1 · SD", True)
        await call(browser, "u", "dock.ui.set", {"screen_sleep": 300})
        async with httpx.AsyncClient() as http:
            assert (await http.get(dock.origin + "/api/screen")).json() == {
                "sleep_after": 0
            }  # on regarde : l'écran reste allumé
        await ws.send(json.dumps({"t": "ended", "job": job}))
        await asyncio.wait_for(_until(browser, lambda d: d["state"] == "idle"), 3)
        async with httpx.AsyncClient() as http:
            assert (await http.get(dock.origin + "/api/screen")).json() == {"sleep_after": 300}


async def _until(browser, predicate):
    while True:
        message = json.loads(await browser.recv())
        if (
            message.get("t") == "snap"
            and message.get("topic") == "player"
            and predicate(message["data"])
        ):
            return message["data"]


async def test_the_remote_control_reaches_the_helper(dock):
    async with dock.connect() as browser, helper(dock) as ws:
        tf1 = (await call(browser, "l", "dock.tv.list"))["r"]["channels"][0]["id"]
        await call(browser, "p", "dock.player.play", {"kind": "tv", "id": tf1})
        await command_of(ws)
        for act, params, expected in [
            ("dock.player.pause", {}, {"name": "toggle"}),
            ("dock.player.seek", {"delta": 30}, {"name": "seek", "delta": 30.0}),
            ("dock.player.volume", {"value": 40}, {"name": "volume", "value": 40}),
            ("dock.player.front", {"to": "dock"}, {"name": "front", "to": "dock"}),
            ("dock.player.stop", {}, {"name": "stop"}),
        ]:
            assert (await call(browser, act, act, params))["ok"], act
            command = await command_of(ws)
            assert {k: command[k] for k in expected} == expected


@pytest.mark.parametrize(
    ("act", "params"),
    [
        ("dock.player.volume", {"value": 101}),
        ("dock.player.volume", {"value": -1}),
        ("dock.player.volume", {"value": True}),
        ("dock.player.volume", {"value": "fort"}),
        ("dock.player.seek", {"delta": 10**9}),
        ("dock.player.seek", {"delta": True}),
        ("dock.player.front", {"to": "ailleurs"}),
        ("dock.player.play", {"kind": "radio"}),
        (
            "dock.player.play",
            {"kind": "tv", "url": "rtsp://pirate/x"},
        ),  # jamais une adresse venue de l'écran
        ("dock.player.play", {"kind": "tv", "id": "x" * 40}),
    ],
)
async def test_the_remote_control_refuses_what_makes_no_sense(dock, act, params):
    async with dock.connect() as browser, helper(dock):
        assert (await call(browser, "x", act, params))["e"]["code"] == "bad_params"


async def test_without_the_helper_playing_says_what_is_missing(dock):
    async with dock.connect() as browser:
        tf1 = (await call(browser, "l", "dock.tv.list"))["r"]["channels"][0]["id"]
        ack = await call(browser, "p", "dock.player.play", {"kind": "tv", "id": tf1})
    assert ack["e"]["code"] == "unavailable"
    assert "assistant de lecture" in ack["e"]["msg"]


async def test_a_channel_that_left_the_list_is_said(dock):
    async with dock.connect() as browser, helper(dock):
        ack = await call(browser, "p", "dock.player.play", {"kind": "tv", "id": "zzzzzzzzzz"})
    assert ack["e"]["code"] == "unavailable"
    assert "plus dans la liste" in ack["e"]["msg"]


async def test_a_file_starts_with_its_share_address_and_the_identifier_only_when_one_is_set(dock):
    async with dock.connect() as browser, helper(dock) as ws:
        params = {"kind": "file", "share": "Disque dur", "path": "Films/Un film d'été (2020).mkv"}
        ack = await call(browser, "p", "dock.player.play", params)
        assert ack["ok"], ack
        command = await command_of(ws)
        assert (
            command["url"]
            == "smb://nas.local/Disque%20dur/Films/Un%20film%20d%27%C3%A9t%C3%A9%20%282020%29.mkv"
        )
        assert command["live"] is False
        # De quoi lire le fichier par smbclient si VLC n'y arrive pas : l'hôte, le partage et le chemin, jamais de secret.
        assert command["smb"] == {
            "host": "nas.local",
            "share": "Disque dur",
            "path": "Films/Un film d'été (2020).mkv",
        }
        assert "smb_user" not in command  # accès libre : rien de secret n'est envoyé
        assert (
            await call(
                browser,
                "m",
                "dock.media.set",
                {"smb_user": "freebox", "smb_password": "motdepasse"},
            )
        )["ok"]
        await call(browser, "q", "dock.player.play", params)
        command = await command_of(ws)
        assert (command["smb_user"], command["smb_password"]) == ("freebox", "motdepasse")
        assert command["smb"]["share"] == "Disque dur"
        snapshot = (await snapshot_of(browser, "player"))["data"]
        assert snapshot["title"] == "Un film d'été (2020)"
        assert snapshot["subtitle"] == "Disque dur/Films"
        assert "motdepasse" not in json.dumps(snapshot)
        assert "smb" not in snapshot  # l'écran ne reçoit ni adresse ni chemin construit


@pytest.mark.parametrize(
    "params",
    [
        {"kind": "file", "share": "Disque dur"},
        {"kind": "file", "path": "Films/a.mkv"},
        {"kind": "file", "share": "Disque dur", "path": "Films/notes.txt"},
        {"kind": "file", "share": "Disque dur", "path": "../../etc/passwd.mkv"},
        {"kind": "file", "share": "../x", "path": "a.mkv"},
        {"kind": "file", "share": "Disque dur", "path": "Films/"},
    ],
)
async def test_only_a_video_inside_a_share_can_be_played(dock, params):
    async with dock.connect() as browser, helper(dock) as ws:
        ack = await call(browser, "p", "dock.player.play", params)
        assert not ack["ok"]
        assert ack["e"]["code"] == "unavailable"
        with pytest.raises(TimeoutError):
            await command_of(ws, timeout=0.3)  # rien n'est parti vers VLC


async def test_losing_the_helper_while_playing_is_said(dock):
    async with dock.connect() as browser:
        async with helper(dock) as ws:
            tf1 = (await call(browser, "l", "dock.tv.list"))["r"]["channels"][0]["id"]
            await call(browser, "p", "dock.player.play", {"kind": "tv", "id": tf1})
            await command_of(ws)
        data = await asyncio.wait_for(_until(browser, lambda d: d["state"] == "error"), 3)
    assert data["available"] is False
    assert "interrompue" in data["error"]


# --- les réglages du Divertissement -----------------------------------------------------------------------------------


async def test_the_password_is_kept_in_the_private_file_and_never_shown(dock):
    async with dock.connect() as browser:
        ack = await call(
            browser,
            "m",
            "dock.media.set",
            {"smb_host": "freebox.lan", "smb_user": "moi", "smb_password": "très secret"},
        )
        assert ack["ok"], ack
        assert ack["r"] == {
            "playlist_url": dock.config.playlist_url,
            "smb_host": "freebox.lan",
            "smb_user": "moi",
            "smb_password_set": True,
            "smbclient": shutil.which("smbclient") is not None,
        }
        topic = (await snapshot_of(browser, "dock"))["data"]["media"]
        assert "très secret" not in json.dumps(topic)
    saved = dock.config.path.read_text()
    assert "très secret" in saved
    assert stat.S_IMODE(dock.config.path.stat().st_mode) == 0o600


@pytest.mark.parametrize(
    "params",
    [
        {"playlist_url": "ftp://x/y"},
        {"playlist_url": "http://user:pw@h/x"},
        {"smb_host": "http://nas"},
        {"smb_user": "a%b"},
        {"smb_password": "x" * 200},
        {"playlist_url": 12},
    ],
)
async def test_invalid_settings_change_nothing(dock, params):
    before = dock.config.path.read_text()
    async with dock.connect() as browser:
        assert (await call(browser, "m", "dock.media.set", params))["e"]["code"] == "bad_params"
    assert dock.config.path.read_text() == before


async def test_the_connection_test_says_which_link_is_broken(dock):
    async with dock.connect() as browser:
        no_helper = (await call(browser, "t", "dock.media.test"))["r"]
        assert no_helper["tv"]["ok"] is True
        assert "3 chaînes" in no_helper["tv"]["text"]
        assert no_helper["player"]["ok"] is False
        assert "assistant de lecture" in no_helper["player"]["text"]
        async with helper(dock, vlc=False):
            assert (
                "VLC est absent"
                in (await call(browser, "t2", "dock.media.test"))["r"]["player"]["text"]
            )
        async with helper(dock, touch=False):
            assert (
                "swayidle" in (await call(browser, "t3", "dock.media.test"))["r"]["player"]["text"]
            )
        async with helper(dock):
            assert (await call(browser, "t4", "dock.media.test"))["r"]["player"] == {
                "ok": True,
                "text": "VLC est prêt.",
            }


async def test_an_unreachable_freebox_is_said_for_the_channels(dock, freebox_web):
    freebox_web.routes.clear()
    async with dock.connect() as browser:
        ack = await call(browser, "l", "dock.tv.list")
    assert ack["e"]["code"] == "unavailable"
    assert "404" in ack["e"]["msg"]


@pytest.fixture
def fake_smbclient(tmp_path, monkeypatch):
    script = tmp_path / "smbclient"
    script.write_text(
        textwrap.dedent(
            """\
            #!/bin/sh
            case "$*" in
              *-L*) printf 'Disk|Disque dur|\\nDisk|Enregistrements|\\nIPC|IPC$|\\n' ;;
              *) printf '  Dessins                             D        0  Wed Nov 11 11:46:01 2026\\n  film.mkv                            N      123  Wed Nov 11 11:46:01 2026\\n  notes.txt                           N        6  Wed Nov 11 11:46:01 2026\\n' ;;
            esac
            """
        )
    )
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    real = shutil.which
    monkeypatch.setattr(
        shutil,
        "which",
        lambda name, *a, **k: str(script) if name == "smbclient" else real(name, *a, **k),
    )


async def test_the_shares_and_folders_reach_the_screen(dock, fake_smbclient):
    async with dock.connect() as browser:
        shares = (await call(browser, "s", "dock.media.shares"))["r"]
        assert shares == {"host": "nas.local", "shares": ["Disque dur", "Enregistrements"]}
        listing = (
            await call(browser, "b", "dock.media.browse", {"share": "Disque dur", "path": "Films"})
        )["r"]
    assert listing == {
        "share": "Disque dur",
        "path": "Films",
        "entries": [
            {"name": "Dessins", "dir": True, "size": 0},
            {"name": "film.mkv", "dir": False, "size": 123},
        ],
    }


@pytest.mark.parametrize(
    "params",
    [
        {"share": "a/b"},
        {"share": "x", "path": "../.."},
        {"share": ""},
        {"share": "x", "path": "a\x00b"},
    ],
)
async def test_a_browse_request_that_climbs_is_refused_before_smbclient_runs(
    dock, fake_smbclient, params
):
    async with dock.connect() as browser:
        ack = await call(browser, "b", "dock.media.browse", params)
    assert ack["e"]["code"] in ("unavailable", "bad_params")
    assert "invalide" in ack["e"]["msg"] or ack["e"]["code"] == "bad_params"
