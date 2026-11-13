"""Le vrai MAME, sous un vrai labwc sans écran (si MAME, labwc et wlrctl sont là, et qu'on n'est pas root).

`pong` n'a besoin d'aucune ROM (c'est de la logique discrète) : le jeu se lance donc sans rien télécharger. Ces essais vérifient
ce qu'aucun faux ne peut dire : MAME ouvre sa fenêtre sous Wayland, ignore SIGTERM, quitte proprement sur le fichier-drapeau
de son script Lua, ou sur la fermeture de sa fenêtre, et son code de sortie quand il ne trouve pas un jeu.
"""

import asyncio
import contextlib
import os
import shutil
import signal
import subprocess
import time

import pytest

from mydock_dock import mame_playback as mp
from mydock_dock import player_session as ps
from mydock_dock.mame_playback import MamePlayback
from mydock_dock.retrogame import find_mame

needs_real_mame = pytest.mark.skipif(
    not (find_mame() and shutil.which("labwc") and shutil.which("wlrctl")) or os.geteuid() == 0,
    reason="MAME, labwc et wlrctl requis, et un utilisateur ordinaire",
)


@pytest.fixture
async def desktop(tmp_path):
    """Un labwc sans écran (backend headless, rendu logiciel) et l'environnement pour y lancer des programmes."""
    runtime = tmp_path / "xdg"
    runtime.mkdir(mode=0o700)
    home = tmp_path / "home"
    home.mkdir()
    env = {
        **os.environ,
        "XDG_RUNTIME_DIR": str(runtime), "HOME": str(home), "WLR_BACKENDS": "headless",
        "WLR_RENDERER": "pixman", "WLR_LIBINPUT_NO_DEVICES": "1", "LC_ALL": "C.UTF-8",
        "SDL_AUDIODRIVER": "dummy",
    }  # fmt: skip
    env.pop("WAYLAND_DISPLAY", None)
    env.pop("DISPLAY", None)
    compositor = subprocess.Popen(  # noqa: ASYNC220
        ["labwc"], env=env, stdin=subprocess.DEVNULL,
        stdout=(tmp_path / "labwc.log").open("w"), stderr=subprocess.STDOUT,
    )  # fmt: skip
    try:
        deadline = time.monotonic() + 15
        socket = None
        while time.monotonic() < deadline and socket is None:
            socket = next(
                (p.name for p in runtime.glob("wayland-*") if not p.name.endswith(".lock")), None
            )
            await asyncio.sleep(0.1)
        assert socket, (tmp_path / "labwc.log").read_text()
        env["WAYLAND_DISPLAY"] = socket
        yield type("Desktop", (), {"env": env, "tmp": tmp_path, "runtime": runtime})()
    finally:
        compositor.terminate()
        with contextlib.suppress(subprocess.TimeoutExpired):
            compositor.wait(10)
        compositor.kill()


def real_game(desktop, name="pong", *, windows=None):
    return MamePlayback(
        name,
        rompath=str(desktop.tmp / "roms"),
        mame=find_mame(),
        windows=windows or ps.Windows(desktop.env),
        env=desktop.env,
        runtime_dir=desktop.runtime,
        job=1,
        title="Pong",
        extra=["-sound", "none"],
    )


async def until(predicate, timeout=40.0):
    async with asyncio.timeout(timeout):
        while not predicate():
            await asyncio.sleep(0.1)


async def shown(playback, timeout=40.0):
    async with asyncio.timeout(timeout):
        while True:
            if playback.finished:
                pytest.fail(f"MAME s'est arrêté : {playback.error()}")
            if (await playback.status())["state"] == "playing":
                return
            await asyncio.sleep(0.2)


@needs_real_mame
async def test_the_real_mame_opens_a_window_and_quits_on_the_flag(desktop):
    playback = real_game(desktop)
    await playback.start()
    try:
        await shown(playback)
        started = time.monotonic()
        await playback.stop()
        assert playback.how_stopped == "propre (script)"
        assert time.monotonic() - started < mp.MamePlayback.FLAG_WAIT + 1
        assert playback._process.returncode == 0
    finally:
        await playback.stop()


@needs_real_mame
async def test_the_real_mame_ignores_sigterm(desktop):
    playback = real_game(desktop)
    await playback.start()
    try:
        await shown(playback)
        os.kill(playback._process.pid, signal.SIGTERM)
        await asyncio.sleep(1.5)
        assert not playback.finished  # c'est pourquoi `stop()` n'envoie pas SIGTERM
    finally:
        await playback.stop()
    assert playback.finished


@needs_real_mame
async def test_without_the_script_the_window_is_closed(desktop, monkeypatch):
    """Si le script Lua ne passe pas, la fermeture de la fenêtre (wlrctl) fait quitter MAME proprement."""
    monkeypatch.setattr(mp, "QUIT_LUA", "-- ne fait rien\n")
    monkeypatch.setattr(MamePlayback, "FLAG_WAIT", 1.0)
    playback = real_game(desktop)
    await playback.start()
    try:
        await shown(playback)
        await playback.stop()
        assert playback.how_stopped == "fenêtre fermée"
        assert playback._process.returncode == 0
    finally:
        await playback.stop()


@needs_real_mame
async def test_a_frozen_mame_is_killed(desktop, monkeypatch):
    monkeypatch.setattr(MamePlayback, "FLAG_WAIT", 0.5)
    monkeypatch.setattr(MamePlayback, "CLOSE_WAIT", 1.0)
    playback = real_game(desktop)
    await playback.start()
    try:
        await shown(playback)
        os.kill(
            playback._process.pid, signal.SIGSTOP
        )  # figé : ne lit plus rien, ni drapeau ni fenêtre
        await playback.stop()
        assert playback.how_stopped == "tué"
        assert playback.finished
    finally:
        await playback.stop()


@needs_real_mame
async def test_an_unknown_game_is_reported(desktop):
    playback = real_game(desktop, name="pasunjeu")
    await playback.start()
    await until(lambda: playback.finished, timeout=60)
    await playback.wait_output()
    message = playback.error()
    assert message is not None
    assert "ne connaît pas le jeu « pasunjeu »" in message
    await playback.stop()


@needs_real_mame
async def test_a_game_without_its_roms_is_reported(desktop):
    (desktop.tmp / "roms").mkdir()
    playback = real_game(desktop, name="pacman")
    await playback.start()
    await until(lambda: playback.finished, timeout=60)
    await playback.wait_output()
    message = playback.error()
    assert message
    assert "incomplet" in message
    assert "pacman" in message
    await playback.stop()
