"""Free TV dans l'assistant de session : un seul occupant à la fois, un toucher qui navigue, le retour au dock (bouton, manette,
appui long dans le coin), le clavier à l'écran, et ce qui est dit quand Chromium ou Widevine manque."""

from __future__ import annotations

import asyncio
import json
import os
import signal
import stat
import sys
from typing import Any

import pytest

from mydock_dock import freetv_playback as ft
from mydock_dock import player_session as ps
from mydock_dock.player_session import Session

from .test_freetv_playback import FAKE_CHROMIUM, FakeWindows


class Windows(FakeWindows):
    """Les fenêtres vues par l'assistant : on note qui est mis devant."""

    available = True

    def __init__(self):
        super().__init__()
        self.focused: list[str] = []

    async def focus(self, which):
        self.focused.append(which)
        return True


class FakeTouch:
    available = True

    def __init__(self):
        self.running = False

    async def start(self):
        self.running = True

    async def stop(self):
        self.running = False


class FakeInputs:
    def __init__(self, corner_ready=True):
        self.started = self.stopped = 0
        self.armed: list[float] = []
        self.touch_seen = True
        self.corner_ready = corner_ready
        self.corner = "top-left"

    def start(self, loop=None):
        self.started += 1

    def stop(self):
        self.stopped += 1

    def arm(self, after):
        self.armed.append(after)

    def summary(self):
        return "1 écran tactile, 0 manette"


class FakeKeyboard:
    def __init__(self, works=True):
        self.visible = False
        self.available = True
        self.works = works
        self.calls: list[str] = []

    async def show(self):
        self.calls.append("show")
        self.visible = self.works
        return self.works

    async def hide(self):
        self.calls.append("hide")
        self.visible = False

    async def toggle(self):
        return await self.show()

    async def close(self):
        self.calls.append("close")
        self.visible = False


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


@pytest.fixture
def rig(tmp_path, monkeypatch):
    chromium = tmp_path / "chromium"
    chromium.write_text(FAKE_CHROMIUM.format(python=sys.executable))
    chromium.chmod(chromium.stat().st_mode | stat.S_IEXEC)
    log = tmp_path / "chromium-log.json"
    monkeypatch.setenv("FAKE_LOG", str(log))
    monkeypatch.setattr(ps, "POLL_SECONDS", 0.05)
    monkeypatch.setattr(ft, "CLOSE_WAIT", 1.5)
    monkeypatch.setattr(ft, "TERM_WAIT", 1.5)
    monkeypatch.setattr(ft, "KILL_WAIT", 1.0)
    monkeypatch.setattr(ps, "has_widevine", lambda: True)
    monkeypatch.setattr(ps, "host_resolves", lambda *_: asyncio.sleep(0, result=True))
    monkeypatch.setattr(ft, "FLAGS_FILE", str(tmp_path / "absent.flags"))
    run = tmp_path / "run"
    run.mkdir(mode=0o700)
    windows = Windows()
    windows.pid_file = log
    return type(
        "Rig",
        (),
        {"chromium": str(chromium), "log": log, "windows": windows, "run": run, "tmp": tmp_path},
    )()


def make_session(rig, **kw):
    s = Session(
        "ws://unused",
        vlc="/usr/bin/vlc",
        smbclient="",
        runtime_dir=rig.run,
        extra_vlc_args=[],
        mame="",
        chromium=kw.pop("chromium", rig.chromium),
        keyboard=kw.pop("keyboard", FakeKeyboard()),
        env={
            **os.environ,
            "HOME": str(rig.tmp / "home"),
            "WAYLAND_DISPLAY": "wayland-0",
            **{k: v for k, v in os.environ.items() if k.startswith("FAKE_")},
        },
        **kw,
    )
    s.windows, s.touch, s.inputs = rig.windows, FakeTouch(), FakeInputs()
    s.sound.enabled = False  # le serveur de son de cette machine ne regarde pas ces essais (voir test_pc_audio.py)
    s.wake_screen = lambda: asyncio.sleep(0, result=True)
    s._loop = asyncio.get_running_loop()
    return s


def play_message(job=1, **kw):
    return {
        "t": "cmd",
        "name": "play",
        "job": job,
        "url": "",
        "volume": 70,
        "engine": "freetv",
        **kw,
    }


async def start_freetv(s, box, rig, **kw):
    await s.handle(play_message(**kw), box)
    async with asyncio.timeout(8):
        while not rig.log.exists():
            await asyncio.sleep(0.02)
    rig.windows.show()  # la fenêtre de Free TV apparaît
    await box.wait(lambda m: m["t"] == "state" and m["state"] == "playing")


