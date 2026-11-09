"""Économiseur d'écran : les vidéos que le Pi garde dans son dossier et sert à l'écran du dock.

Le dock ne contient aucune vidéo : l'interface a ses propres scènes animées. Les vidéos sont celles que l'on dépose dans
`<dossier d'état>/screensaver` (ou que `mydock-dock screensaver add` y télécharge) : des vues aériennes, des fonds
marins, des villes, à ton goût. Le navigateur du kiosque ne contacte jamais Internet : il les lit depuis le Pi.

Tout ce qui sort du dossier est borné : seules des extensions de vidéo, jamais un lien symbolique ni un sous-dossier,
des noms sans chemin ni caractère de contrôle, et le serveur ne sert que ce qu'il vient lui-même de lister.
"""

from __future__ import annotations

import contextlib
import os
import shutil
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO
from urllib.parse import urlsplit

from mydock_dock.net import build_opener

#: Les formats que Chromium lit sans réglage : MP4 (H.264) et WebM. Pas de .mov ni .mkv : à convertir (voir le README).
VIDEO_TYPES = {".mp4": "video/mp4", ".m4v": "video/mp4", ".webm": "video/webm"}
MAX_VIDEOS = 100
MAX_NAME = 150
#: Une vidéo d'économiseur pèse quelques dizaines de Mo en 1024×600 ; au-delà de 2 Go, c'est une erreur.
MAX_BYTES = 2 * 1024**3
FREE_MARGIN = 300 * 1024**2  # on ne remplit jamais la carte : cette marge reste libre
CHUNK = 1024 * 256
TIMEOUT = 20.0


class SaverError(Exception):
    """Un ajout ou un retrait de vidéo refusé ; le message est affichable."""


@dataclass(frozen=True)
class Video:
    name: str
    path: Path
    size: int
    mime: str


def safe_name(name: str) -> bool:
    """Un nom de fichier acceptable : pas de chemin, pas de caractère de contrôle, une extension de vidéo."""
    return (
        0 < len(name) <= MAX_NAME
        and not name.startswith(".")
        and "/" not in name
        and "\\" not in name
        and all(ord(c) >= 32 and ord(c) != 127 for c in name)
        and Path(name).suffix.lower() in VIDEO_TYPES
    )


def videos(directory: Path) -> list[Video]:
    """Les vidéos du dossier, par nom (aucune si le dossier n'existe pas)."""
    found: list[Video] = []
    try:
        entries = sorted(os.scandir(directory), key=lambda e: e.name.casefold())
    except OSError:
        return []
    for entry in entries:
        if len(found) >= MAX_VIDEOS or not safe_name(entry.name):
            continue
        try:
            if entry.is_symlink() or not entry.is_file(follow_symlinks=False):
                continue
            size = entry.stat(follow_symlinks=False).st_size
        except OSError:
            continue
        if size > 0:
            found.append(
                Video(
                    entry.name, Path(entry.path), size, VIDEO_TYPES[Path(entry.name).suffix.lower()]
                )
            )
    return found


def find(directory: Path, name: str) -> Video | None:
    """La vidéo de ce nom, si elle est dans la liste : on ne construit jamais un chemin à partir d'un nom reçu."""
    if not safe_name(name):
        return None
    return next((v for v in videos(directory) if v.name == name), None)


def clean_name(raw: str) -> str:
    """Un nom de fichier sûr tiré d'une adresse ou d'un chemin : les caractères douteux deviennent « _ »."""
    base = os.path.basename(urllib.parse.unquote(raw).replace("\\", "/")).strip()
    stem, dot, suffix = base.rpartition(".")
    stem = "".join(
        c if c.isalnum() or c in " ._()+-" else "_" for c in (stem if dot else base)
    ).strip(" .")
    return f"{stem[: MAX_NAME - 8]}{dot}{suffix.lower()}" if dot else stem[:MAX_NAME]


def _free_bytes(directory: Path) -> int:
    probe = directory
    while not probe.exists() and probe != probe.parent:
        probe = probe.parent
    return shutil.disk_usage(probe).free


def _store(directory: Path, name: str, source: BinaryIO, limit: int) -> Path:
    """Écrit `source` dans le dossier sous `name`, par un fichier provisoire (jamais une vidéo à moitié écrite)."""
    if not safe_name(name):
        raise SaverError(
            f"« {name} » n'est pas un nom de vidéo acceptable (extensions : {', '.join(sorted(VIDEO_TYPES))})."
        )
    try:
        directory.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise SaverError(
            f"Impossible d'ouvrir {directory} ({exc.strerror}) : lance la commande avec sudo."
        ) from None
    target = directory / name
    part = directory / f".{name}.part"
    written = 0
    try:
        with part.open("wb") as out:
            while chunk := source.read(CHUNK):
                written += len(chunk)
                if written > min(limit, MAX_BYTES):
                    raise SaverError("Cette vidéo est trop grosse (2 Go au plus).")
                out.write(chunk)
        if written == 0:
            raise SaverError("Le fichier est vide.")
        part.chmod(0o644)
        os.replace(part, target)
    except OSError as exc:
        raise SaverError(f"Écriture impossible dans {directory} ({exc.strerror}).") from None
    finally:
        with contextlib.suppress(OSError):
            part.unlink()
    return target


