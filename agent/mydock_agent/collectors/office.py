"""Thème « office » : LibreOffice, pour créer un document et rouvrir un document récent.

Les documents récents viennent de la liste que LibreOffice tient lui-même (celle de son écran de
démarrage : `registrymodifications.xcu`, groupe `PickList`) ; à défaut, de la liste récente du
bureau (`recently-used.xbel`) réduite aux documents bureautiques. L'agent ne lit que ces deux
fichiers de l'utilisateur, jamais le contenu des documents.

Le dock ne peut rouvrir qu'un document de la liste, par son identifiant, et que s'il existe encore ;
le chemin est reconstruit par l'agent, jamais envoyé par le dock.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import shutil
import urllib.parse
import xml.etree.ElementTree as ET
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field

from mydock_agent.hub import Provider, Publish
from mydock_agent.launcher import Launcher, LaunchError, SystemLauncher
from mydock_common.actions import ActionContext, ActionError, ActionRegistry

log = logging.getLogger(__name__)

MAX_XML_BYTES = 24 * 1024 * 1024  # les vignettes des documents récents en base64 pèsent lourd
MAX_RECENT = 12
REFRESH_SECONDS = 10.0
FLATPAK_ID = "org.libreoffice.LibreOffice"
OOR = "{http://openoffice.org/2001/registry}"

KIND_ARGUMENT = {"writer": "--writer", "calc": "--calc", "impress": "--impress"}
EXTENSIONS = {
    "writer": {".odt", ".ott", ".fodt", ".doc", ".docx", ".dot", ".dotx", ".rtf", ".txt", ".wpd"},
    "calc": {".ods", ".ots", ".fods", ".xls", ".xlsx", ".xlt", ".xltx", ".csv", ".tsv"},
    "impress": {".odp", ".otp", ".fodp", ".ppt", ".pptx", ".pps", ".ppsx", ".pot", ".potx"},
    "draw": {".odg", ".otg", ".fodg", ".vsd", ".vsdx", ".pdf"},
}
FILTER_WORDS = (
    ("writer", ("writer", "word", "text")),
    ("calc", ("calc", "excel")),
    ("impress", ("impress", "powerpoint")),
    ("draw", ("draw", "pdf")),
)
OFFICE_MIME_PREFIXES = (
    "application/vnd.oasis.opendocument.",
    "application/vnd.openxmlformats-officedocument.",
    "application/msword",
    "application/vnd.ms-",
    "application/rtf",
    "text/csv",
)


def kind_of(path: Path, filter_name: str | None = None) -> str:
    """Le module de LibreOffice qui ouvre ce document (writer, calc, impress, draw ou other)."""
    extension = path.suffix.lower()
    for kind, extensions in EXTENSIONS.items():
        if extension in extensions:
            return kind
    lowered = (filter_name or "").lower()
    for kind, words in FILTER_WORDS:
        if any(word in lowered for word in words):
            return kind
    return "other"


def recent_id(url: str) -> str:
    return hashlib.sha1(url.encode(), usedforsecurity=False).hexdigest()[:16]


def url_to_path(url: str) -> Path | None:
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme != "file" or parsed.netloc not in ("", "localhost"):
        return None
    path = Path(urllib.parse.unquote(parsed.path))
    return path if path.is_absolute() else None


@dataclass(frozen=True)
class Recent:
    id: str
    path: Path
    name: str
    kind: str
    modified: float | None


# --- les deux sources de documents récents ------------------------------------------------------


def _parse(path: Path) -> ET.Element | None:
    try:
        if path.stat().st_size > MAX_XML_BYTES:
            return None
        return ET.parse(path).getroot()
    except (OSError, ET.ParseError):
        return None


def _prop(node: ET.Element, name: str) -> str | None:
    for prop in node.findall("prop"):
        if prop.get(f"{OOR}name") == name:
            value = prop.find("value")
            return value.text if value is not None and value.text else None
    return None


def read_libreoffice_history(path: Path) -> list[tuple[str, str | None]]:
    """(adresse, nom du filtre) des documents de la liste de LibreOffice, du plus récent au plus ancien."""
    root = _parse(path)
    if root is None:
        return []
    filters: dict[str, str | None] = {}
    order: dict[int, str] = {}
    for item in root.iter("item"):
        where = item.get(f"{OOR}path", "")
        if where.endswith("['PickList']/ItemList"):
            for node in item.findall("node"):
                if url := node.get(f"{OOR}name"):
                    filters[url] = _prop(node, "Filter")
        elif where.endswith("['PickList']/OrderList"):
            for node in item.findall("node"):
                position, reference = node.get(f"{OOR}name", ""), _prop(node, "HistoryItemRef")
                if position.isdigit() and reference:
                    order[int(position)] = reference
    urls = [order[i] for i in sorted(order) if order[i] in filters]
    urls += [url for url in filters if url not in urls]
    return [(url, filters[url]) for url in urls]


def read_recently_used(path: Path) -> list[tuple[str, str | None]]:
    """Les documents bureautiques de la liste récente du bureau (GTK), du plus récent au plus ancien."""
    root = _parse(path)
    if root is None:
        return []
    found: list[tuple[str, str, str | None]] = []
    for bookmark in root.iter("bookmark"):
        href = bookmark.get("href")
        if not href:
            continue
        mimes = [e.get("type", "") for e in bookmark.iter() if e.tag.endswith("}mime-type")]
        apps = [e.get("name", "") for e in bookmark.iter() if e.tag.endswith("}application")]
        if any(m.startswith(OFFICE_MIME_PREFIXES) for m in mimes) or any(
            "libreoffice" in a.lower() for a in apps
        ):
            found.append((bookmark.get("modified") or "", href, None))
    found.sort(reverse=True)  # les dates ISO se classent comme du texte
    return [(href, flt) for _, href, flt in found]


def libreoffice_profiles(home: Path) -> list[Path]:
    return [
        home / ".config" / "libreoffice" / "4" / "user" / "registrymodifications.xcu",
        home
        / ".var"
        / "app"
        / FLATPAK_ID
        / "config"
        / "libreoffice"
        / "4"
        / "user"
        / "registrymodifications.xcu",
        home
        / "snap"
        / "libreoffice"
        / "current"
        / ".config"
        / "libreoffice"
        / "4"
        / "user"
        / "registrymodifications.xcu",
    ]


def recently_used_files(home: Path) -> list[Path]:
    return [
        home / ".local" / "share" / "recently-used.xbel",
        home / ".var" / "app" / FLATPAK_ID / "data" / "recently-used.xbel",
    ]


def collect_recent(profiles: Sequence[Path], xbels: Sequence[Path], home: Path) -> list[Recent]:
    """Les documents récents existants : la liste de LibreOffice d'abord, puis celle du bureau."""
    entries: list[tuple[str, str | None]] = []
    for profile in sorted((p for p in profiles if p.is_file()), key=_mtime, reverse=True):
        entries += read_libreoffice_history(profile)
    for xbel in sorted((p for p in xbels if p.is_file()), key=_mtime, reverse=True):
        entries += read_recently_used(xbel)
    recents: list[Recent] = []
    seen: set[Path] = set()
    for url, filter_name in entries:
        path = url_to_path(url)
        if path is None or path in seen:
            continue
        try:
            if not path.is_file():
                continue
            modified: float | None = path.stat().st_mtime
        except OSError:
            continue
        seen.add(path)
        name = " ".join(path.name.split())[:100] or path.name
        recents.append(Recent(recent_id(url), path, name, kind_of(path, filter_name), modified))
        if len(recents) >= MAX_RECENT:
            break
    return recents


