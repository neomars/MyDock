"""Un jeu de MAME sur l'écran du dock : lancement, détection de la fenêtre, cascade de sortie, erreurs, et la session (toucher, bouton, manette)."""

import asyncio
import json
import os
import stat
import sys
import time
from pathlib import Path
from typing import Any

import pytest

from mydock_dock import mame_playback as mp
from mydock_dock import player_session as ps
from mydock_dock.mame_playback import (
    CTRLR_CFG,
    QUIT_LUA,
    DemoGamePlayback,
    MameDirs,
    MamePlayback,
    explain_exit,
    mame_command,
    prepare_dirs,
    read_flags,
    valid_rompath,
)
from mydock_dock.player_session import Session

# Un faux MAME : il ignore SIGTERM et SIGINT comme le vrai, note sa ligne de commande, et selon FAKE_MAME_MODE :
#   polite   — quitte quand le drapeau de sortie existe (le script Lua du vrai MAME) ou quand sa fenêtre est « fermée » ;
#   stubborn — ignore le drapeau, quitte seulement quand sa fenêtre est « fermée » ;
#   hung     — n'obéit à rien (il faut le tuer) ;
#   missing, unknown, crash, quick — s'arrêtent d'eux-mêmes avec le message et le code du vrai MAME.
FAKE_MAME = """#!{python}
import json, os, signal, sys, time
signal.signal(signal.SIGTERM, signal.SIG_IGN)
signal.signal(signal.SIGINT, signal.SIG_IGN)
mode = os.environ.get("FAKE_MAME_MODE", "polite")
game = sys.argv[1]
with open(os.environ["FAKE_MAME_LOG"], "w") as handle:
    json.dump({{"argv": sys.argv[1:], "flag": os.environ.get("MYDOCK_QUIT_FLAG"), "sdl": os.environ.get("SDL_VIDEODRIVER"),
               "wmclass": os.environ.get("SDL_VIDEO_WAYLAND_WMCLASS"), "pid": os.getpid()}}, handle)
if mode == "missing":
    sys.stderr.write("libEGL warning: bruit\\n" + game + ".bin NOT FOUND (tried in " + game + ")\\n")
    sys.stderr.write("Required files are missing, the machine cannot be run.\\n")
    sys.exit(2)
if mode == "bios":
    sys.stderr.write("sp-s2.sp1 NOT FOUND (tried in neogeo)\\nboot.bin WRONG LENGTH (expected: 10 found: 5)\\n")
    sys.exit(2)
if mode == "unknown":
    sys.stderr.write("Unknown system '" + game + "'\\n")
    sys.exit(5)
if mode == "crash":
    sys.stderr.write("MESA: error: ZINK: vkCreateInstance failed\\nFatal error: Could not initialize SDL\\n")
    sys.exit(3)
if mode == "quick":
    sys.exit(0)
flag, window, close = os.environ["MYDOCK_QUIT_FLAG"], os.environ["FAKE_MAME_WINDOW"], os.environ["FAKE_MAME_CLOSE"]
time.sleep(float(os.environ.get("FAKE_MAME_LOAD", "0")))
open(window, "w").write("mame")
while True:
    if mode == "polite" and os.path.exists(flag):
        sys.exit(0)
    if mode in ("polite", "stubborn") and os.path.exists(close):
        sys.exit(0)
    time.sleep(0.03)
"""


class FakeWindows:
    """Ce que `wlrctl` dit : la fenêtre du jeu n'existe qu'une fois le faux MAME « affiché »."""

    available = True

    def __init__(self, window: Path, close: Path):
        self.window, self.close_file = window, close
        self.focused: list[str] = []
        self.closed: list[str] = []
        self.listing_works = True

    async def toplevels(self):
        if not self.listing_works:
            return []
        found = [("chromium", "MyDock")]
        if self.window.exists():
            found.append(("mame", "MAME: Pong"))
        return found

    async def focus(self, which):
        self.focused.append(which)
        return True

    async def close_app(self, word):
        self.closed.append(word)
        self.close_file.write_text("x")
        return 1


class FakeTouch:
    available = True

    def __init__(self):
        self.running = False

    async def start(self):
        self.running = True

    async def stop(self):
        self.running = False


