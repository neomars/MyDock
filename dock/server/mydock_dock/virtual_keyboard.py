"""Le clavier virtuel de Free TV : wvkbd, un clavier à l'écran pour les fenêtres Wayland.

Free TV demande un identifiant et un mot de passe la première fois (la connexion reste ensuite dans le profil), et il n'y a ni
clavier ni souris sur le dock : sans clavier à l'écran, impossible de s'y connecter. Le clavier de l'interface du dock, lui,
vit dans la page du dock : il ne peut pas écrire dans la fenêtre de Free TV.

`wvkbd` est un clavier qui parle le protocole « virtual-keyboard » de Wayland : ses touches arrivent dans la fenêtre qui a le
focus, comme celles d'un vrai clavier. Il se montre et se cache par signal (SIGUSR2 et SIGUSR1) ; on le lance donc une
fois, visible (lui envoyer SIGUSR2 avant qu'il soit prêt le tuerait), et on le cache ensuite au lieu de le fermer.

Rien ne vient du dock que « montre » ou « cache » : la ligne de commande est figée.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import shutil
import signal
from collections.abc import Callable
from typing import Any

from mydock_dock import playerlog

log = logging.getLogger("mydock.player")

#: Le clavier tactile (touches larges) d'abord, puis celui de bureau.
PROGRAMS = ("wvkbd-mobintl", "wvkbd-deskintl")
#: Hauteur du clavier (pixels, écran en paysage) : la moitié de l'écran de 600 px, pour garder la page de Free TV visible.
HEIGHT = 300
START_WAIT = 0.6


def find_keyboard(which: Callable[[str], str | None] = shutil.which) -> str | None:
    for program in PROGRAMS:
        if found := which(program):
            return found
    return None


class VirtualKeyboard:
    def __init__(
        self, env: dict[str, str], *, find: Callable[[], str | None] = find_keyboard
    ) -> None:
        self._env = env
        self._find = find
        self._process: asyncio.subprocess.Process | None = None
        self.visible = False

    @property
    def available(self) -> bool:
        return self._find() is not None

    @property
    def running(self) -> bool:
        return self._process is not None and self._process.returncode is None

    def command_line(self, program: str) -> list[str]:
        return [program, "-L", str(HEIGHT), "-H", str(HEIGHT), "--fn", "DejaVu Sans 18"]

    async def _start(self) -> bool:
        program = self._find()
        if program is None:
            return False
        try:
            self._process = await asyncio.create_subprocess_exec(
                *self.command_line(program),
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
                env=self._env,
                start_new_session=True,
            )
        except OSError as exc:
            playerlog.event(f"clavier : {program} ne démarre pas ({exc.strerror or exc})")
            return False
        await asyncio.sleep(START_WAIT)  # le temps d'installer ses gestionnaires de signaux
        if not self.running:
            playerlog.event(
                f"clavier : {program} s'est arrêté tout de suite (compositeur sans clavier virtuel ?)"
            )
            return False
        playerlog.event(f"clavier : {program} lancé")
        return True

    def _signal(self, sig: signal.Signals) -> None:
        if self._process is not None:
            with contextlib.suppress(ProcessLookupError, PermissionError):
                os.kill(self._process.pid, sig)

    async def show(self) -> bool:
        """Montre le clavier ; faux si on ne peut pas (pas de wvkbd, ou le compositeur n'en veut pas)."""
        if not self.running:
            self.visible = False
            if not await self._start():
                return False
            self.visible = True  # il démarre visible
            return True
        if not self.visible:
            self._signal(signal.SIGUSR2)
            self.visible = True
        return True

    async def hide(self) -> None:
        if self.running and self.visible:
            self._signal(signal.SIGUSR1)
        self.visible = False

    async def toggle(self) -> bool:
        if self.visible and self.running:
            await self.hide()
            return True
        return await self.show()

    async def close(self) -> None:
        """Ferme le clavier (Free TV est fermé : le clavier n'a plus rien à écrire)."""
        process, self._process = self._process, None
        self.visible = False
        if process is None or process.returncode is not None:
            return
        with contextlib.suppress(ProcessLookupError, PermissionError):
            os.killpg(process.pid, signal.SIGTERM)
        try:
            await asyncio.wait_for(process.wait(), 2)
        except TimeoutError:
            with contextlib.suppress(ProcessLookupError, PermissionError):
                os.killpg(process.pid, signal.SIGKILL)
            await process.wait()


class NoKeyboard:
    """Le mode démo : aucun clavier réel, mais l'état « visible » se suit comme avec le vrai (pour les captures)."""

    available = True
    running = True

    def __init__(self, *_: Any, **__: Any) -> None:
        self.visible = False

    async def show(self) -> bool:
        self.visible = True
        return True

    async def hide(self) -> None:
        self.visible = False

    async def toggle(self) -> bool:
        self.visible = not self.visible
        return True

    async def close(self) -> None:
        self.visible = False
