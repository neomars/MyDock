"""Retrogame : la liste des jeux d'arcade que MAME peut lancer d'après les ROMs du Pi.

C'est MAME lui-même qui dit ce qui est jouable, pas une liste écrite ici :

  1. les candidats sont les archives (`.zip`, `.7z`) et dossiers du dossier des ROMs dont le nom est un nom de jeu
     valide (`[a-z0-9_]`, 24 caractères au plus) ;
  2. `mame -verifyroms <noms>` dit lesquels sont complets (`good`, `best available`) — MAME n'écrit rien des autres ;
  3. `mame -listxml <noms>` donne leur nom complet, l'année et l'éditeur, et dit ce qui n'est pas un jeu (BIOS,
     périphérique, machine mécanique).

Le résultat est gardé dans `games.json` (état du dock), fichier par fichier : seuls les fichiers nouveaux ou modifiés
sont revérifiés, le dossier est relu à l'ouverture de la page et au bouton « Actualiser ». Les miniatures sont les
captures que la personne dépose dans le dossier `snap` (`<nom>.png`, ou `<nom>/0000.png` comme MAME les écrit).

Sécurité : l'écran n'envoie jamais un chemin ni une option, seulement l'identifiant d'un jeu de cette liste ; MAME est
lancé sans shell, avec des listes d'arguments, et son dossier de travail est celui du dock.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import logging
import os
import re
import shutil
import xml.etree.ElementTree as ET
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

NAME_RE = re.compile(r"^[a-z0-9_]{1,24}$")
MAME_DIRS = ("/usr/games", "/usr/local/games", "/usr/bin")
ROM_SUFFIXES = (".zip", ".7z")
MAX_CANDIDATES = 6000
CHUNK = 150  # noms par appel de MAME
VERIFY_TIMEOUT = 240.0
LISTXML_TIMEOUT = 120.0
MAX_OUTPUT = 48 * 1024 * 1024
SNAP_MAX_BYTES = 1024 * 1024
CACHE_VERSION = 1
GOOD = ("good", "best available")
_VERIFY_LINE = re.compile(r"^romset (\S+)(?: \[\S+\])? is (good|best available|bad)\b")
_SNAP_NAMES = ("{n}.png", "{n}.jpg", "{n}/0000.png")

#: (code retour, sortie standard, sortie d'erreur) d'un programme lancé sans shell.
Runner = Callable[[list[str], float], Awaitable[tuple[int, bytes, bytes]]]


def find_mame() -> str | None:
    """Le programme MAME. Debian le met dans /usr/games, absent du PATH d'un service."""
    found = shutil.which("mame")
    if found:
        return found
    for folder in MAME_DIRS:
        path = Path(folder) / "mame"
        if path.is_file() and os.access(path, os.X_OK):
            return str(path)
    return None


@dataclass(frozen=True)
class Game:
    """Un jeu que MAME sait lancer."""

    name: str
    title: str
    year: str
    maker: str
    status: str  # l'état de l'émulation selon MAME : good, imperfect, preliminary
    parent: str | None = None

    def public(self, *, snap: bool) -> dict[str, Any]:
        return {
            "id": self.name,
            "title": self.title,
            "year": self.year,
            "maker": self.maker,
            "status": self.status,
            "snap": snap,
        }


@dataclass(frozen=True)
class Machine:
    """Une entrée de `mame -listxml` : un jeu, mais aussi un BIOS ou un périphérique."""

    name: str
    title: str
    year: str
    maker: str
    status: str
    parent: str | None
    playable: bool  # ni BIOS, ni périphérique, ni machine mécanique, et MAME peut la lancer


def parse_verify(text: str) -> dict[str, str]:
    """`romset pacman [puckman] is good` -> {"pacman": "good"}. Les jeux absents ne sont pas écrits par MAME."""
    found: dict[str, str] = {}
    for line in text.splitlines():
        match = _VERIFY_LINE.match(line.strip())
        if match:
            found[match.group(1)] = match.group(2)
    return found