@pytest.fixture
def rig(tmp_path, monkeypatch):
    mame = tmp_path / "mame"
    mame.write_text(FAKE_MAME.format(python=sys.executable))
    mame.chmod(mame.stat().st_mode | stat.S_IEXEC)
    window, close = tmp_path / "window", tmp_path / "close"
    run = tmp_path / "run"
    run.mkdir(mode=0o700)
    log = tmp_path / "mame-log.json"
    monkeypatch.setenv("FAKE_MAME_LOG", str(log))
    monkeypatch.setenv("FAKE_MAME_WINDOW", str(window))
    monkeypatch.setenv("FAKE_MAME_CLOSE", str(close))
    monkeypatch.delenv("FAKE_MAME_MODE", raising=False)
    monkeypatch.setattr(MamePlayback, "FLAG_WAIT", 0.4)
    monkeypatch.setattr(MamePlayback, "CLOSE_WAIT", 0.6)
    monkeypatch.setattr(MamePlayback, "KILL_WAIT", 2.0)
    monkeypatch.setattr(mp, "FLAGS_FILE", str(tmp_path / "absent.flags"))
    return type(
        "Rig",
        (),
        {
            "mame": str(mame), "window": window, "close": close, "run": run, "log": log, "tmp": tmp_path,
            "windows": FakeWindows(window, close), "roms": tmp_path / "roms",
        },
    )()  # fmt: skip


def game(rig, name="pong", mode=None, monkeypatch=None, **kw):
    if mode is not None:
        os.environ["FAKE_MAME_MODE"] = mode
    return MamePlayback(
        name,
        rompath=str(rig.roms),
        mame=rig.mame,
        windows=rig.windows,
        env={**os.environ, "HOME": str(rig.tmp / "home")},
        runtime_dir=rig.run,
        job=kw.pop("job", 7),
        title=kw.pop("title", "Pong"),
        extra=kw.pop("extra", []),
        **kw,
    )


async def until(predicate, timeout=6.0):
    async with asyncio.timeout(timeout):
        while not predicate():
            await asyncio.sleep(0.02)


def alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


def logged(rig) -> dict[str, Any]:
    return json.loads(rig.log.read_text())


# --- la ligne de commande -----------------------------------------------------------------------------------------


def test_the_command_line_is_fixed_and_everything_lives_under_the_kiosk_home(tmp_path):
    dirs = MameDirs(tmp_path / "mame")
    command = mame_command(
        "/usr/games/mame", "pong", "/var/lib/mydock/roms", dirs, ["-sound", "none"]
    )
    assert command[:2] == ["/usr/games/mame", "pong"]
    options = dict(zip(command[2::2], command[3::2], strict=False))
    assert options["-rompath"] == "/var/lib/mydock/roms"
    assert options["-ctrlr"] == "mydock"
    assert options["-plugin"] == "hiscore"
    assert options["-video"] == "accel"
    assert (
        options["-inipath"] == f"{dirs.ini};/etc/mame"
    )  # sans /etc/mame, MAME ne retrouve pas ses plugins
    assert "-skip_gameinfo" in command
    assert "-nowindow" in command  # plein écran
    assert command[-2:] == [
        "-sound",
        "none",
    ]  # les options de mame.flags passent en dernier : elles l'emportent
    for key in (
        "-homepath", "-inipath", "-cfg_directory", "-nvram_directory", "-input_directory", "-state_directory",
        "-diff_directory", "-snapshot_directory", "-comment_directory", "-ctrlrpath", "-autoboot_script",
    ):  # fmt: skip
        assert command[command.index(key) + 1].startswith(str(dirs.root)), key


def test_prepare_writes_the_helper_files_once(tmp_path):
    dirs = MameDirs(tmp_path / "mame")
    prepare_dirs(dirs)
    assert dirs.script.read_text() == QUIT_LUA
    assert (dirs.ctrlr / "mydock.cfg").read_text() == CTRLR_CFG
    assert all(p.is_dir() for p in dirs.all())
    stamp = dirs.script.stat().st_mtime_ns
    prepare_dirs(dirs)
    assert dirs.script.stat().st_mtime_ns == stamp  # pas réécrit s'il n'a pas changé
    dirs.script.write_text("-- abîmé")
    prepare_dirs(dirs)
    assert dirs.script.read_text() == QUIT_LUA


