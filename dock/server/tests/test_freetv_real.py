"""Un vrai Chromium sous un vrai labwc sans écran (si labwc, wlrctl et un Chromium sont là, et qu'on n'est pas root).

La page de Free TV n'a pas besoin de se charger : en mode `--app`, la fenêtre porte déjà l'adresse ouverte dans son identifiant
(sans réseau elle montre la page d'erreur de Chromium). Ces essais vérifient ce qu'aucun faux ne peut dire : l'identifiant que
labwc donne à la fenêtre de Free TV et à celle du dock (deux Chromium), le passage de l'une à l'autre, et la fermeture.

Un Chromium sans bac à sable (`--no-sandbox`) est nécessaire dans un conteneur ; sur le Pi, c'est celui du paquet qui est lancé.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import shutil
import subprocess
import time
from pathlib import Path

import pytest

from mydock_dock import player_session as ps
from mydock_dock.freetv_playback import FreeTvPlayback, find_chromium


def real_chromium() -> str | None:
    for candidate in (
        os.environ.get("MYDOCK_TEST_CHROMIUM"),
        find_chromium(),
        "/opt/pw-browsers/chromium",
    ):
        if candidate and Path(candidate).exists():
            return candidate
    return None


CHROMIUM = real_chromium()
needs_real_chromium = pytest.mark.skipif(
    not (CHROMIUM and shutil.which("labwc") and shutil.which("wlrctl")) or os.geteuid() == 0,
    reason="Chromium, labwc et wlrctl requis, et un utilisateur ordinaire",
)
FLAGS = ["--no-sandbox", "--disable-gpu", "--no-proxy-server"]


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
    }  # fmt: skip
    for key in (
        "WAYLAND_DISPLAY",
        "DISPLAY",
        "HTTPS_PROXY",
        "https_proxy",
        "HTTP_PROXY",
        "http_proxy",
    ):
        env.pop(key, None)
    compositor = subprocess.Popen(  # noqa: ASYNC220
        ["labwc"], env=env, stdin=subprocess.DEVNULL,
        stdout=(tmp_path / "labwc.log").open("w"), stderr=subprocess.STDOUT,
    )  # fmt: skip
    dock = None
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
        # Le dock : un Chromium en kiosque sur son propre profil, comme celui du Pi.
        dock = subprocess.Popen(  # noqa: ASYNC220
            [CHROMIUM, *FLAGS, "--ozone-platform=wayland", f"--user-data-dir={tmp_path}/dock", "--kiosk",
             "--no-first-run", "data:text/html,<title>MyDock</title>LE DOCK"],
            env=env, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )  # fmt: skip
        windows = ps.Windows(env)
        async with asyncio.timeout(60):
            while not await windows.toplevels():
                await asyncio.sleep(0.3)
        yield type(
            "Desktop", (), {"env": env, "tmp": tmp_path, "runtime": runtime, "windows": windows}
        )()
    finally:
        if dock is not None:
            dock.terminate()
            with contextlib.suppress(subprocess.TimeoutExpired):
                dock.wait(10)
            dock.kill()
        compositor.terminate()
        with contextlib.suppress(subprocess.TimeoutExpired):
            compositor.wait(10)
        compositor.kill()


def real_free_tv(desktop, **kw):
    return FreeTvPlayback(
        chromium=CHROMIUM, windows=desktop.windows, env=desktop.env, runtime_dir=desktop.runtime, job=1,
        extra=FLAGS, **kw,
    )  # fmt: skip


async def until(predicate, timeout=60.0):
    async with asyncio.timeout(timeout):
        while not await predicate():
            await asyncio.sleep(0.3)


@needs_real_chromium
async def test_the_window_of_free_tv_and_that_of_the_dock_are_told_apart_and_brought_forward_in_turn(
    desktop,
):
    pb = real_free_tv(desktop)
    await pb.start()
    try:

        async def shown():
            return (await pb.status())["state"] == "playing"

        await until(shown)
        listed = await desktop.windows.toplevels()
        free_tv = [a for a, _ in listed if "tv.free.fr" in a]
        assert len(free_tv) == 1, (
            listed
        )  # un seul lancement : jamais une seconde fenêtre du même profil
        assert pb.app_id == free_tv[0]
        kinds = {a: ps.classify_window(a, t) for a, t in listed}
        assert sorted(kinds.values()) == ["dock", "freetv"], kinds
        assert ps.pick_window(listed, "dock") != ps.pick_window(listed, "freetv")
        # Le passage de l'une à l'autre, par l'identifiant exact : c'est ce que font le bouton, la manette et le geste de coin.
        assert await desktop.windows.focus("dock")
        assert await desktop.windows.focus("freetv")
        assert await desktop.windows.focus("dock")
        assert await desktop.windows.fullscreen(pb.app_id)
        assert await desktop.windows.maximize(pb.app_id)
        profile = desktop.tmp / "home" / ".config" / "mydock-freetv"
        assert (
            profile.stat().st_mode & 0o777
        ) == 0o700  # le profil de Free TV n'est lisible que par l'utilisateur de l'écran
        assert pb.user_agent is not None
        assert "x86_64" in pb.user_agent
        assert "aarch64" not in pb.user_agent
    finally:
        await pb.stop()


@needs_real_chromium
async def test_stopping_closes_only_free_tv_and_leaves_the_dock_in_place(desktop):
    pb = real_free_tv(desktop)
    await pb.start()

    async def shown():
        return (await pb.status())["state"] == "playing"

    await until(shown)
    await pb.stop()
    assert (
        pb.how_stopped == "fenêtre fermée"
    )  # le même chemin que la croix de la fenêtre : Chromium garde la connexion
    assert pb.finished
    assert pb.error() is None

    async def gone():
        return not any("tv.free.fr" in a for a, _ in await desktop.windows.toplevels())

    await until(gone, timeout=10)
    remaining = await desktop.windows.toplevels()
    assert [ps.classify_window(a, t) for a, t in remaining] == ["dock"]
    assert await desktop.windows.focus("dock")


@needs_real_chromium
async def test_a_second_launch_on_the_same_profile_is_never_made_by_a_new_free_tv(desktop):
    """Un Chromium resté ouvert sur ce profil est refermé avant le lancement : on n'ouvre jamais deux fenêtres."""
    first = real_free_tv(desktop)
    await first.start()

    async def shown():
        return (await first.status())["state"] == "playing"

    await until(shown)
    second = real_free_tv(desktop, profile=first._profile)
    await second.start()  # `kill_stale` ferme le premier : la fenêtre du second est la seule
    try:

        async def only_one():
            names = [a for a, _ in await desktop.windows.toplevels() if "tv.free.fr" in a]
            return len(names) == 1 and (await second.status())["state"] == "playing"

        await until(only_one)
        assert first.finished or not first._process or first._process.returncode is not None
    finally:
        await second.stop()
        await first.stop()