def parse_listxml(data: bytes) -> dict[str, Machine]:
    """Les machines de `mame -listxml <noms>`. Un XML illisible donne ce qui a été lu jusque-là."""
    machines: dict[str, Machine] = {}
    try:
        for _, element in ET.iterparse(_Bytes(data), events=("end",)):
            if element.tag != "machine":
                continue
            name = element.get("name", "")
            if NAME_RE.fullmatch(name):
                driver = element.find("driver")
                playable = (
                    element.get("isbios", "no") != "yes"
                    and element.get("isdevice", "no") != "yes"
                    and element.get("ismechanical", "no") != "yes"
                    and element.get("runnable", "yes") != "no"
                )
                machines[name] = Machine(
                    name=name,
                    title=" ".join((element.findtext("description") or name).split())[:120],
                    year=(element.findtext("year") or "").strip()[:12],
                    maker=" ".join((element.findtext("manufacturer") or "").split())[:80],
                    status=(driver.get("status", "good") if driver is not None else "good")[:16],
                    parent=element.get("cloneof"),
                    playable=playable,
                )
            element.clear()
    except ET.ParseError:
        pass
    return machines


class _Bytes:
    """Un fichier en mémoire pour `iterparse` (sans copie de plus)."""

    def __init__(self, data: bytes) -> None:
        self._data, self._at = data, 0

    def read(self, size: int = -1) -> bytes:
        end = len(self._data) if size < 0 else self._at + size
        chunk = self._data[self._at : end]
        self._at += len(chunk)
        return chunk


async def run_program(argv: list[str], timeout: float) -> tuple[int, bytes, bytes]:
    """Lance MAME sans shell, dans l'environnement du dock, et rend sa sortie (plafonnée)."""
    process = await asyncio.create_subprocess_exec(
        *argv,
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env={"LC_ALL": "C.UTF-8", "LANG": "C.UTF-8", "PATH": "/usr/bin:/bin"},
        start_new_session=True,
    )
    try:
        out, err = await asyncio.wait_for(process.communicate(), timeout)
    except TimeoutError:
        with contextlib.suppress(ProcessLookupError):
            process.kill()
        await process.wait()
        raise
    return process.returncode or 0, out[:MAX_OUTPUT], err[:65536]