def test_the_lua_script_reads_the_flag_and_quits_cleanly():
    assert "MYDOCK_QUIT_FLAG" in QUIT_LUA
    assert "manager.machine:exit()" in QUIT_LUA
    assert "register_frame_done" in QUIT_LUA


def test_the_controller_file_maps_select_to_coin_and_start_to_start():
    assert 'type="COIN1"' in CTRLR_CFG
    assert "KEYCODE_5 OR JOYCODE_1_SELECT" in CTRLR_CFG
    assert 'type="START1"' in CTRLR_CFG
    assert "KEYCODE_1 OR JOYCODE_1_START" in CTRLR_CFG
    assert "JOYCODE_4_START" in CTRLR_CFG


@pytest.mark.parametrize(
    "value", ["relatif/roms", "", None, 3, "/a;/b", "/a\nb", "/a\0b", "/" + "x" * 1100]
)
def test_a_rom_folder_the_assistant_does_not_accept(value):
    assert valid_rompath(value) is None


def test_a_normal_rom_folder_is_accepted():
    assert valid_rompath("/var/lib/mydock/roms") == "/var/lib/mydock/roms"


def test_mame_flags_are_one_per_line_without_comments(tmp_path):
    path = tmp_path / "mame.flags"
    path.write_text("# commentaire\n-sound none\n\n  -video opengl  \n#-x\n")
    assert read_flags(str(path)) == ["-sound none", "-video opengl"]
    assert read_flags(str(tmp_path / "absent")) == []


@pytest.mark.parametrize("name", ["../x", "PONG", "pong.zip", "a b", "", "x" * 25, "pong\n", "é"])
async def test_a_bad_game_name_never_reaches_mame(rig, name):
    with pytest.raises(RuntimeError, match="Nom de jeu refusé"):
        await game(rig, name=name).start()
    assert not rig.log.exists()


async def test_a_bad_rom_folder_never_reaches_mame(rig):
    playback = game(rig)
    playback._rompath = "/a;/b"
    with pytest.raises(RuntimeError, match="Dossier des ROMs refusé"):
        await playback.start()
    assert not rig.log.exists()


# --- la vie d'un jeu ----------------------------------------------------------------------------------------------


async def test_a_game_starts_then_is_playing_once_its_window_exists(rig, monkeypatch):
    monkeypatch.setenv("FAKE_MAME_LOAD", "0.3")
    playback = game(rig)
    await playback.start()
    try:
        await until(rig.log.exists)
        assert (await playback.status())["state"] == "starting"  # MAME charge, aucune fenêtre
        await until(rig.window.exists)
        status = await playback.status()
        assert status["state"] == "playing"
        assert status["seekable"] is False
        assert status["position"] is None
        assert playback.shown
        seen = logged(rig)
        assert seen["argv"][0] == "pong"
        assert seen["flag"] == str(rig.run / "mydock-mame-quit-7")
        assert seen["wmclass"] == "mame"
    finally:
        await playback.stop()


async def test_wayland_is_used_when_the_session_has_a_display(rig, monkeypatch):
    monkeypatch.setenv("WAYLAND_DISPLAY", "wayland-0")
    monkeypatch.delenv("SDL_VIDEODRIVER", raising=False)
    playback = game(rig)
    playback._env = {**os.environ, "HOME": str(rig.tmp / "home"), "WAYLAND_DISPLAY": "wayland-0"}
    await playback.start()
    try:
        await until(rig.log.exists)
        assert logged(rig)["sdl"] == "wayland"
    finally:
        await playback.stop()


async def test_a_polite_exit_uses_the_flag_file(rig):
    playback = game(rig)
    await playback.start()
    await until(rig.window.exists)
    started = time.monotonic()
    await playback.stop()
    assert playback.how_stopped == "propre (script)"
    assert time.monotonic() - started < 2.0
    assert playback.finished
    assert playback._process.returncode == 0
    assert rig.windows.closed == []  # la fenêtre n'a pas eu à être fermée
    assert not (rig.run / "mydock-mame-quit-7").exists()  # le drapeau est rangé
    assert playback.error() is None  # une sortie demandée n'est jamais une erreur


async def test_a_stubborn_game_gets_its_window_closed(rig):
    playback = game(rig, mode="stubborn")
    await playback.start()
    await until(rig.window.exists)
    await playback.stop()
    assert playback.how_stopped == "fenêtre fermée"
    assert rig.windows.closed == ["mame"]
    assert playback.finished
    assert playback.error() is None


