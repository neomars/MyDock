"""Le lecteur du dock : ce que joue l'écran (Kiosque, Retrogame), la télécommande, et l'assistant de lecture qui pilote VLC."""

from __future__ import annotations

import asyncio
import contextlib
import json
import shutil
import stat
import textwrap

import pytest

from mydock_dock import player as player_module
from mydock_dock.player import HelperConn, PlayerError, PlayerHub
from mydock_dock.testing import call, eventually, running_dock, snapshot_of

#: Un film du Kiosque : de quoi lancer une lecture sans rien lister.
FILM = {"kind": "file", "share": "Disque dur", "path": "Films/Un film.mkv"}


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
    h.play(
        {"kind": "file", "title": "Film", "subtitle": "Disque dur/Films"}, "smb://nas/x/film.mkv"
    )
    (command,) = sent(conn)
    assert command == {
        "t": "cmd",
        "name": "play",
        "job": 1,
        "url": "smb://nas/x/film.mkv",
        "volume": 70,
    }
    snapshot = h.snapshot()
    assert (snapshot["state"], snapshot["title"], snapshot["front"]) == (
        "starting",
        "Film",
        "video",
    )
    assert changes
    h.shutdown()


async def test_the_helper_reports_progress_and_the_end():
    h, conn, _ = hub()
    h.play({"kind": "file", "title": "Film"}, "smb://nas/x/film.mkv")
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
    h.play({"kind": "file", "title": "Film"}, "smb://nas/x/film.mkv")
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
    h.play({"kind": "file", "title": "Autre"}, "smb://nas/x/autre.mkv")
    assert h.snapshot()["seekable"] is True  # une nouvelle lecture repart de zéro


async def test_the_smb_source_is_passed_to_the_helper_but_never_to_the_screen():
    h, conn, _ = hub()
    media = {
        "kind": "file",
        "title": "F",
        "smb": {"host": "nas", "share": "S", "path": "a/b.mkv"},
    }
    h.play(media, "smb://nas/S/a/b.mkv", secrets={"smb_user": "u", "smb_password": "p"})
    command = sent(conn)[-1]
    assert command["smb"] == {"host": "nas", "share": "S", "path": "a/b.mkv"}
    assert (command["smb_user"], command["smb_password"]) == ("u", "p")
    assert "smb" not in h.snapshot()
    h.play({"kind": "file", "title": "Autre"}, "http://box/x.mkv")
    assert "smb" not in sent(conn)[-1]  # un fichier hors partage n'a pas de partage


async def test_an_error_from_the_helper_is_kept_for_the_screen():
    h, conn, _ = hub()
    h.play({"title": "x"}, "rtsp://h/x")
    h.on_message(conn, {"t": "ended", "job": 1, "error": "Le fichier ne répond pas."})
    snapshot = h.snapshot()
    assert (snapshot["state"], snapshot["error"]) == ("error", "Le fichier ne répond pas.")
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
    """La prochaine commande que le dock envoie à l'assistant (hors « configure », envoyé à chaque connexion)."""
    while True:
        message = json.loads(await asyncio.wait_for(ws.recv(), timeout))
        if message.get("name") != "configure":
            return message


async def test_the_helper_is_told_the_button_pin_when_it_connects_and_when_it_changes(dock):
    async with helper(dock) as ws:
        first = json.loads(await asyncio.wait_for(ws.recv(), 3))
        assert first == {
            "t": "cmd",
            "name": "configure",
            "button_gpio": 17,
            "pc_audio": True,
            "audio_advance_ms": 150,
            "freetv_user_agent": "auto",
            "freetv_scale": 0.7,
            "freetv_corner": "top-left",
            "freetv_fullscreen": True,
        }
        async with dock.connect() as browser:
            await call(browser, "a1", "dock.retro.set", {"button_gpio": 22})
        again = json.loads(await asyncio.wait_for(ws.recv(), 3))
        assert again == {
            "t": "cmd",
            "name": "configure",
            "button_gpio": 22,
            "pc_audio": True,
            "audio_advance_ms": 150,
            "freetv_user_agent": "auto",
            "freetv_scale": 0.7,
            "freetv_corner": "top-left",
            "freetv_fullscreen": True,
        }


