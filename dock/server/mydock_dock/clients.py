"""Les paquets que le portail propose au téléchargement : un par type de PC.

Le paquet du Pi embarque le paquet du PC (`mydock-agent_<version>_all.deb`) dans son dossier de
clients. Ce module ne sert que ce qu'il y trouve, **sous le nom exact qu'il a lui-même reconnu** :
le nom demandé par un navigateur ne sert jamais à construire un chemin, il sert à retrouver une
entrée de la liste.

Ajouter un autre type de PC un jour = déposer un autre fichier dans le dossier et le décrire ici.
"""

from __future__ import annotations

import hashlib
import logging
import re
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger(__name__)

#: Un paquet Debian de l'agent : `mydock-agent_0.7.0_all.deb` (et rien d'autre : pas de chemin, pas d'espace).
DEB_RE = re.compile(r"^mydock-agent_(?P<version>[0-9]+(?:\.[0-9]+){1,3})_all\.deb$")
MAX_CLIENT_BYTES = 64 * 1024 * 1024  # un paquet plus gros n'est pas le nôtre
CHUNK = 1 << 20


@dataclass(frozen=True, slots=True)
class Client:
    id: str  # « deb » : identifiant stable pour la page
    label: str  # « Ubuntu, Debian et dérivés »
    file: str  # nom exact du fichier
    path: Path
    version: str
    size: int
    sha256: str

    def public(self) -> dict[str, str | int]:
        """Ce que la page et l'API publique disent de ce paquet (jamais le chemin sur le Pi)."""
        return {
            "id": self.id,
            "label": self.label,
            "file": self.file,
            "version": self.version,
            "size": self.size,
            "sha256": self.sha256,
        }


def version_key(version: str) -> tuple[int, ...]:
    return tuple(int(part) for part in version.split("."))


class ClientRegistry:
    """La liste des paquets du dossier, relue seulement quand le dossier change."""

    def __init__(self, directory: Path):
        self._directory = Path(directory)
        self._stamp: tuple[tuple[str, int, int], ...] | None = None
        self._clients: list[Client] = []
        self._hashes: dict[tuple[str, int, int], str] = {}

    def _candidates(self) -> list[tuple[Path, str, int, int]]:
        found = []
        try:
            entries = sorted(self._directory.iterdir())
        except OSError:
            return []
        for path in entries:
            match = DEB_RE.fullmatch(path.name)
            if match is None:
                continue
            try:
                info = path.stat()
            except OSError:
                continue
            if not path.is_file() or path.is_symlink() or not 0 < info.st_size <= MAX_CLIENT_BYTES:
                continue
            found.append((path, match["version"], info.st_size, info.st_mtime_ns))
        return found

    def _sha256(self, path: Path, key: tuple[str, int, int]) -> str | None:
        cached = self._hashes.get(key)
        if cached is not None:
            return cached
        digest = hashlib.sha256()
        try:
            with path.open("rb") as handle:
                while chunk := handle.read(CHUNK):
                    digest.update(chunk)
        except OSError:
            log.warning("Paquet client illisible : %s", path.name)
            return None
        self._hashes[key] = digest.hexdigest()
        return self._hashes[key]

    def scan(self) -> list[Client]:
        """Les paquets disponibles, le plus récent d'abord."""
        candidates = self._candidates()
        stamp = tuple((path.name, size, mtime) for path, _v, size, mtime in candidates)
        if stamp == self._stamp:
            return list(self._clients)
        clients = []
        for path, version, size, mtime in candidates:
            digest = self._sha256(path, (path.name, size, mtime))
            if digest is None:
                continue
            clients.append(
                Client("deb", "Ubuntu, Debian et dérivés", path.name, path, version, size, digest)
            )
        clients.sort(key=lambda c: version_key(c.version), reverse=True)
        self._clients, self._stamp = clients, stamp
        return list(clients)

    def latest(self) -> Client | None:
        found = self.scan()
        return found[0] if found else None

    def get(self, name: str) -> Client | None:
        """Le paquet de ce nom exact, s'il est dans la liste (jamais un chemin construit à partir de `name`)."""
        return next((c for c in self.scan() if c.file == name), None)