async def test_a_hung_game_is_killed_and_leaves_nothing_behind(rig):
    playback = game(rig, mode="hung")
    await playback.start()
    await until(rig.window.exists)
    pid = logged(rig)["pid"]
    assert alive(pid)
    await playback.stop()
    assert playback.how_stopped == "tué"
    assert rig.windows.closed == ["mame"]  # on a d'abord essayé de fermer la fenêtre
    await until(lambda: not alive(pid))
    assert playback.error() is None


async def test_stopping_twice_is_harmless(rig):
    playback = game(rig)
    await playback.start()
    await until(rig.window.exists)
    await playback.stop()
    await playback.stop()
    assert playback.how_stopped == "propre (script)"


async def test_stopping_a_game_that_already_exited_is_harmless(rig):
    playback = game(rig, mode="quick")
    await playback.start()
    await until(lambda: playback.finished)
    await playback.stop()
    assert playback.how_stopped == "déjà fini"


async def test_blind_mode_trusts_a_running_game_when_windows_cannot_be_listed(rig, monkeypatch):
    monkeypatch.setattr(MamePlayback, "BLIND_SHOWN_AFTER", 0.2)
    rig.windows.listing_works = False
    playback = game(rig)
    await playback.start()
    try:
        await until(rig.log.exists)
        assert (await playback.status())["state"] == "starting"
        await asyncio.sleep(0.3)
        assert (await playback.status())["state"] == "playing"
    finally:
        await playback.stop()


async def test_a_game_that_never_shows_stays_starting(rig, monkeypatch):
    """Le dock l'arrête au bout de 40 s : tant que MAME n'a pas de fenêtre, l'état reste « starting »."""
    monkeypatch.setenv("FAKE_MAME_LOAD", "30")
    playback = game(rig)
    await playback.start()
    try:
        await asyncio.sleep(0.4)
        assert (await playback.status())["state"] == "starting"
    finally:
        await playback.stop()


# --- les arrêts que MAME décide seul -------------------------------------------------------------------------------


async def finished_with(rig, mode, name="pong"):
    playback = game(rig, name=name, mode=mode)
    await playback.start()
    await until(lambda: playback.finished)
    await playback.wait_output()
    return playback


async def test_missing_roms_are_explained(rig):
    playback = await finished_with(rig, "missing")
    message = playback.error()
    assert "« Pong »" in message
    assert "incomplet" in message
    assert "pong.bin" in message
    assert "pong.zip" in message
    assert "libEGL" not in message  # le bruit des pilotes graphiques n'est pas la cause
    await playback.stop()


async def test_a_missing_bios_is_named(rig):
    playback = await finished_with(rig, "bios", name="kof98")
    message = playback.error()
    assert "sp-s2.sp1" in message
    assert "neogeo.zip" in message
    await playback.stop()


async def test_an_unknown_game_is_explained(rig):
    playback = await finished_with(rig, "unknown", name="inconnu")
    assert "ne connaît pas le jeu « inconnu »" in playback.error()
    await playback.stop()


async def test_a_crash_says_what_mame_said(rig):
    playback = await finished_with(rig, "crash")
    message = playback.error()
    assert "code 3" in message
    assert "Could not initialize SDL" in message
    assert "MESA" not in message
    await playback.stop()


async def test_a_normal_exit_by_the_player_is_not_an_error(rig):
    playback = await finished_with(rig, "quick")  # Échap dans MAME : code 0
    assert playback.error() is None
    await playback.stop()


@pytest.mark.parametrize(
    ("code", "tail", "expected"),
    [
        (0, "", None),
        (2, "", "incomplet"),
        (5, "", "ne connaît pas"),
        (-9, "", "signal 9"),
        (-11, "libEGL warning: x\nALSA lib y", "signal 11"),
        (1, "", "sans dire pourquoi"),
        (1, "Average speed: 99%\nQuelque chose de précis a échoué", "Quelque chose de précis"),
        (2, "a.bin NOT FOUND (tried in pong, neogeo)\nb.bin NOT FOUND", "neogeo.zip"),
        (
            2,
            "pacman.6e NOT FOUND (tried in pacman puckman)",
            "puckman.zip",
        ),  # un clone : il lui faut son parent
    ],
)
def test_exit_explanations(code, tail, expected):
    message = explain_exit("pong", "Pong", code, tail)
    if expected is None:
        assert message is None
    else:
        assert expected in message