@pytest.fixture
async def dock(tmp_path):
    async with running_dock(tmp_path) as running:
        running.config.set_media(smb_host="nas.local")
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
                "touch": True,
                "demo": False,
                "mame": False,
                "chromium": False,
                "widevine": False,
                "keyboard": False,
                "gpio": None,
            }
        await eventually(lambda: dock.server.player.helper is None)


async def test_playing_and_stopping_are_seen_on_the_screen_and_keep_it_awake(dock):
    import httpx

    async with dock.connect() as browser, helper(dock) as ws:
        await call(browser, "p", "dock.player.play", FILM)
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
        assert (seen["title"], seen["subtitle"]) == ("Un film", "Disque dur/Films")
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
        await call(browser, "p", "dock.player.play", FILM)
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
            {"kind": "file", "url": "rtsp://pirate/x"},
        ),  # jamais une adresse venue de l'écran
        (
            "dock.player.play",
            {"kind": "tv", "id": "tf1"},
        ),  # Freetv n'existe plus : Free TV, lui, a sa propre action
        ("dock.player.play", {"kind": "game", "id": "x" * 40}),
    ],
)
async def test_the_remote_control_refuses_what_makes_no_sense(dock, act, params):
    async with dock.connect() as browser, helper(dock):
        assert (await call(browser, "x", act, params))["e"]["code"] == "bad_params"


async def test_without_the_helper_playing_says_what_is_missing(dock):
    async with dock.connect() as browser:
        ack = await call(browser, "p", "dock.player.play", FILM)
    assert ack["e"]["code"] == "unavailable"
    assert "assistant de lecture" in ack["e"]["msg"]


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
            await call(browser, "p", "dock.player.play", FILM)
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
            "smb_host": "freebox.lan",
            "smb_user": "moi",
            "smb_password_set": True,
            "smbclient": shutil.which("smbclient") is not None,
            "pc_audio": True,
            "audio_advance_ms": 150,
            "freetv_corner": "top-left",
        }
        topic = (await snapshot_of(browser, "dock"))["data"]["media"]
        assert "très secret" not in json.dumps(topic)
    saved = dock.config.path.read_text()
    assert "très secret" in saved
    assert stat.S_IMODE(dock.config.path.stat().st_mode) == 0o600


