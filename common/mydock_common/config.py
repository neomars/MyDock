"""Lecture et écriture des fichiers de configuration privés (TOML, droits 600).

Les jetons et les clés d'API ne vivent jamais dans le dépôt : ils sont dans ces fichiers,
et on refuse de les lire s'ils sont accessibles à d'autres utilisateurs.
"""

from __future__ import annotations

import contextlib
import os
import stat
import tempfile
import tomllib
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import tomli_w


class ConfigError(Exception):
    """Problème de configuration, avec un message déjà prêt à être affiché."""


def check_private_file(path: Path) -> None:
    """Refuse un fichier lisible par le groupe ou les autres, ou appartenant à quelqu'un d'autre."""
    try:
        info = path.stat()
    except FileNotFoundError:
        raise ConfigError(f"Fichier de configuration introuvable : {path}") from None
    except PermissionError:
        raise ConfigError(
            f"Accès refusé à {path} : il doit appartenir à l'utilisateur qui lance le service "
            f"(chown) et être en droits 600."
        ) from None
    mode = stat.S_IMODE(info.st_mode)
    if mode & 0o077:
        raise ConfigError(
            f"{path} est accessible à d'autres utilisateurs (droits {mode:03o}). "
            f"Il contient un secret : corrige avec  chmod 600 {path}"
        )
    if hasattr(os, "getuid") and info.st_uid != os.getuid():
        raise ConfigError(
            f"{path} appartient à un autre utilisateur (uid {info.st_uid}) que celui du service "
            f"(uid {os.getuid()})."
        )


def load_toml(path: Path | str, *, private: bool = True) -> dict[str, Any]:
    path = Path(path)
    if private:
        check_private_file(path)
    try:
        with path.open("rb") as handle:
            return tomllib.load(handle)
    except FileNotFoundError:
        raise ConfigError(f"Fichier de configuration introuvable : {path}") from None
    except PermissionError:
        raise ConfigError(f"Lecture impossible de {path} (permissions).") from None
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"{path} n'est pas un TOML valide : {exc}") from None


def write_toml_private(path: Path | str, data: Mapping[str, Any], *, header: str = "") -> None:
    """Écrit le fichier de façon atomique, en droits 600."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    comment = "".join(f"# {line}\n" for line in header.strip().splitlines()) if header else ""
    text = (comment + "\n" if comment else "") + tomli_w.dumps(dict(data))
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(tmp_name, 0o600)
        os.replace(tmp_name, path)
    except BaseException:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(tmp_name)
        raise


def ensure_private_dir(path: Path | str) -> Path:
    """Crée le dossier en droits 700 s'il n'existe pas (ne touche pas aux droits d'un existant)."""
    path = Path(path)
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    return path