def test_many_missing_files_are_summarised():
    tail = "\n".join(f"f{i}.bin NOT FOUND" for i in range(9))
    message = explain_exit("x", "X", 2, tail)
    assert "f0.bin" in message
    assert "f4.bin" not in message
    assert "…" in message


# --- le faux jeu de la démo ----------------------------------------------------------------------------------------


async def test_the_demo_game_starts_then_plays_then_stops():
    demo = DemoGamePlayback("pong", title="Pong")
    await demo.start()
    assert (await demo.status())["state"] == "starting"
    demo._started -= 2
    assert (await demo.status())["state"] == "playing"
    assert not demo.finished
    await demo.stop()
    assert demo.finished
    assert demo.error() is None


# --- la session ---------------------------------------------------------------------------------------------------


class Mailbox:
    def __init__(self):
        self.messages: list[dict[str, Any]] = []

    async def __call__(self, message):
        self.messages.append(message)

    def of(self, kind):
        return [m for m in self.messages if m["t"] == kind]

    async def wait(self, predicate, timeout=8.0):
        async with asyncio.timeout(timeout):
            while not any(predicate(m) for m in self.messages):
                await asyncio.sleep(0.02)


class FakeInputs:
    """L'observateur de saisie : on note ce que la session lui demande."""

    def __init__(self, touch_seen=True):
        self.started = self.stopped = 0
        self.armed: list[float] = []
        self.touch_seen = touch_seen

    def start(self, loop=None):
        self.started += 1

    def stop(self):
        self.stopped += 1

    def arm(self, after):
        self.armed.append(after)

    def summary(self):
        return "0 écran tactile, 0 manette"


@pytest.fixture
def fast(monkeypatch):
    monkeypatch.setattr(ps, "POLL_SECONDS", 0.05)
    monkeypatch.setattr(ps, "GAME_TOUCH_GRACE", 0.3)


def make_session(rig, **kw):
    s = Session(
        "ws://unused",
        vlc="",
        ffmpeg="",
        smbclient="",
        live555=False,
        runtime_dir=rig.run,
        extra_vlc_args=[],
        mame=kw.pop("mame", rig.mame),
        env={**os.environ, "HOME": str(rig.tmp / "home")},
        **kw,
    )
    s.windows, s.touch = rig.windows, FakeTouch()
    s.inputs = FakeInputs()
    s.wake_screen = lambda: asyncio.sleep(0, result=True)
    s._loop = asyncio.get_running_loop()
    return s


def play_message(job=1, **kw):
    return {
        "t": "cmd", "name": "play", "job": job, "url": "", "live": False, "volume": 70,
        "engine": "mame", "game": "pong", "title": "Pong", "rompath": "/var/lib/mydock/roms", **kw,
    }  # fmt: skip


async def start_game(s, box, **kw):
    await s.handle(play_message(**kw), box)
    await box.wait(lambda m: m["t"] == "state" and m["state"] == "playing")


def stub_mame_home(monkeypatch, rig):
    monkeypatch.setattr(mp, "mame_root", lambda env=None: rig.tmp / "mamehome")


async def test_hello_tells_whether_mame_is_there(rig):
    assert make_session(rig).hello()["mame"] is True
    assert make_session(rig, mame="").hello()["mame"] is False
    assert Session("ws://x", demo=True).hello()["mame"] is True


async def test_a_game_goes_to_the_front_and_is_reported(rig, fast, monkeypatch):
    stub_mame_home(monkeypatch, rig)
    s, box = make_session(rig), Mailbox()
    try:
        await start_game(s, box)
        state = box.of("state")[-1]
        assert state["front"] == "video"
        assert state["job"] == 1
        assert "game" in rig.windows.focused  # MAME est mis devant, pas VLC
        assert s._engine == "mame"
        assert s.inputs.started == 1
        assert s.inputs.armed == [ps.GAME_TOUCH_GRACE]
    finally:
        await s._stop_playback()