def _mtime(path: Path) -> float:
    try:
        return path.stat().st_mtime
    except OSError:
        return 0.0


def shorten(path: Path, home: Path) -> str:
    """Le dossier d'un document, lisible : « ~/Documents/Factures »."""
    folder = path.parent
    try:
        return "~/" + folder.relative_to(home).as_posix() if folder != home else "~"
    except ValueError:
        return str(folder)


# --- le programme -------------------------------------------------------------------------------


def detect_command(which: Callable[[str], str | None], flatpak_present: bool) -> list[str] | None:
    """La commande qui lance LibreOffice (paquet, Snap, ou Flatpak), ou None s'il n'est pas là."""
    for name in ("libreoffice", "soffice"):
        if which(name):
            return [name]
    if flatpak_present and which("flatpak"):
        return ["flatpak", "run", FLATPAK_ID]
    return None


# --- le fournisseur ----------------------------------------------------------------------------


class OfficeBackend(Protocol):
    async def new(self, kind: str) -> dict[str, Any]: ...
    async def open(self, recent: str) -> dict[str, Any]: ...


class OfficeProvider(Provider):
    topic = "office"
    lazy = True

    def __init__(
        self,
        *,
        home: Path | None = None,
        profiles: Sequence[Path] | None = None,
        xbels: Sequence[Path] | None = None,
        launcher: Launcher | None = None,
        which: Callable[[str], str | None] = shutil.which,
        flatpak_present: Callable[[], bool] | None = None,
        interval: float = REFRESH_SECONDS,
    ):
        self._home = home or Path.home()
        self._profiles = (
            list(profiles) if profiles is not None else libreoffice_profiles(self._home)
        )
        self._xbels = list(xbels) if xbels is not None else recently_used_files(self._home)
        self._launcher: Launcher = launcher or SystemLauncher()
        self._which = which
        self._flatpak = flatpak_present or (
            lambda: (
                (self._home / ".local" / "share" / "flatpak" / "app" / FLATPAK_ID).exists()
                or Path("/var/lib/flatpak/app", FLATPAK_ID).exists()
            )
        )
        self._interval = interval
        self._recent: list[Recent] = []
        self._command: list[str] | None = None
        self._signature: tuple[Any, ...] | None = None

    @property
    def command(self) -> list[str] | None:
        return self._command

    @property
    def recent(self) -> list[Recent]:
        return list(self._recent)

    @property
    def sources(self) -> list[Path]:
        return [p for p in (*self._profiles, *self._xbels) if p.is_file()]

    def _stamp(self) -> tuple[Any, ...]:
        return tuple((str(p), _mtime(p)) for p in (*self._profiles, *self._xbels))

    def _load(self) -> None:
        self._command = detect_command(self._which, self._flatpak())
        self._recent = collect_recent(self._profiles, self._xbels, self._home)
        self._signature = self._stamp()

    async def refresh(self) -> None:
        # Le contenu d'un document peut changer sans toucher à la liste : on revérifie l'existence
        # des fichiers à chaque lecture, mais on ne relit les listes que si elles ont bougé.
        if self._signature is not None and self._stamp() == self._signature:
            self._recent = [r for r in self._recent if r.path.is_file()]
            return
        await asyncio.to_thread(self._load)

    def snapshot(self) -> dict[str, Any]:
        if self._command is None:
            return {
                "unavailable": "LibreOffice n'est pas installé sur ce PC.",
                "kinds": [],
                "recent": [],
            }
        return {
            "kinds": list(KIND_ARGUMENT),
            "recent": [
                {
                    "id": r.id,
                    "name": r.name,
                    "kind": r.kind,
                    "where": shorten(r.path, self._home),
                    "modified": r.modified,
                }
                for r in self._recent
            ],
        }

    async def run(self, publish: Publish) -> None:
        while True:
            await self.refresh()
            await publish(self.snapshot())
            await asyncio.sleep(self._interval)

    # --- actions ---

    async def _start(self, extra: Sequence[str], label: str) -> None:
        assert self._command is not None
        try:
            await self._launcher.start([*self._command, *extra], label=label)
        except LaunchError as exc:
            raise ActionError("launch_failed", str(exc)) from None

    async def new(self, kind: str) -> dict[str, Any]:
        await self.refresh()
        if self._command is None:
            raise ActionError("unavailable", "LibreOffice n'est pas installé sur ce PC.")
        titles = {"writer": "Writer", "calc": "Calc", "impress": "Impress"}
        await self._start([KIND_ARGUMENT[kind]], f"LibreOffice {titles[kind]}")
        return {"started": True}

    async def open(self, recent: str) -> dict[str, Any]:
        await self.refresh()
        if self._command is None:
            raise ActionError("unavailable", "LibreOffice n'est pas installé sur ce PC.")
        document = next((r for r in self._recent if r.id == recent), None)
        if document is None or not document.path.is_file():
            raise ActionError("not_found", "Ce document n'existe plus.")
        await self._start([str(document.path)], document.name)
        return {"started": True, "name": document.name}


# --- actions -----------------------------------------------------------------------------------


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class NewParams(_Strict):
    kind: Literal["writer", "calc", "impress"]


class OpenParams(_Strict):
    id: str = Field(pattern=r"^[0-9a-f]{16}$")


def register_office_actions(registry: ActionRegistry, backend: OfficeBackend) -> None:
    async def new(_ctx: ActionContext, params: NewParams) -> dict[str, Any]:
        return await backend.new(params.kind)

    async def open_recent(_ctx: ActionContext, params: OpenParams) -> dict[str, Any]:
        return await backend.open(params.id)

    registry.register("office.new", new, params=NewParams, timeout=10.0)
    registry.register("office.open", open_recent, params=OpenParams, timeout=10.0)
