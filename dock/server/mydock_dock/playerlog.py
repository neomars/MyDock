"""Les journaux de débogage du Divertissement : `/var/log/mydock/player.log` (la lecture) et `dock.log` (le dock).

L'assistant de lecture y écrit, pour chaque vidéo : ce que le dock lui a demandé, les commandes lancées (VLC, ffmpeg,
smbclient), tout ce que VLC et ffmpeg ont dit (VLC est lancé en `-vv`), et comment ça s'est terminé. C'est le fichier à
envoyer quand une vidéo ne se lance pas : `sudo cat /var/log/mydock/player.log`.

  * il tourne (1,5 Mo, trois anciens gardés) : il ne grossit jamais sans fin ;
  * **aucun mot de passe n'y entre** : le mot de passe du partage n'est jamais dans une ligne de commande, et tout ce qui
    ressemble à ce mot de passe est remplacé par `***` avant d'être écrit ;
  * `dock.log` est celui du serveur du dock : la liste des chaînes lue ou refusée, les partages parcourus, les lectures
    demandées et refusées. Il ne contient jamais le mot de passe non plus ;
  * le dossier `/var/log/mydock` est créé par l'installation (droits 1777 : l'assistant tourne sous l'utilisateur de
    la session de l'écran, pas sous celui du dock). S'il manque, le journal va dans le dossier d'exécution de la session.
"""

from __future__ import annotations

import contextlib
import logging
import logging.handlers
import os
import tempfile
from collections.abc import Mapping
from pathlib import Path

LOG_DIR = Path("/var/log/mydock")
LOG_NAME = "player.log"
ENV_PATH = "MYDOCK_PLAYER_LOG"
MAX_BYTES = 1_500_000
BACKUPS = 3
FAILURE_MARK = "ÉCHEC"

_logger = logging.getLogger("mydock.player.file")
_logger.setLevel(logging.DEBUG)
_logger.propagate = False
_secrets: set[str] = set()
_handler: logging.Handler | None = None
_path: Path | None = None


class _Scrubbing(logging.Formatter):
    """Remplace par `***` ce qui ne doit jamais être écrit (le mot de passe du partage)."""

    def format(self, record: logging.LogRecord) -> str:
        text = super().format(record)
        for secret in _secrets:
            text = text.replace(secret, "***")
        return text


def candidates(env: Mapping[str, str] | None = None) -> list[Path]:
    """Où le journal peut se trouver, dans l'ordre : le chemin demandé, le dossier du paquet, le dossier d'exécution."""
    env = os.environ if env is None else env
    paths: list[Path] = []
    if env.get(ENV_PATH):
        paths.append(Path(env[ENV_PATH]))
    paths.append(LOG_DIR / LOG_NAME)
    runtime = env.get("XDG_RUNTIME_DIR")
    paths.append(
        Path(runtime) / "mydock-player.log"
        if runtime
        else Path(tempfile.gettempdir()) / f"mydock-player-{os.getuid()}.log"
    )
    return paths


def attach(env: Mapping[str, str] | None = None) -> Path | None:
    """Ouvre le journal au premier emplacement où l'on peut écrire ; `None` si nulle part (le reste marche sans)."""
    global _handler, _path
    if _handler is not None:
        return _path
    for path in candidates(env):
        try:
            handler = logging.handlers.RotatingFileHandler(
                path, maxBytes=MAX_BYTES, backupCount=BACKUPS, encoding="utf-8"
            )
        except OSError:
            continue
        handler.setFormatter(_Scrubbing("%(asctime)s %(message)s", "%Y-%m-%d %H:%M:%S"))
        _logger.addHandler(handler)
        _handler, _path = handler, path
        with contextlib.suppress(OSError):
            path.chmod(
                0o644
            )  # lisible par le diagnostic (utilisateur « mydock »), jamais le mot de passe
        return path
    return None