async def test_a_touch_during_the_game_quits_it_and_tells_the_dock(rig, fast, monkeypatch):
    stub_mame_home(monkeypatch, rig)
    s, box = make_session(rig), Mailbox()
    await start_game(s, box)
    s._game_touched()  # le doigt qui a lancé le jeu : trop tôt, ignoré
    await asyncio.sleep(0.1)
    assert s._playback is not None
    await asyncio.sleep(0.3)
    s._game_touched()
    await box.wait(lambda m: m["t"] == "ended")
    ended = box.of("ended")[-1]
    assert ended == {"t": "ended", "job": 1, "error": None}
    assert s._playback is None
    assert s._engine is None
    assert s.front == "dock"
    assert rig.windows.focused[-1] == "dock"
    assert s.inputs.stopped >= 1


async def test_select_and_start_quit_the_game(rig, fast, monkeypatch):
    stub_mame_home(monkeypatch, rig)
    s, box = make_session(rig), Mailbox()
    await start_game(s, box)
    s._game_combo()
    await box.wait(lambda m: m["t"] == "ended")
    assert box.of("ended")[-1]["error"] is None
    assert s._playback is None


async def test_the_button_quits_the_game_even_before_it_shows(rig, fast, monkeypatch):
    stub_mame_home(monkeypatch, rig)
    monkeypatch.setenv("FAKE_MAME_LOAD", "30")  # MAME charge encore : le bouton doit déjà marcher
    s, box = make_session(rig), Mailbox()
    await s.handle(play_message(), box)
    await until(rig.log.exists)
    await s._button_pressed("bouton physique")
    assert box.of("ended")[-1] == {"t": "ended", "job": 1, "error": None}
    assert box.of("button")  # le dock est prévenu
    assert s._playback is None


async def test_the_button_works_without_any_dock(rig, fast, monkeypatch):
    """L'assistant ne dépend pas du dock : sans liaison (`_send` vide), le bouton quitte quand même le jeu."""
    stub_mame_home(monkeypatch, rig)
    s, box = make_session(rig), Mailbox()
    await start_game(s, box)
    s._send = None  # le dock redémarre
    await s._button_pressed("bouton physique")
    assert s._playback is None
    assert s.front == "dock"


async def test_the_button_during_a_video_brings_the_dock_back(rig):
    s, box = make_session(rig), Mailbox()
    s._send = box
    s._engine, s._playback, s.front = "vlc", object(), "video"
    await s._button_pressed("bouton physique")
    assert s.front == "dock"
    assert rig.windows.focused[-1] == "dock"
    assert s._playback is not None  # la vidéo continue derrière
    assert box.of("button")


async def test_the_button_on_the_dock_wakes_the_screen(rig):
    s, box = make_session(rig), Mailbox()
    s._send = box
    woken = []

    async def wake():
        woken.append(1)
        return True

    s.wake_screen = wake
    await s._button_pressed("bouton physique")
    assert woken == [1]
    assert box.of("button") == [{"t": "button"}]


async def test_a_failing_screen_wake_never_breaks_the_button(rig):
    s, box = make_session(rig), Mailbox()
    s._send = box

    async def broken():
        raise OSError("wlopm a disparu")

    s.wake_screen = broken
    await s._button_pressed("bouton physique")
    assert box.of("button")


async def test_a_game_that_stops_by_itself_returns_to_the_dock_with_its_reason(
    rig, fast, monkeypatch
):
    stub_mame_home(monkeypatch, rig)
    monkeypatch.setenv("FAKE_MAME_MODE", "missing")
    s, box = make_session(rig), Mailbox()
    await s.handle(play_message(), box)
    await box.wait(lambda m: m["t"] == "ended")
    ended = box.of("ended")[-1]
    assert "incomplet" in ended["error"]
    assert s._engine is None
    assert s.front == "dock"
    assert s.inputs.stopped >= 1


async def test_escape_in_mame_ends_the_game_without_an_error(rig, fast, monkeypatch):
    stub_mame_home(monkeypatch, rig)
    monkeypatch.setenv("FAKE_MAME_MODE", "quick")
    s, box = make_session(rig), Mailbox()
    await s.handle(play_message(), box)
    await box.wait(lambda m: m["t"] == "ended")
    assert box.of("ended")[-1]["error"] is None


async def test_without_mame_the_session_says_how_to_install_it(rig):
    s, box = make_session(rig, mame=""), Mailbox()
    await s.handle(play_message(), box)
    assert "sudo apt install mame" in box.of("ended")[-1]["error"]