@pytest.mark.parametrize(
    "params",
    [
        {
            "playlist_url": "http://mafreebox.freebox.fr/freeboxtv/playlist.m3u"
        },  # Freetv n'existe plus
        {"smb_host": "http://nas"},
        {"smb_user": "a%b"},
        {"smb_password": "x" * 200},
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
        assert "tv" not in no_helper
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


# --- les jeux (MAME) : le même écran, un seul occupant ---------------------------------------------------------------


@pytest.fixture
def arcade(dock):
    """Une liste de jeux sans MAME : le dock les croit validés."""
    from mydock_dock.retrogame import Game

    games = [Game("pong", "Pong", "1972", "Atari", "good")]
    dock.server.retro.games = lambda: games
    dock.server.retro._mame = lambda: "/usr/games/mame"
    return dock


async def test_a_game_is_launched_by_name_and_the_screen_learns_what_runs(arcade):
    dock = arcade
    async with dock.connect() as browser, helper(dock, mame=True) as ws:
        ack = await call(browser, "g", "dock.player.play", {"kind": "game", "id": "pong"})
        assert ack["ok"], ack
        assert ack["r"] == {"started": "Pong"}
        command = await command_of(ws)
        assert command["name"] == "play"
        assert command["engine"] == "mame"
        assert command["game"] == "pong"
        assert command["title"] == "Pong"
        assert command["rompath"] == str(dock.config.roms_path)
        assert "url" in command
        assert not {"smb_user", "smb_password"} & command.keys()
        snap = dock.server.player.snapshot()
        assert snap["engine"] == "mame"
        assert snap["game"] == "pong"
        assert snap["state"] == "starting"
        assert snap["title"] == "Pong"
        assert snap["subtitle"] == "1972 · Atari"


async def test_a_game_that_is_not_in_the_list_is_never_launched(arcade):
    dock = arcade
    async with dock.connect() as browser, helper(dock, mame=True) as ws:
        for name in ("inconnu", "../../bin/sh", "PONG", "pong;rm"):
            ack = await call(browser, "g", "dock.player.play", {"kind": "game", "id": name})
            assert not ack["ok"], name
        with pytest.raises(asyncio.TimeoutError):  # rien n'a été envoyé à l'assistant
            await command_of(ws, timeout=0.3)


async def test_a_game_needs_mame_on_the_pi(arcade):
    dock = arcade
    async with dock.connect() as browser, helper(dock, mame=False):
        ack = await call(browser, "g", "dock.player.play", {"kind": "game", "id": "pong"})
    assert not ack["ok"]
    assert "MAME est absent du Pi" in ack["e"]["msg"]
    assert "sudo apt install mame" in ack["e"]["msg"]


async def test_a_video_does_not_need_the_touch_tools_to_play_a_game(arcade):
    """Le jeu se quitte aussi au bouton et à la manette : swayidle n'est pas exigé, comme il l'est pour une vidéo."""
    dock = arcade
    async with dock.connect() as browser, helper(dock, mame=True, touch=False) as ws:
        ack = await call(browser, "g", "dock.player.play", {"kind": "game", "id": "pong"})
        assert ack["ok"], ack
        assert (await command_of(ws))["engine"] == "mame"


async def test_a_game_plays_then_ends_and_the_dock_goes_back_to_idle(arcade):
    dock = arcade
    async with dock.connect() as browser, helper(dock, mame=True) as ws:
        await call(browser, "g", "dock.player.play", {"kind": "game", "id": "pong"})
        job = (await command_of(ws))["job"]
        await ws.send(json.dumps({"t": "state", "job": job, "state": "playing", "front": "video"}))
        await eventually(lambda: dock.server.player.snapshot()["state"] == "playing")
        await ws.send(json.dumps({"t": "ended", "job": job, "error": None}))
        await eventually(lambda: dock.server.player.snapshot()["state"] == "idle")
        snap = dock.server.player.snapshot()
        assert snap["engine"] == "vlc"
        assert snap["game"] is None
        assert snap["error"] is None


async def test_a_game_that_cannot_start_leaves_an_error_for_the_screen(arcade):
    dock = arcade
    async with dock.connect() as browser, helper(dock, mame=True) as ws:
        await call(browser, "g", "dock.player.play", {"kind": "game", "id": "pong"})
        job = (await command_of(ws))["job"]
        message = "Le jeu « Pong » est incomplet : MAME ne trouve pas toutes ses ROMs."
        await ws.send(json.dumps({"t": "ended", "job": job, "error": message}))
        await eventually(lambda: dock.server.player.snapshot()["state"] == "error")
        snap = dock.server.player.snapshot()
        assert snap["error"] == message
        assert (
            snap["engine"] == "mame"
        )  # l'écran sait que c'est un jeu : il revient à la liste des jeux


async def test_the_button_is_counted_so_the_screen_can_wake_up(arcade):
    dock = arcade
    async with helper(dock, mame=True) as ws:
        before = dock.server.player.snapshot()["button"]
        await ws.send(json.dumps({"t": "button"}))
        await eventually(lambda: dock.server.player.snapshot()["button"] == before + 1)
        await ws.send(json.dumps({"t": "button"}))
        await eventually(lambda: dock.server.player.snapshot()["button"] == before + 2)


async def test_the_helper_describes_its_button_and_mame(dock):
    async with helper(dock, mame=True, gpio="GPIO17 (broche 11) : prêt."):
        info = dock.server.player.snapshot()["helper"]
        assert info["mame"] is True
        assert info["gpio"] == "GPIO17 (broche 11) : prêt."
    async with helper(dock, mame="oui", gpio="x" * 500):
        assert (
            len(dock.server.player.snapshot()["helper"]["gpio"]) == 120
        )  # borné : l'assistant n'est pas cru sur parole


async def test_a_game_stuck_before_its_window_is_stopped_by_the_watchdog(arcade, monkeypatch):
    monkeypatch.setattr(player_module, "START_TIMEOUT", 0.3)
    dock = arcade
    async with dock.connect() as browser, helper(dock, mame=True) as ws:
        await call(browser, "g", "dock.player.play", {"kind": "game", "id": "pong"})
        await command_of(ws)
        await eventually(lambda: dock.server.player.snapshot()["state"] == "error", timeout=3)
        assert "Le jeu n'a pas démarré" in dock.server.player.snapshot()["error"]
        assert (await command_of(ws))["name"] == "stop"  # l'assistant est prié d'arrêter MAME


# --- Free TV : le troisième occupant de l'écran --------------------------------------------------------------------------


FREETV = {"kind": "freetv", "title": "Free TV", "subtitle": "tv.free.fr"}


def freetv_hub(**info):
    h, conn, changes = hub()
    conn.info = {
        "vlc": True,
        "touch": True,
        "chromium": True,
        "widevine": True,
        "keyboard": True,
        **info,
    }
    return h, conn, changes


async def test_free_tv_is_opened_on_the_helper_without_any_address_and_the_screen_sees_it():
    h, conn, _ = freetv_hub()
    h.play(FREETV, "", extra={"engine": "freetv"})
    (command,) = sent(conn)
    assert (command["name"], command["engine"], command["url"]) == ("play", "freetv", "")
    snapshot = h.snapshot()
    assert (snapshot["state"], snapshot["kind"], snapshot["engine"], snapshot["front"]) == (
        "starting",
        "freetv",
        "freetv",
        "video",
    )
    assert snapshot["title"] == "Free TV"
    assert snapshot["keyboard"] is False
    h.shutdown()


async def test_free_tv_needs_chromium_and_widevine_and_says_what_to_install():
    h, conn, _ = freetv_hub(chromium=False)
    with pytest.raises(PlayerError, match=r"Chromium est absent.*sudo apt install chromium"):
        h.play(FREETV, "", extra={"engine": "freetv"})
    conn.info = {"vlc": True, "touch": True, "chromium": True, "widevine": False}
    with pytest.raises(PlayerError, match=r"Widevine est absent.*sudo apt install libwidevinecdm0"):
        h.play(FREETV, "", extra={"engine": "freetv"})
    assert h.state == "idle"
    assert sent(conn) == []


async def test_free_tv_does_not_need_vlc_nor_the_touch_tools():
    """Pas de swayidle : un toucher ne ramène pas le dock pendant Free TV (le retour se fait par le bouton, la manette, le coin)."""
    h, _conn, _ = freetv_hub(vlc=False, touch=False)
    h.play(FREETV, "", extra={"engine": "freetv"})
    assert h.state == "starting"
    h.shutdown()


async def test_the_screen_stays_awake_while_free_tv_plays_like_for_a_video():
    h, conn, _ = freetv_hub()
    h.play(FREETV, "", extra={"engine": "freetv"})
    assert not h.playing
    h.on_message(
        conn, {"t": "state", "job": 1, "state": "playing", "front": "video", "keyboard": False}
    )
    assert h.playing  # /api/screen dit alors « pas de veille » (sleep_after = 0)
    h.shutdown()


async def test_the_keyboard_state_comes_from_the_helper_and_resets_at_the_end():
    h, conn, _ = freetv_hub()
    h.play(FREETV, "", extra={"engine": "freetv"})
    h.on_message(
        conn, {"t": "state", "job": 1, "state": "playing", "front": "video", "keyboard": True}
    )
    assert h.snapshot()["keyboard"] is True
    h.on_message(
        conn, {"t": "state", "job": 1, "state": "playing", "front": "video", "keyboard": "oui"}
    )
    assert h.snapshot()["keyboard"] is True  # une valeur qui n'en est pas une est ignorée
    h.on_message(conn, {"t": "ended", "job": 1})
    assert h.snapshot()["keyboard"] is False
    h.shutdown()


async def test_the_keyboard_command_is_checked_and_only_for_free_tv():
    h, conn, _ = freetv_hub()
    with pytest.raises(PlayerError, match=r"ne sert que pendant Free TV"):
        h.command("keyboard", on=True)
    h.play({"kind": "file", "title": "Film"}, "smb://nas/x.mkv")
    with pytest.raises(PlayerError, match=r"ne sert que pendant Free TV"):
        h.command("keyboard", on=True)
    h.play(FREETV, "", extra={"engine": "freetv"})
    sent(conn)
    with pytest.raises(PlayerError, match=r"invalide"):
        h.command("keyboard", on="oui")
    h.command("keyboard", on=True)
    assert sent(conn)[-1] == {"t": "cmd", "name": "keyboard", "job": 2, "on": True}
    conn.info = {**conn.info, "keyboard": False}
    with pytest.raises(PlayerError, match=r"sudo apt install wvkbd"):
        h.command("keyboard", on=True)
    h.command("keyboard", on=False)  # cacher n'a jamais besoin du clavier
    h.shutdown()


async def test_a_free_tv_that_does_not_open_is_said_after_the_watchdog(monkeypatch):
    monkeypatch.setattr(player_module, "START_TIMEOUT", 0.1)
    h, conn, _ = freetv_hub()
    h.play(FREETV, "", extra={"engine": "freetv"})
    await asyncio.sleep(0.4)
    assert h.state == "error"
    assert "Free TV ne s'est pas ouvert" in h.error
    assert "player.log" in h.error
    assert sent(conn)[-1]["name"] == "stop"
    h.shutdown()


async def test_the_hub_reports_what_the_helper_has_for_free_tv():
    h, conn, _ = hub()
    h.on_message(
        conn,
        {
            "t": "hello",
            "vlc": True,
            "touch": True,
            "chromium": True,
            "widevine": False,
            "keyboard": True,
            "gpio": None,
        },
    )
    helper = h.snapshot()["helper"]
    assert (helper["chromium"], helper["widevine"], helper["keyboard"]) == (True, False, True)
    h.on_message(conn, {"t": "hello", "vlc": True, "touch": True})  # un assistant plus ancien
    helper = h.snapshot()["helper"]
    assert (helper["chromium"], helper["widevine"], helper["keyboard"]) == (False, False, False)


# --- dans le dock : l'écran ouvre Free TV, jamais une adresse -------------------------------------------------------------


async def test_the_screen_opens_free_tv_and_the_helper_gets_nothing_but_the_name_of_the_engine(
    dock,
):
    async with (
        dock.connect() as browser,
        helper(dock, chromium=True, widevine=True, keyboard=True) as ws,
    ):
        ack = await call(browser, "f", "dock.player.play", {"kind": "freetv"})
        assert ack["ok"], ack
        assert ack["r"] == {"started": "Free TV"}
        command = await command_of(ws)
        assert (command["name"], command["engine"], command["url"]) == ("play", "freetv", "")
        assert not {"smb", "smb_user", "smb_password", "game", "rompath"} & command.keys()
        snap = (await snapshot_of(browser, "player"))["data"]
        assert (snap["engine"], snap["kind"], snap["state"]) == ("freetv", "freetv", "starting")


@pytest.mark.parametrize(
    "params",
    [
        {"kind": "freetv", "url": "https://pirate.example/"},  # jamais une adresse venue de l'écran
        {"kind": "freetv", "user_agent": "x"},
        {"kind": "freetv", "id": "x"},
        {"kind": "freetv", "share": "Disque dur"},
        {"kind": "freetv", "path": "/etc/passwd"},
    ],
)
async def test_free_tv_takes_no_parameter_from_the_screen(dock, params):
    async with dock.connect() as browser, helper(dock, chromium=True, widevine=True) as ws:
        ack = await call(browser, "f", "dock.player.play", params)
        assert ack["e"]["code"] == "bad_params"
        with pytest.raises(TimeoutError):
            await command_of(ws, timeout=0.3)  # rien n'est parti vers l'assistant


async def test_free_tv_without_the_helper_says_what_is_missing(dock):
    async with dock.connect() as browser:
        ack = await call(browser, "f", "dock.player.play", {"kind": "freetv"})
    assert ack["e"]["code"] == "unavailable"
    assert "assistant de lecture" in ack["e"]["msg"]


async def test_the_keyboard_of_free_tv_goes_through_a_named_action(dock):
    async with (
        dock.connect() as browser,
        helper(dock, chromium=True, widevine=True, keyboard=True) as ws,
    ):
        await call(browser, "f", "dock.player.play", {"kind": "freetv"})
        await command_of(ws)
        assert (await call(browser, "k", "dock.player.keyboard", {"on": True}))["ok"]
        command = await command_of(ws)
        assert (command["name"], command["on"]) == ("keyboard", True)
        for bad in ({"on": "oui"}, {"on": 1}, {}, {"on": True, "x": 1}):
            assert (await call(browser, "k", "dock.player.keyboard", bad))["e"][
                "code"
            ] == "bad_params"


async def test_the_helper_is_given_the_free_tv_settings_of_dock_toml(dock):
    async with helper(dock) as ws:
        first = json.loads(await asyncio.wait_for(ws.recv(), 3))
        assert first["freetv_user_agent"] == "auto"
        assert first["freetv_scale"] == 0.7
        assert first["freetv_corner"] == "top-left"
        assert first["freetv_fullscreen"] is True
        assert "token" not in json.dumps(first)  # l'assistant ne connaît jamais le jeton