def chromium_pid(rig) -> int:
    return json.loads(rig.log.read_text())["pid"]


def alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


# --- ce que l'assistant dit de lui ---------------------------------------------------------------------------------------


async def test_hello_tells_whether_chromium_widevine_and_the_keyboard_are_there(rig, monkeypatch):
    hello = make_session(rig).hello()
    assert (hello["chromium"], hello["widevine"], hello["keyboard"]) == (True, True, True)
    monkeypatch.setattr(ps, "has_widevine", lambda: False)
    hello = make_session(rig, chromium="").hello()
    assert (hello["chromium"], hello["widevine"]) == (False, False)
    demo = Session("ws://x", demo=True).hello()
    assert (demo["chromium"], demo["widevine"], demo["keyboard"]) == (True, True, True)


async def test_without_chromium_or_widevine_the_dock_is_told_what_to_install(rig, monkeypatch):
    s, box = make_session(rig, chromium=""), Mailbox()
    await s.handle(play_message(), box)
    assert "sudo apt install chromium" in box.of("ended")[-1]["error"]
    assert s._playback is None

    monkeypatch.setattr(ps, "has_widevine", lambda: False)
    s, box = make_session(rig), Mailbox()
    await s.handle(play_message(), box)
    error = box.of("ended")[-1]["error"]
    assert "Widevine est absent" in error
    assert "sudo apt install libwidevinecdm0" in error
    assert not rig.log.exists()  # rien n'a été lancé


# --- Free TV devant -------------------------------------------------------------------------------------------------------


async def test_free_tv_opens_in_front_with_the_corner_gesture_and_without_the_touch_watcher(rig):
    s, box = make_session(rig), Mailbox()
    await s.handle(
        {"t": "cmd", "name": "configure", "freetv_corner": "bottom-right", "freetv_scale": 0.8}, box
    )
    try:
        await start_freetv(s, box, rig)
        assert s._engine == "freetv"
        state = box.of("state")[-1]
        assert (state["front"], state["job"]) == ("video", 1)
        assert state["keyboard"] is False
        assert (
            "freetv" in rig.windows.focused
        )  # la fenêtre de Free TV est mise devant, pas VLC ni MAME
        assert s.inputs.started == 1
        assert s.inputs.armed == [ps.FREETV_TOUCH_GRACE]
        assert s.inputs.corner == "bottom-right"
        assert (
            not s.touch.running
        )  # swayidle ramènerait le dock au premier toucher : il n'est pas lancé
        args = json.loads(rig.log.read_text())["args"]
        assert "--force-device-scale-factor=0.8" in args
    finally:
        await s._stop_playback()


async def test_a_touch_navigates_in_free_tv_and_never_brings_the_dock_back(rig):
    s, box = make_session(rig), Mailbox()
    try:
        await start_freetv(s, box, rig)
        s._front_since -= 10
        s._touched()  # le toucher de swayidle, si jamais il tournait
        s._game_touched()
        await asyncio.sleep(0.2)
        assert s.front == "video"
        assert rig.windows.focused[-1] == "freetv"
    finally:
        await s._stop_playback()


async def test_a_long_press_in_the_corner_brings_the_dock_back_and_free_tv_stays_open_behind(rig):
    s, box = make_session(rig), Mailbox()
    try:
        await start_freetv(s, box, rig)
        pid = chromium_pid(rig)
        s._freetv_corner()
        await asyncio.sleep(0.2)
        assert s.front == "dock"
        assert rig.windows.focused[-1] == "dock"
        assert s._playback is not None
        assert alive(pid)  # Free TV est resté derrière, prêt à revenir
        await box.wait(lambda m: m["t"] == "state" and m["front"] == "dock")
        await s.handle({"t": "cmd", "name": "front", "to": "video"}, box)
        assert s.front == "video"
        assert rig.windows.focused[-1] == "freetv"  # « Revenir à Free TV »
    finally:
        await s._stop_playback()


@pytest.mark.parametrize("how", ["button", "combo"])
async def test_the_physical_button_and_select_start_bring_the_dock_back_too(rig, how):
    s, box = make_session(rig), Mailbox()
    try:
        await start_freetv(s, box, rig)
        if how == "button":
            await s._button_pressed("bouton physique")
            assert box.of("button")  # le dock est prévenu : il réveille son écran
        else:
            s._game_combo()
            await asyncio.sleep(0.2)
        assert s.front == "dock"
        assert rig.windows.focused[-1] == "dock"
        assert s._playback is not None
        assert alive(chromium_pid(rig))
    finally:
        await s._stop_playback()