def detach() -> None:
    global _handler, _path
    if _handler is not None:
        _logger.removeHandler(_handler)
        _handler.close()
    _handler = _path = None
    _secrets.clear()


def active() -> bool:
    return _handler is not None


def current_path() -> Path | None:
    return _path


def event(message: str) -> None:
    """Une ligne du journal (un événement de la lecture)."""
    if _handler is not None:
        _logger.info(message)


def line(prefix: str, text: str) -> None:
    """Une ligne dite par un programme (VLC, ffmpeg, smbclient), recopiée telle quelle."""
    if _handler is not None:
        _logger.info("%s| %s", prefix, text.rstrip("\r\n"))


def failure(job: int, message: str) -> None:
    """Une lecture ratée : la ligne que le diagnostic retrouve."""
    event(f"{FAILURE_MARK} lecture {job} : {message}")


def add_secret(value: str) -> None:
    """Un texte à ne jamais écrire. Les mots de moins de 4 caractères sont trop courts pour être effacés sans dégât."""
    if len(value) >= 4:
        _secrets.add(value)


def drop_secret(value: str) -> None:
    _secrets.discard(value)


def recent_failures(count: int = 3, env: Mapping[str, str] | None = None) -> list[str]:
    """Les dernières lectures ratées du journal (celui de la machine : le diagnostic n'est pas l'assistant)."""
    for path in candidates(env):
        try:
            with path.open("rb") as handle:
                handle.seek(0, os.SEEK_END)
                size = handle.tell()
                handle.seek(max(0, size - 400_000))
                text = handle.read().decode("utf-8", "replace")
        except OSError:
            continue
        found = [row for row in text.splitlines() if FAILURE_MARK in row]
        if found:
            return found[-count:]
    return []


def existing_path(env: Mapping[str, str] | None = None) -> Path | None:
    """Le premier journal qui existe et se lit (pour le diagnostic)."""
    for path in candidates(env):
        if os.access(path, os.R_OK) and path.is_file():
            return path
    return None


# --- le journal du serveur du dock (dock.log) ------------------------------------------------------------------------

DOCK_LOG_NAME = "dock.log"
DOCK_ENV_PATH = "MYDOCK_DOCK_LOG"
DOCK_LOGGER = "mydock_dock.media"
_dock_handler: logging.Handler | None = None
_dock_path: Path | None = None


def attach_dock(
    env: Mapping[str, str] | None = None, fallback_dir: Path | None = None
) -> Path | None:
    """Ouvre `dock.log` : les événements du Divertissement côté serveur (logger « mydock_dock.media »)."""
    global _dock_handler, _dock_path
    if _dock_handler is not None:
        return _dock_path
    env = os.environ if env is None else env
    paths: list[Path] = []
    if env.get(DOCK_ENV_PATH):
        paths.append(Path(env[DOCK_ENV_PATH]))
    paths.append(LOG_DIR / DOCK_LOG_NAME)
    if fallback_dir is not None:
        paths.append(fallback_dir / DOCK_LOG_NAME)
    logger = logging.getLogger(DOCK_LOGGER)
    for path in paths:
        try:
            handler = logging.handlers.RotatingFileHandler(
                path, maxBytes=MAX_BYTES, backupCount=BACKUPS, encoding="utf-8"
            )
        except OSError:
            continue
        handler.setFormatter(
            logging.Formatter("%(asctime)s %(levelname)s %(message)s", "%Y-%m-%d %H:%M:%S")
        )
        logger.setLevel(logging.INFO)
        logger.addHandler(handler)
        _dock_handler, _dock_path = handler, path
        with contextlib.suppress(OSError):
            path.chmod(0o644)
        return path
    return None


def detach_dock() -> None:
    global _dock_handler, _dock_path
    if _dock_handler is not None:
        logging.getLogger(DOCK_LOGGER).removeHandler(_dock_handler)
        _dock_handler.close()
    _dock_handler = _dock_path = None