def add_file(directory: Path, source: Path, name: str | None = None) -> Path:
    """Copie une vidéo du disque dans le dossier de l'économiseur."""
    try:
        size = source.stat().st_size
        handle = source.open("rb")
    except OSError as exc:
        raise SaverError(f"Impossible de lire {source} ({exc.strerror or 'erreur'}).") from None
    with handle:
        if size + FREE_MARGIN > _free_bytes(directory):
            raise SaverError("Pas assez de place libre sur la carte du Pi.")
        return _store(directory, name or clean_name(source.name), handle, MAX_BYTES)


class HttpsOnly(urllib.request.HTTPRedirectHandler):
    """Suit les redirections, mais seulement vers du https."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # type: ignore[no-untyped-def]
        if urllib.parse.urlsplit(newurl).scheme != "https":
            raise SaverError("Le serveur renvoie vers une adresse qui n'est pas en https : refusé.")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def add_url(
    directory: Path,
    url: str,
    name: str | None = None,
    *,
    opener: Callable[[urllib.request.Request, float], BinaryIO] | None = None,
) -> Path:
    """Télécharge une vidéo (https seulement, 2 Go au plus) dans le dossier de l'économiseur."""
    parts = urllib.parse.urlsplit(url)
    if parts.scheme != "https" or not parts.hostname:
        raise SaverError("Seules les adresses en https sont acceptées.")
    request = urllib.request.Request(url, headers={"User-Agent": "mydock-dock"})

    def default_open(req: urllib.request.Request, timeout: float) -> BinaryIO:
        return build_opener(HttpsOnly()).open(req, timeout=timeout)  # type: ignore[return-value]

    try:
        response = (opener or default_open)(request, TIMEOUT)
    except (urllib.error.URLError, OSError, ValueError) as exc:
        reason = getattr(exc, "reason", exc)
        raise SaverError(f"Téléchargement impossible : {reason}.") from None
    with contextlib.closing(response):
        headers = getattr(response, "headers", None)
        kind = (headers.get("Content-Type", "") if headers else "").split(";")[0].strip().lower()
        if kind and not (kind.startswith("video/") or kind == "application/octet-stream"):
            raise SaverError(f"Ce n'est pas une vidéo (le serveur annonce « {kind} »).")
        try:
            length = int(headers.get("Content-Length", "0")) if headers else 0
        except ValueError:
            length = 0
        if length > MAX_BYTES:
            raise SaverError("Cette vidéo est trop grosse (2 Go au plus).")
        if length + FREE_MARGIN > _free_bytes(directory):
            raise SaverError("Pas assez de place libre sur la carte du Pi.")
        chosen = name or clean_name(parts.path)
        if Path(chosen).suffix.lower() not in VIDEO_TYPES:
            chosen += ".webm" if "webm" in kind else ".mp4"
        return _store(directory, chosen, response, MAX_BYTES)


def remove(directory: Path, name: str) -> None:
    video = find(directory, name)
    if video is None:
        raise SaverError(f"« {name} » n'est pas dans le dossier de l'économiseur.")
    try:
        video.path.unlink()
    except OSError as exc:
        raise SaverError(f"Impossible de supprimer {name} ({exc.strerror}).") from None


def human_size(size: int) -> str:
    units = ["o", "Ko", "Mo", "Go"]
    value = float(size)
    for unit in units:
        if value < 1024 or unit == units[-1]:
            return f"{value:.0f} {unit}" if unit == "o" else f"{value:.1f} {unit}".replace(".", ",")
        value /= 1024
    return f"{size} o"  # pragma: no cover


MAX_SOURCE_URL = 500


def valid_source_url(value: object) -> bool:
    """Une adresse de source acceptable : https, un nom de machine, ni identifiants ni caractère de contrôle ni espace."""
    if not isinstance(value, str) or not 10 <= len(value) <= MAX_SOURCE_URL:
        return False
    if any(ord(c) <= 32 or ord(c) == 127 for c in value):
        return False
    try:
        parts = urlsplit(value)
        parts.port  # noqa: B018 - lève ValueError si le port est absurde
    except ValueError:
        return False
    return (
        parts.scheme == "https"
        and bool(parts.hostname)
        and parts.username is None
        and parts.password is None
    )
