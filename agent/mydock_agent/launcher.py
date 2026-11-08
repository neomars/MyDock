"""Lancer un programme graphique de la session, hors de l'agent.

Un programme lancé *par l'agent* en hériterait : il resterait dans son groupe de contrôle systemd (mettre
le paquet à jour, donc redémarrer l'agent, le fermerait avec lui) et surtout il hériterait de son bac à
sable. L'unité de l'agent porte `NoNewPrivileges=yes` : tout ce qu'elle lance, jusqu'au bout de l'arbre,
ne peut plus jamais obtenir de privilèges. Or un Snap démarre par `snap-confine`, un programme qui n'agit
que parce qu'il est « setuid root » : sans privilèges il échoue, et Spotify (Snap) ne se lançait pas depuis
le dock. Électron et Chromium (leur « bac à sable »), Flatpak (`bwrap`) y perdent aussi des moyens.

On demande donc au gestionnaire systemd *de l'utilisateur* de lancer le programme, comme un service à
lui (`systemd-run --user`) : ses enfants ne descendent plus de l'agent. Ce service range à son tour le
programme dans son propre « scope » (comme le fait GNOME pour les applications qu'il lance) : le service
s'arrête quand le lanceur (`gio launch`) a rendu la main, le programme, lui, continue dans son scope.
Sans `systemd-run`, ou si le gestionnaire est injoignable, on lance directement le programme, détaché de
l'agent (session à part) mais pas de son groupe, et il hérite alors des restrictions.

Jamais de shell : une liste d'arguments, et c'est l'agent qui la construit (le dock n'envoie que le
nom d'une action et l'identifiant d'un élément qu'il a lui-même listé).
"""

from __future__ import annotations

import asyncio
import logging
import os
import shutil
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Protocol

from mydock_agent.session import user_manager_environment

log = logging.getLogger(__name__)

SETTLE_SECONDS = 0.6  # un lanceur qui échoue le fait tout de suite ; un programme qui démarre dure

#: Ce que la session graphique fournit et que le programme lancé doit retrouver. Seulement ceux-là : le reste de
#: l'environnement de l'agent (NOTIFY_SOCKET, INVOCATION_ID, JOURNAL_STREAM…) n'a de sens que pour l'agent.
SESSION_VARIABLES = (
    "DISPLAY",
    "WAYLAND_DISPLAY",
    "XAUTHORITY",
    "XDG_CURRENT_DESKTOP",
    "XDG_SESSION_TYPE",
    "XDG_SESSION_DESKTOP",
    "XDG_DATA_DIRS",
    "XDG_RUNTIME_DIR",
    "DBUS_SESSION_BUS_ADDRESS",
)


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


def through_user_manager(
    systemd_run: str, command: Sequence[str], label: str, env: Mapping[str, str]
) -> list[str]:
    """La ligne de commande qui fait lancer `command` par le gestionnaire systemd de l'utilisateur.

    Dehors, un service transitoire (`--wait` : on apprend s'il échoue tout de suite, `--collect` : il
    disparaît tout seul) ; dedans, le scope du programme. `systemd-run` ne lit pas l'environnement de
    celui qui l'appelle : on transmet donc, une à une, les variables de la session.
    """
    setenv = [f"--setenv={name}={env[name]}" for name in SESSION_VARIABLES if env.get(name)]
    home = env.get("HOME") or str(Path.home())
    scope = [
        systemd_run,
        "--user",
        "--scope",
        "--quiet",
        "--collect",
        f"--description=MyDock : {label}",
        "--",
        *command,
    ]
    return [
        systemd_run,
        "--user",
        "--quiet",
        "--collect",
        "--wait",
        f"--description=MyDock : lancement de {label}",
        f"--working-directory={home}",
        *setenv,
        "--",
        *scope,
    ]


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
        systemd_run = shutil.which("systemd-run", path=env.get("PATH"))
        if systemd_run is not None:
            via_manager = through_user_manager(systemd_run, command, label, env)
            if await self._launch(via_manager, env, label, manager=True):
                return
            # Le gestionnaire de l'utilisateur n'a pas pu être joint : mieux vaut un programme lancé
            # directement (avec les restrictions de l'agent) que pas de programme du tout.
        await self._launch(command, env, label, manager=False)

    async def _launch(
        self, command: Sequence[str], env: Mapping[str, str], label: str, *, manager: bool
    ) -> bool:
        """Lance `command`. Faux seulement si `systemd-run` lui-même a échoué (gestionnaire injoignable)."""
        try:
            process = await asyncio.create_subprocess_exec(
                *command,
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.PIPE if manager else asyncio.subprocess.DEVNULL,
                env=dict(env),
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
            return True  # il tourne : c'est lancé
        if code == 0:
            return True
        if manager and (reason := await self._manager_error(process)).startswith("Failed to"):
            log.warning(
                "systemd-run --user inutilisable (%s) : « %s » est lancé directement.",
                reason,
                label,
            )
            return False
        raise LaunchError(f"Le lancement de « {label} » a échoué (code {code}).")

    @staticmethod
    async def _manager_error(process: asyncio.subprocess.Process) -> str:
        """La première ligne de ce que `systemd-run` a écrit (ses propres erreurs commencent par « Failed to »)."""
        if process.stderr is None:
            return ""
        try:
            data = await asyncio.wait_for(process.stderr.read(2000), 1.0)
        except (TimeoutError, OSError):
            return ""
        return next(
            (line.strip() for line in data.decode(errors="replace").splitlines() if line.strip()),
            "",
        )

    @staticmethod
    async def _reap(process: asyncio.subprocess.Process, label: str) -> None:
        await (
            process.communicate()
        )  # vide aussi ce que `systemd-run` écrirait encore, puis attend la fin
        if process.returncode != 0:
            log.info("« %s » s'est terminé avec le code %s", label, process.returncode)
