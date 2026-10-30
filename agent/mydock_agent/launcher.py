"""Lancer un programme graphique de la session, hors de l'agent.

Un programme lancé par l'agent resterait dans le groupe de contrôle systemd de l'agent : mettre le
paquet à jour (donc redémarrer l'agent) le fermerait avec lui. `systemd-run --user --scope` le
place dans son propre « scope », comme le fait GNOME pour les applications qu'il lance. Sans
`systemd-run`, on lance directement le programme, détaché de l'agent (session à part) mais pas de
son groupe.

Jamais de shell : une liste d'arguments, et c'est l'agent qui la construit (le dock n'envoie que le
nom d'une action et l'identifiant d'un élément qu'il a lui-même listé).
"""

from __future__ import annotations

import asyncio
import logging
import os
import shutil
from collections.abc import Mapping, Sequence
from typing import Protocol

from mydock_agent.session import user_manager_environment

log = logging.getLogger(__name__)

SETTLE_SECONDS = 0.6  # un lanceur qui échoue le fait tout de suite ; un programme qui démarre dure


class LaunchError(Exception):
    """Le programme n'a pas pu être lancé ; le message est affichable à l'écran."""


class Launcher(Protocol):
    async def start(self, argv: Sequence[str], *, label: str) -> None:
        """Lance `argv` et rend la main sans attendre la fin du programme."""


def graphical_environment(base: Mapping[str, str] | None = None) -> dict[str, str]:
    """L'environnement d'un programme de la session : celui de l'agent, complété par celui de systemd.

    Le gestionnaire utilisateur de systemd connaît l'écran (WAYLAND_DISPLAY, DISPLAY) même quand le
    service de l'agent a démarré avant qu'il soit importé.
    """
    env = dict(os.environ if base is None else base)
    for name, value in (user_manager_environment() or {}).items():
        env.setdefault(name, value)
    return env


class SystemLauncher:
    def __init__(self, env: Mapping[str, str] | None = None, *, settle: float = SETTLE_SECONDS):
        self._env = dict(env) if env is not None else None
        self._settle = settle
        self._tasks: set[asyncio.Task[None]] = set()

    def _environment(self) -> dict[str, str]:
        return dict(self._env) if self._env is not None else graphical_environment()

    async def start(self, argv: Sequence[str], *, label: str) -> None:
        env = self._environment()
        program = shutil.which(argv[0], path=env.get("PATH"))
        if program is None:
            raise LaunchError(f"« {argv[0]} » est introuvable sur ce PC.")
        command = [program, *argv[1:]]
        scope = shutil.which("systemd-run", path=env.get("PATH"))
        if scope is not None:
            command = [
                scope,
                "--user",
                "--scope",
                "--quiet",
                "--collect",
                f"--description=MyDock : {label}",
                "--",
                *command,
            ]
        try:
            process = await asyncio.create_subprocess_exec(
                *command,
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
                env=env,
                start_new_session=True,
            )
        except OSError as exc:
            raise LaunchError(
                f"Impossible de lancer « {label} » ({exc.__class__.__name__})."
            ) from None
        try:
            code = await asyncio.wait_for(process.wait(), self._settle)
        except TimeoutError:
            task = asyncio.create_task(self._reap(process, label))
            self._tasks.add(task)
            task.add_done_callback(self._tasks.discard)
            return  # il tourne : c'est lancé
        if code != 0:
            raise LaunchError(f"Le lancement de « {label} » a échoué (code {code}).")

    @staticmethod
    async def _reap(process: asyncio.subprocess.Process, label: str) -> None:
        code = await process.wait()
        if code != 0:
            log.info("« %s » s'est terminé avec le code %s", label, code)