async def test_the_corner_gesture_of_another_occupant_does_nothing(rig):
    s = make_session(rig)
    s._freetv_corner()  # rien n'est ouvert
    s._engine = "vlc"
    s._freetv_corner()
    await asyncio.sleep(0.05)
    assert rig.windows.focused == []


# --- un seul occupant à la fois ---------------------------------------------------------------------------------------------


async def test_stopping_closes_free_tv_gives_the_screen_back_and_closes_the_keyboard(rig):
    s, box = make_session(rig), Mailbox()
    await start_freetv(s, box, rig)
    pid = chromium_pid(rig)
    await s.handle({"t": "cmd", "name": "stop", "job": 1}, box)
    assert s._playback is None
    assert s._engine is None
    assert not alive(pid)
    assert rig.windows.closed == ["tv.free.fr"]
    assert rig.windows.focused[-1] == "dock"
    assert "close" in s.keyboard.calls
    assert s.inputs.stopped >= 1


async def test_a_video_started_while_free_tv_is_open_closes_free_tv(rig):
    s, box = make_session(rig), Mailbox()
    await start_freetv(s, box, rig)
    pid = chromium_pid(rig)
    await s.handle(
        {"t": "cmd", "name": "play", "job": 2, "url": "/tmp/a.mkv", "volume": 70}, box
    )  # VLC est absent des essais : l'échec compte peu, c'est Free TV qui doit avoir été fermé d'abord
    assert not alive(pid)
    assert rig.windows.closed == ["tv.free.fr"]
    assert s._engine != "freetv"
    await s._stop_playback()


async def test_free_tv_opened_while_a_game_runs_stops_the_game(rig):
    s, box = make_session(rig), Mailbox()

    class Game:
        stopped = False
        finished = False

        async def stop(self):
            self.stopped = True

    game = Game()
    s._playback, s._engine = game, "mame"
    await start_freetv(s, box, rig, job=3)
    assert game.stopped
    assert s._engine == "freetv"
    await s._stop_playback()


async def test_free_tv_closed_by_hand_ends_normally_and_the_dock_comes_back(rig):
    s, box = make_session(rig), Mailbox()
    await start_freetv(s, box, rig)
    os.kill(chromium_pid(rig), signal.SIGTERM)
    await box.wait(lambda m: m["t"] == "ended")
    assert box.of("ended")[-1] == {"t": "ended", "job": 1, "error": None}
    assert s._playback is None
    assert rig.windows.focused[-1] == "dock"
    assert "close" in s.keyboard.calls


async def test_a_chromium_that_dies_at_once_is_explained_to_the_dock(rig, monkeypatch):
    monkeypatch.setenv("FAKE_EXIT", "3")
    monkeypatch.setenv("FAKE_STDERR", "No usable sandbox! Update your kernel")
    s, box = make_session(rig), Mailbox()
    await s.handle(play_message(), box)
    await box.wait(lambda m: m["t"] == "ended")
    assert "bac à sable" in box.of("ended")[-1]["error"]
    assert s._engine is None


# --- le clavier à l'écran ---------------------------------------------------------------------------------------------------


async def test_the_keyboard_is_shown_in_front_of_free_tv_and_reported_to_the_dock(rig):
    s, box = make_session(rig), Mailbox()
    try:
        await start_freetv(s, box, rig)
        await s.handle({"t": "cmd", "name": "keyboard", "on": True}, box)
        assert s.keyboard.visible
        await box.wait(lambda m: m["t"] == "state" and m.get("keyboard") is True)
        await s.handle({"t": "cmd", "name": "keyboard", "on": False}, box)
        assert not s.keyboard.visible
        await box.wait(lambda m: m["t"] == "state" and m.get("keyboard") is False)
    finally:
        await s._stop_playback()


async def test_the_keyboard_goes_away_when_the_dock_comes_back(rig):
    s, box = make_session(rig), Mailbox()
    try:
        await start_freetv(s, box, rig)
        await s.handle({"t": "cmd", "name": "keyboard", "on": True}, box)
        s._freetv_corner()
        await asyncio.sleep(0.2)
        assert not s.keyboard.visible
        await s.handle(
            {"t": "cmd", "name": "keyboard", "on": True}, box
        )  # Free TV est derrière : rien à écrire dedans
        assert not s.keyboard.visible
    finally:
        await s._stop_playback()