class GameLibrary:
    """La liste des jeux jouables, son cache et sa mise à jour en tâche de fond."""

    def __init__(
        self,
        roms_dir: Callable[[], Path],
        retro_dir: Path,
        *,
        on_change: Callable[[], None] = lambda: None,
        mame: Callable[[], str | None] = find_mame,
        runner: Runner = run_program,
    ) -> None:
        self._roms_dir, self._retro_dir = roms_dir, retro_dir
        self._on_change, self._mame, self._run = on_change, mame, runner
        self.state = "idle"  # idle | scanning | error
        self.done = self.total = 0
        self.error: str | None = None
        self.skipped: list[str] = []
        self._entries: dict[str, dict[str, Any]] = {}
        self._task: asyncio.Task[None] | None = None
        self._snaps: tuple[int, set[str]] = (0, set())
        self._load()

    # --- ce que voit l'écran ---------------------------------------------------------------------

    @property
    def mame(self) -> str | None:
        return self._mame()

    def games(self) -> list[Game]:
        found = []
        for name, entry in self._entries.items():
            meta = entry.get("meta")
            if entry.get("verify") in GOOD and meta and meta.get("playable"):
                found.append(
                    Game(
                        name=name,
                        title=meta["title"],
                        year=meta["year"],
                        maker=meta["maker"],
                        status=meta["status"],
                        parent=meta.get("parent"),
                    )
                )
        found.sort(key=lambda g: (g.title.casefold(), g.name))
        return found

    def find(self, name: object) -> Game | None:
        """Le jeu de la liste qui porte ce nom (jamais un chemin : le nom doit être l'un de ceux que MAME a validés)."""
        if not isinstance(name, str) or not NAME_RE.fullmatch(name):
            return None
        return next((g for g in self.games() if g.name == name), None)

    def payload(self) -> dict[str, Any]:
        snaps = self._snap_names()
        folder = self._roms_dir()
        return {
            "mame": self.mame is not None,
            "roms_dir": str(folder),
            "readable": os.access(folder, os.R_OK | os.X_OK) if folder.is_dir() else False,
            "state": self.state,
            "done": self.done,
            "total": self.total,
            "error": self.error,
            "skipped": self.skipped[:5],
            "games": [g.public(snap=g.name in snaps) for g in self.games()],
        }

    # --- le cache --------------------------------------------------------------------------------

    @property
    def _cache_path(self) -> Path:
        return self._retro_dir / "games.json"

    def _load(self) -> None:
        try:
            data = json.loads(self._cache_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        entries = data.get("entries") if isinstance(data, dict) else None
        if (
            isinstance(data, dict)
            and data.get("version") == CACHE_VERSION
            and isinstance(entries, dict)
        ):
            self._entries = {
                name: entry
                for name, entry in entries.items()
                if NAME_RE.fullmatch(name) and isinstance(entry, dict)
            }

    def _save(self) -> None:
        try:
            self._retro_dir.mkdir(parents=True, exist_ok=True)
            temporary = self._cache_path.with_suffix(".tmp")
            temporary.write_text(
                json.dumps(
                    {"version": CACHE_VERSION, "entries": self._entries}, ensure_ascii=False
                ),
                encoding="utf-8",
            )
            os.replace(temporary, self._cache_path)
        except OSError as exc:
            log.warning("Retrogame : la liste n'a pas pu être gardée (%s)", exc)

    # --- les candidats ---------------------------------------------------------------------------

    def candidates(self) -> tuple[dict[str, list[int]], list[str]]:
        """{nom: [taille, date]} des archives et dossiers du dossier des ROMs, et les noms écartés."""
        found: dict[str, list[int]] = {}
        skipped: list[str] = []
        try:
            with os.scandir(self._roms_dir()) as entries:
                for entry in entries:
                    path = Path(entry.name)
                    name = path.stem if path.suffix.lower() in ROM_SUFFIXES else entry.name
                    is_set = (
                        entry.is_file() and path.suffix.lower() in ROM_SUFFIXES
                    ) or entry.is_dir()
                    if not is_set:
                        continue
                    if not NAME_RE.fullmatch(name):
                        skipped.append(entry.name)
                        continue
                    info = entry.stat()
                    found[name] = [info.st_size, int(info.st_mtime)]
                    if len(found) >= MAX_CANDIDATES:
                        break
        except OSError:
            return {}, []
        return found, sorted(skipped)

    def is_stale(self) -> bool:
        """Le dossier a-t-il changé depuis la dernière lecture ?"""
        found, _ = self.candidates()
        known = {name: entry.get("sig") for name, entry in self._entries.items()}
        return found != known

    # --- la mise à jour --------------------------------------------------------------------------

    def refresh(self, *, force: bool = False) -> None:
        """Relit le dossier en tâche de fond (sans attendre) ; rien si une lecture est déjà en cours."""
        if self._task is not None and not self._task.done():
            return
        self._task = asyncio.create_task(self._refresh(force), name="retrogame-scan")

    async def wait(self) -> None:
        if self._task is not None:
            await asyncio.shield(self._task)

    async def _refresh(self, force: bool) -> None:
        mame = self.mame
        found, self.skipped = await asyncio.to_thread(self.candidates)
        changed = [
            name
            for name, sig in found.items()
            if force
            or name not in self._entries
            or self._entries[name].get("sig") != sig
            or self._entries[name].get("verify") not in GOOD
        ]
        gone = [name for name in self._entries if name not in found]
        if not changed and not gone:
            self._finish("idle")
            return
        if mame is None:
            self._finish("error", "MAME est absent du Pi : sudo apt install mame")
            return
        for name in gone:
            del self._entries[name]
        self.state, self.error, self.done, self.total = "scanning", None, 0, len(changed)
        self._on_change()
        try:
            for start in range(0, len(changed), CHUNK):
                names = changed[start : start + CHUNK]
                await self._check(mame, names, found)
                self.done = min(self.total, start + len(names))
                self._on_change()
        except (OSError, TimeoutError, RuntimeError) as exc:
            log.warning("Retrogame : la liste des jeux n'a pas pu être lue (%s)", exc)
            self._save()
            self._finish("error", f"MAME n'a pas pu lire les ROMs ({exc.__class__.__name__}).")
            return
        self._save()
        self._finish("idle")

    def _finish(self, state: str, error: str | None = None) -> None:
        self.state, self.error = state, error
        self._on_change()

    async def _check(self, mame: str, names: list[str], found: dict[str, list[int]]) -> None:
        """Vérifie ces jeux, puis demande leur description à MAME."""
        work = self._retro_dir / "work"
        await asyncio.to_thread(work.mkdir, parents=True, exist_ok=True)
        base = [
            mame,
            "-rompath", str(self._roms_dir()),
            "-inipath", str(work / "ini"),
            "-homepath", str(work / "home"),
        ]  # fmt: skip
        code, out, _ = await self._run([*base, "-verifyroms", *names], VERIFY_TIMEOUT)
        verdicts = parse_verify(out.decode("utf-8", "replace"))
        if code != 0 and not verdicts and out:
            raise RuntimeError(f"code {code}")
        good = [name for name in names if verdicts.get(name) in GOOD]
        machines: dict[str, Machine] = {}
        if good:
            code, out, _ = await self._run([*base, "-listxml", *good], LISTXML_TIMEOUT)
            machines = parse_listxml(out)
        for name in names:
            verdict = verdicts.get(name, "missing")
            machine = machines.get(name) if verdict in GOOD else None
            self._entries[name] = {
                "sig": found[name],
                "verify": verdict,
                "meta": {
                    "title": machine.title,
                    "year": machine.year,
                    "maker": machine.maker,
                    "status": machine.status,
                    "parent": machine.parent,
                    "playable": machine.playable,
                }
                if machine
                else None,
            }

    # --- les miniatures --------------------------------------------------------------------------

    @property
    def snap_dir(self) -> Path:
        return self._retro_dir / "snap"

    @staticmethod
    def _image_type(path: Path) -> str | None:
        """Le type d'image d'un fichier de capture : un vrai PNG ou JPEG de 1 Mo au plus (jamais un lien)."""
        try:
            if path.is_symlink() or not path.is_file():
                return None
            if not 0 < path.stat().st_size <= SNAP_MAX_BYTES:
                return None
            with path.open("rb") as handle:
                head = handle.read(8)
        except OSError:
            return None
        if head.startswith(b"\x89PNG\r\n\x1a\n"):
            return "image/png"
        return "image/jpeg" if head.startswith(b"\xff\xd8\xff") else None

    def _snap_names(self) -> set[str]:
        """Les jeux qui ont une vraie capture (le dossier n'est relu que s'il a changé)."""
        try:
            stamp = self.snap_dir.stat().st_mtime_ns
        except OSError:
            return set()
        if stamp == self._snaps[0]:
            return self._snaps[1]
        names: set[str] = set()
        try:
            with os.scandir(self.snap_dir) as entries:
                for entry in entries:
                    stem = Path(entry.name).stem if entry.is_file() else entry.name
                    if NAME_RE.fullmatch(stem) and stem not in names and self._snap_of(stem):
                        names.add(stem)
        except OSError:
            pass
        self._snaps = (stamp, names)
        return names

    def _snap_of(self, name: str) -> tuple[Path, str] | None:
        for pattern in _SNAP_NAMES:
            path = self.snap_dir / pattern.format(n=name)
            kind = self._image_type(path)
            if kind is not None:
                return path, kind
        return None

    def snap_file(self, name: object) -> tuple[Path, str] | None:
        """La capture d'un jeu de la liste (jamais un chemin : le nom doit être celui d'un jeu que MAME a validé)."""
        game = self.find(name)
        return self._snap_of(game.name) if game is not None else None

    def signature(self) -> str:
        """Une empreinte du contenu du dossier (pour les essais et le diagnostic)."""
        found, _ = self.candidates()
        return hashlib.sha1(json.dumps(sorted(found.items())).encode()).hexdigest()[:12]