@pytest.mark.parametrize(
    "bad",
    [
        {"game": "../../bin/sh"},
        {"game": "PONG"},
        {"game": "pong -video none"},
        {"game": None},
        {"game": 3},
        {"rompath": "relatif"},
        {"rompath": "/a;/b"},
        {"rompath": None},
    ],
)
async def test_a_hostile_play_command_is_refused(rig, bad):
    s, box = make_session(rig), Mailbox()
    await s.handle(play_message(**bad), box)
    assert "refusée" in box.of("ended")[-1]["error"]
    assert not rig.log.exists()  # MAME n'a jamais été lancé


async def test_a_new_game_replaces_the_running_one(rig, fast, monkeypatch):
    stub_mame_home(monkeypatch, rig)
    s, box = make_session(rig), Mailbox()
    await start_game(s, box, job=1)
    first = logged(rig)["pid"]
    rig.window.unlink()
    await s.handle(play_message(job=2, game="galaga"), box)
    await until(lambda: not alive(first))
    await box.wait(lambda m: m["t"] == "state" and m["job"] == 2 and m["state"] == "playing")
    assert logged(rig)["argv"][0] == "galaga"
    await s._stop_playback()


async def test_the_dock_asking_to_stop_ends_the_game(rig, fast, monkeypatch):
    stub_mame_home(monkeypatch, rig)
    s, box = make_session(rig), Mailbox()
    await start_game(s, box)
    pid = logged(rig)["pid"]
    await s.handle({"t": "cmd", "name": "stop", "job": 1}, box)
    assert s._playback is None
    await until(lambda: not alive(pid))


async def test_without_a_readable_touchscreen_swayidle_takes_over(rig, fast, monkeypatch):
    stub_mame_home(monkeypatch, rig)
    s, box = make_session(rig), Mailbox()
    s.inputs = FakeInputs(touch_seen=False)
    await start_game(s, box)
    assert s.touch.running
    await s._stop_playback()
    assert not s.touch.running


async def test_a_swayidle_touch_during_a_game_quits_it(rig, fast, monkeypatch):
    stub_mame_home(monkeypatch, rig)
    s, box = make_session(rig), Mailbox()
    s.inputs = FakeInputs(touch_seen=False)
    await start_game(s, box)
    await asyncio.sleep(0.4)
    s._touched()
    await box.wait(lambda m: m["t"] == "ended")
    assert box.of("ended")[-1]["error"] is None


async def test_the_pin_comes_from_the_dock_and_only_valid_pins_count(rig):
    s = make_session(rig)
    started = []
    s._start_button = lambda: started.append(s.button_pin)
    for bad in (None, "17", 1, 28, True, 99, -3):
        await s._configure({"button_gpio": bad})
    assert started == []
    await s._configure({"button_gpio": 22})
    await s._configure({"button_gpio": 22})  # inchangé : rien à refaire
    assert started == [22]
    assert s.button_pin == 22


async def test_a_new_gpio_status_is_sent_to_the_dock_again(rig):
    s, box = make_session(rig), Mailbox()
    s._send = box
    s._gpio_changed("GPIO17 (broche 11) : prêt.")
    await box.wait(lambda m: m["t"] == "hello")
    assert box.of("hello")[-1]["gpio"] == "GPIO17 (broche 11) : prêt."


# --- le mode démo ---------------------------------------------------------------------------------------------------


async def test_in_demo_the_game_plays_and_two_files_stand_for_the_button_and_the_touch(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(ps, "POLL_SECONDS", 0.05)
    monkeypatch.setattr(ps, "GAME_TOUCH_GRACE", 0.1)
    s, box = Session("ws://x", demo=True, runtime_dir=tmp_path), Mailbox()
    s._loop = asyncio.get_running_loop()
    task = asyncio.create_task(s._demo_triggers())
    try:
        await start_game(s, box)
        assert s._engine == "mame"
        (tmp_path / ps.DEMO_TOUCH).write_text("")
        await box.wait(lambda m: m["t"] == "ended")
        assert box.of("ended")[-1]["error"] is None
        assert not (tmp_path / ps.DEMO_TOUCH).exists()  # consommé
        await start_game(s, box, job=2)
        (tmp_path / ps.DEMO_BUTTON).write_text("")
        await box.wait(lambda m: m["t"] == "ended" and m["job"] == 2)
        assert box.of("button")
    finally:
        task.cancel()
        await s._stop_playback()