async def test_the_keyboard_is_only_for_free_tv_and_only_with_a_real_yes_or_no(rig):
    s, box = make_session(rig), Mailbox()
    await s.handle({"t": "cmd", "name": "keyboard", "on": True}, box)  # rien ne tourne
    assert s.keyboard.calls == []
    try:
        await start_freetv(s, box, rig)
        for bad in ("oui", 1, None):
            await s.handle({"t": "cmd", "name": "keyboard", "on": bad}, box)
        assert "show" not in s.keyboard.calls
    finally:
        await s._stop_playback()


async def test_a_keyboard_that_does_not_start_is_not_reported_as_shown(rig):
    s, box = make_session(rig, keyboard=FakeKeyboard(works=False)), Mailbox()
    try:
        await start_freetv(s, box, rig)
        await s.handle({"t": "cmd", "name": "keyboard", "on": True}, box)
        await asyncio.sleep(0.2)
        assert box.of("state")[-1]["keyboard"] is False
    finally:
        await s._stop_playback()


# --- la configuration que le dock transmet ---------------------------------------------------------------------------------


async def test_the_settings_of_the_dock_are_checked_before_they_are_kept(rig):
    s, box = make_session(rig), Mailbox()
    await s.handle(
        {
            "t": "cmd",
            "name": "configure",
            "freetv_user_agent": "",
            "freetv_scale": 1.5,
            "freetv_corner": "top-right",
        },
        box,
    )
    assert (s.freetv_user_agent, s.freetv_scale, s.freetv_corner) == ("", 1.5, "top-right")
    await s.handle(
        {
            "t": "cmd", "name": "configure", "freetv_user_agent": "x\ny", "freetv_scale": 99,
            "freetv_corner": "milieu",
        },
        box,
    )  # fmt: skip
    assert (s.freetv_user_agent, s.freetv_scale, s.freetv_corner) == ("", 1.5, "top-right")
    await s.handle(
        {"t": "cmd", "name": "configure", "freetv_scale": True, "freetv_user_agent": 3}, box
    )
    assert (s.freetv_user_agent, s.freetv_scale) == ("", 1.5)
    assert s.freetv_fullscreen is True
    await s.handle({"t": "cmd", "name": "configure", "freetv_fullscreen": False}, box)
    assert s.freetv_fullscreen is False
    await s.handle({"t": "cmd", "name": "configure", "freetv_fullscreen": "non"}, box)
    assert s.freetv_fullscreen is False  # ce qui n'est pas un booléen est ignoré


async def test_a_maximised_free_tv_when_dock_toml_says_so(rig):
    s, box = make_session(rig), Mailbox()
    await s.handle({"t": "cmd", "name": "configure", "freetv_fullscreen": False}, box)
    try:
        await start_freetv(s, box, rig)
        assert "--start-fullscreen" not in json.loads(rig.log.read_text())["args"]
    finally:
        await s._stop_playback()


async def test_an_empty_identity_in_dock_toml_means_chromium_keeps_its_own(rig):
    s, box = make_session(rig), Mailbox()
    await s.handle({"t": "cmd", "name": "configure", "freetv_user_agent": ""}, box)
    try:
        await start_freetv(s, box, rig)
        assert not any(
            a.startswith("--user-agent") for a in json.loads(rig.log.read_text())["args"]
        )
    finally:
        await s._stop_playback()


async def test_a_chromium_left_open_by_a_previous_assistant_is_closed_at_startup(rig):
    import subprocess

    profile = ft.profile_dir({"HOME": str(rig.tmp / "home")})
    leftover = subprocess.Popen(  # noqa: ASYNC220
        [sys.executable, "-c", "import time; time.sleep(60)", f"--user-data-dir={profile}"]
    )
    try:
        async with asyncio.timeout(5):
            while leftover.pid not in ft.stale_pids(profile):
                await asyncio.sleep(0.05)
        s = make_session(rig)
        await s._close_stale_freetv()
        assert leftover.wait(5) == -signal.SIGTERM
    finally:
        if leftover.poll() is None:
            leftover.kill()


# --- la démonstration ------------------------------------------------------------------------------------------------------


async def test_the_demo_opens_a_fake_free_tv_without_chromium_nor_inputs(rig):
    s, box = Session("ws://unused", demo=True, keyboard=FakeKeyboard()), Mailbox()
    s._loop = asyncio.get_running_loop()
    s.sound.enabled = False
    await s.handle(play_message(), box)
    try:
        await box.wait(lambda m: m["t"] == "state" and m["state"] == "playing", timeout=8)
        assert s._engine == "freetv"
        assert s.front == "video"
        s._freetv_corner()
        await asyncio.sleep(0.2)
        assert s.front == "dock"
    finally:
        await s._stop_playback()
