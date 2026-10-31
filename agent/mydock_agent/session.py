"""Détection de la session graphique (Wayland ou X11) dans laquelle tourne l'agent.

L'agent est un service systemd *utilisateur* : il hérite de l'environnement graphique que
GNOME importe dans le gestionnaire utilisateur (WAYLAND_DISPLAY, DISPLAY, DBUS_SESSION_BUS_ADDRESS).
"""

from __future__ import annotations

import os
import shutil
import subprocess
from collections.abc import Mapping
from dataclasses import dataclass

GRAPHICAL_VARS = ("WAYLAND_DISPLAY", "DISPLAY", "XAUTHORITY", "DBUS_SESSION_BUS_ADDRESS")
# La langue de la session : sans elle, une application lancée par l'agent s'ouvrirait en anglais.
LOCALE_VARS = ("LANG", "LANGUAGE", "LC_ALL", "LC_MESSAGES")


@dataclass(frozen=True)
class SessionInfo:
    type: str  # "wayland" | "x11" | "tty" | "unknown"
    desktop: str | None
    wayland_display: str | None
    display: str | None

    def to_dict(self) -> dict[str, str | None]:
        return {"type": self.type, "desktop": self.desktop}


def _loginctl_session_type() -> str | None:
    """Dernier recours quand l'environnement ne dit rien : demander à logind."""
    if not shutil.which("loginctl"):
        return None
    try:
        out = subprocess.run(
            ["loginctl", "show-user", str(os.getuid()), "-p", "Display", "--value"],
            capture_output=True,
            text=True,
            timeout=2,
            check=False,
        ).stdout.strip()
        if not out:
            return None
        kind = subprocess.run(
            ["loginctl", "show-session", out, "-p", "Type", "--value"],
            capture_output=True,
            text=True,
            timeout=2,
            check=False,
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return None
    return kind or None


def detect_session(env: Mapping[str, str] | None = None, *, ask_logind: bool = True) -> SessionInfo:
    env = os.environ if env is None else env
    wayland = env.get("WAYLAND_DISPLAY") or None
    display = env.get("DISPLAY") or None
    kind = (env.get("XDG_SESSION_TYPE") or "").lower()
    if kind not in {"wayland", "x11", "tty", "mir"}:
        if wayland:
            kind = "wayland"
        elif display:
            kind = "x11"
        else:
            kind = (_loginctl_session_type() if ask_logind else None) or "unknown"
    desktop = env.get("XDG_CURRENT_DESKTOP") or None
    if desktop:
        desktop = desktop.split(":")[-1] if desktop.startswith("ubuntu:") else desktop.split(":")[0]
    return SessionInfo(kind, desktop, wayland, display)


def user_manager_environment() -> dict[str, str] | None:
    """Variables de session vues par `systemctl --user` (None si systemd utilisateur absent).

    Ce sont l'écran (Wayland, X11, bus de session), le type de session et la langue.
    """
    if not shutil.which("systemctl"):
        return None
    try:
        result = subprocess.run(
            ["systemctl", "--user", "show-environment"],
            capture_output=True,
            text=True,
            timeout=3,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    found: dict[str, str] = {}
    for line in result.stdout.splitlines():
        name, _, value = line.partition("=")
        if (
            name in GRAPHICAL_VARS
            or name in LOCALE_VARS
            or name in {"XDG_SESSION_TYPE", "XDG_CURRENT_DESKTOP"}
        ):
            found[name] = value
    return found
