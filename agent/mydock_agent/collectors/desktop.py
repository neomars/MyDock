"""Thème « apps » : les applications installées (fichiers .desktop), leurs icônes, et leur lancement.

Tout vient des fichiers `.desktop` que lit le bureau lui-même : ceux du système, de l'utilisateur,
de Flatpak et de Snap, avec les règles de la spécification (version de l'utilisateur prioritaire,
`NoDisplay`, `Hidden`, `OnlyShowIn`, `TryExec`, nom dans la langue de la session). Le dock choisit
dans cette liste les applications de sa page « Applis » ; il ne peut pas lancer autre chose :
l'action n'accepte que l'identifiant d'un fichier de la liste, et c'est `gio launch` (le lanceur
de GNOME) qui exécute la ligne `Exec`, jamais l'agent.

Les icônes se demandent une par une (`apps.icon`), par un identifiant opaque : le dock ne donne
jamais un nom de fichier.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import io
import logging
import os
import re
import shutil
import subprocess
import unicodedata
from collections import OrderedDict
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict, Field

from mydock_agent.collectors.music import sniff_image
from mydock_agent.hub import Provider, Publish
from mydock_agent.launcher import Launcher, LaunchError, SystemLauncher
from mydock_common.actions import ActionContext, ActionError, ActionRegistry

log = logging.getLogger(__name__)

RESCAN_SECONDS = 30.0
MAX_ENTRY_BYTES = 64 * 1024
MAX_ICON_BYTES = 512 * 1024
ICON_SEND_LIMIT = 160 * 1024
ICON_SIZE = 192
ICON_CACHE = 96
NAME_LIMIT = 80
ICON_SIZES = (128, 256, 96, 64, 192, 512, 48, 72, 32)
FALLBACK_THEMES = ("Yaru", "Adwaita", "hicolor")
EXTRA_DATA_DIRS = (
    "/var/lib/flatpak/exports/share",
    "~/.local/share/flatpak/exports/share",
    "/var/lib/snapd/desktop",
)

_ESCAPES = {"s": " ", "n": "\n", "t": "\t", "r": "\r", "\\": "\\"}


# --- environnement -----------------------------------------------------------------------------


def data_dirs(env: Mapping[str, str], home: Path) -> list[Path]:
    """Les dossiers `share` qui comptent, du plus prioritaire au moins prioritaire."""
    first = env.get("XDG_DATA_HOME") or str(home / ".local" / "share")
    system = (env.get("XDG_DATA_DIRS") or "/usr/local/share:/usr/share").split(":")
    extra = [str(home / p[2:]) if p.startswith("~/") else p for p in EXTRA_DATA_DIRS]
    ordered: list[Path] = []
    for raw in (first, *system, *extra):
        path = Path(raw)
        if raw and path not in ordered:
            ordered.append(path)
    return ordered


def current_desktops(env: Mapping[str, str]) -> list[str]:
    """« ubuntu:GNOME » -> ['ubuntu', 'GNOME'] (une application peut se réserver à un bureau)."""
    return [d for d in (env.get("XDG_CURRENT_DESKTOP") or "").split(":") if d]


def current_locales(env: Mapping[str, str], default_file: Path | None = None) -> list[str]:
    """['fr_FR', 'fr'] : les langues de la session, de la plus souhaitée à la moins souhaitée.

    Un service systemd utilisateur n'a pas toujours LANG : on se rabat sur /etc/default/locale.
    """
    raw = [
        *(env.get("LANGUAGE") or "").split(":"),
        env.get("LC_ALL") or "",
        env.get("LC_MESSAGES") or "",
        env.get("LANG") or "",
    ]
    if not any(raw):
        path = default_file or Path("/etc/default/locale")
        try:
            for line in path.read_text(encoding="utf-8").splitlines():
                if line.startswith("LANG="):
                    raw.append(line.partition("=")[2].strip().strip("\"'"))
        except OSError:
            pass
    locales: list[str] = []
    for item in raw:
        name = re.split(r"[.@]", item, maxsplit=1)[0]
        if not name or name in ("C", "POSIX"):
            continue
        for candidate in (name, name.split("_")[0]):
            if candidate not in locales:
                locales.append(candidate)
    return locales


# --- fichiers .desktop -------------------------------------------------------------------------


def parse_entry(text: str) -> dict[str, str]:
    """Les clés du groupe [Desktop Entry] ; les autres groupes (actions) sont ignorés."""
    entry: dict[str, str] = {}
    inside = False
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("["):
            inside = line == "[Desktop Entry]"
            continue
        if not inside or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        if key and key not in entry:
            entry[key] = re.sub(
                r"\\(.)", lambda m: _ESCAPES.get(m.group(1), m.group(0)), value.strip()
            )
    return entry


def localized(entry: Mapping[str, str], key: str, locales: Sequence[str]) -> str | None:
    for locale in locales:
        if value := entry.get(f"{key}[{locale}]"):
            return value
    return entry.get(key) or None


def split_list(value: str | None) -> list[str]:
    """« A;B; » -> ['A', 'B'] (le point-virgule échappé fait partie de l'élément)."""
    if not value:
        return []
    return [p.replace("\\;", ";") for p in re.split(r"(?<!\\);", value) if p]


def truthy(value: str | None) -> bool:
    return (value or "").strip().lower() == "true"


def is_visible(
    entry: Mapping[str, str], desktops: Sequence[str], which: Callable[[str], str | None]
) -> bool:
    """Ce que le menu du bureau montrerait."""
    if entry.get("Type") != "Application" or not entry.get("Name") or not entry.get("Exec"):
        return False
    if truthy(entry.get("NoDisplay")) or truthy(entry.get("Hidden")):
        return False
    only, never = split_list(entry.get("OnlyShowIn")), split_list(entry.get("NotShowIn"))
    if only and not set(only) & set(desktops):
        return False
    if never and set(never) & set(desktops):
        return False
    return not (entry.get("TryExec") and not which(entry["TryExec"]))


@dataclass(frozen=True)
class DesktopApp:
    id: str  # identifiant du fichier : « org.mozilla.firefox.desktop »
    path: Path
    name: str
    generic: str | None
    icon: str | None  # valeur de Icon= : un nom du thème ou un chemin absolu
    terminal: bool

    @property
    def icon_id(self) -> str | None:
        return icon_id(self.icon) if self.icon else None


def icon_id(icon: str) -> str:
    return hashlib.sha1(icon.encode(), usedforsecurity=False).hexdigest()[:16]


def sort_key(name: str) -> str:
    """Ordre alphabétique à la française : « Éditeur » se range avec les E, pas après les Z."""
    decomposed = unicodedata.normalize("NFKD", name)
    return "".join(c for c in decomposed if not unicodedata.combining(c)).casefold()


def clean(value: str | None, limit: int = NAME_LIMIT) -> str | None:
    text = re.sub(r"[\x00-\x1f\x7f]+", " ", value or "").strip()
    return text[:limit] or None


def scan_applications(
    directories: Sequence[Path],
    *,
    desktops: Sequence[str],
    locales: Sequence[str],
    which: Callable[[str], str | None] = shutil.which,
) -> list[DesktopApp]:
    """Les applications visibles, triées par nom. Le premier dossier qui a un identifiant l'emporte,
    même pour le cacher (`Hidden=true` dans le dossier de l'utilisateur masque la version du système)."""
    seen: set[str] = set()
    found: list[DesktopApp] = []
    for base in directories:
        if not base.is_dir():
            continue
        for path in sorted(base.rglob("*.desktop")):
            app_id = "-".join(path.relative_to(base).parts)
            if app_id in seen:
                continue
            seen.add(app_id)
            try:
                if path.stat().st_size > MAX_ENTRY_BYTES:
                    continue
                entry = parse_entry(path.read_text(encoding="utf-8", errors="replace"))
            except OSError:
                continue
            if not is_visible(entry, desktops, which):
                continue
            name = clean(localized(entry, "Name", locales))
            if name is None:
                continue
            found.append(
                DesktopApp(
                    id=app_id,
                    path=path,
                    name=name,
                    generic=clean(localized(entry, "GenericName", locales)),
                    icon=entry.get("Icon") or None,
                    terminal=truthy(entry.get("Terminal")),
                )
            )
    found.sort(key=lambda app: (sort_key(app.name), app.id))
    return found


# --- icônes ------------------------------------------------------------------------------------


def is_svg(data: bytes) -> bool:
    head = data[:2048].lstrip(b"\xef\xbb\xbf \t\r\n")
    return (
        head.startswith((b"<svg", b"<?xml", b"<!--", b"<!DOCTYPE svg")) and b"<svg" in data[:4096]
    )


def image_mime(data: bytes) -> str | None:
    """Type d'image d'après les premiers octets (jamais d'après l'extension)."""
    if (mime := sniff_image(data)) is not None:
        return mime
    return "image/svg+xml" if is_svg(data) else None


class IconFinder:
    """Retrouve le fichier d'une icône comme le ferait le bureau : thème courant, thèmes parents,
    hicolor, puis /usr/share/pixmaps."""

    def __init__(self, roots: Sequence[Path], theme: str | None, home: Path):
        self._share = [r / "icons" for r in roots] + [home / ".icons"]
        self._pixmaps = [r / "pixmaps" for r in roots]
        self._themes = self._chain(theme)

    def _inherits(self, theme: str) -> list[str]:
        for root in self._share:
            try:
                text = (root / theme / "index.theme").read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            for line in text.splitlines():
                if line.startswith("Inherits="):
                    return [t.strip() for t in line.partition("=")[2].split(",") if t.strip()]
        return []

    def _chain(self, theme: str | None) -> list[str]:
        chain: list[str] = []
        pending = [theme] if theme else list(FALLBACK_THEMES[:2])
        while pending and len(chain) < 8:
            current = pending.pop(0)
            if current and current not in chain:
                chain.append(current)
                pending.extend(self._inherits(current))
        if "hicolor" not in chain:
            chain.append("hicolor")
        return chain

    def find(self, icon: str) -> Path | None:
        if icon.startswith("/"):
            path = Path(icon)
            return path if path.is_file() else None
        name = Path(icon).stem if icon.endswith((".png", ".svg", ".jpg")) else icon
        if not re.fullmatch(r"[\w.+@-]{1,120}", name):
            return None
        for theme in self._themes:
            for root in self._share:
                base = root / theme
                if not base.is_dir():
                    continue
                for size in ICON_SIZES:
                    for layout in (f"{size}x{size}/apps", f"apps/{size}"):
                        candidate = base / layout / f"{name}.png"
                        if candidate.is_file():
                            return candidate
                for layout in ("scalable/apps", "apps/scalable"):
                    candidate = base / layout / f"{name}.svg"
                    if candidate.is_file():
                        return candidate
        for pixmaps in self._pixmaps:
            for extension in ("png", "svg", "jpg"):
                candidate = pixmaps / f"{name}.{extension}"
                if candidate.is_file():
                    return candidate
        return None


def read_icon(path: Path) -> tuple[str, bytes]:
    """Le contenu d'une icône : une vraie image, de taille raisonnable, réduite au besoin."""
    try:
        if path.stat().st_size > MAX_ICON_BYTES:
            raise ActionError("icon_failed", "Icône trop volumineuse.")
        data = path.read_bytes()
    except OSError:
        raise ActionError("icon_failed", "Icône illisible.") from None
    mime = image_mime(data)
    if mime is None:
        raise ActionError("icon_failed", "Ce fichier n'est pas une image.")
    if len(data) > ICON_SEND_LIMIT and mime != "image/svg+xml":
        try:
            from PIL import Image
        except ImportError:
            raise ActionError("icon_failed", "Icône trop lourde (installe python3-pil).") from None
        try:
            with Image.open(io.BytesIO(data)) as image:
                image.thumbnail((ICON_SIZE, ICON_SIZE))
                out = io.BytesIO()
                image.convert("RGBA").save(out, "PNG", optimize=True)
        except (OSError, ValueError, Image.DecompressionBombError):
            raise ActionError("icon_failed", "Icône illisible.") from None
        return "image/png", out.getvalue()
    return mime, data


def gsettings_icon_theme() -> str | None:
    """Le thème d'icônes du bureau GNOME (None si gsettings est absent ou muet)."""
    if not shutil.which("gsettings"):
        return None
    try:
        out = subprocess.run(
            ["gsettings", "get", "org.gnome.desktop.interface", "icon-theme"],
            capture_output=True,
            text=True,
            timeout=2,
            check=False,
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return None
    return out.strip("'\"") or None


# --- le fournisseur ----------------------------------------------------------------------------


class AppsBackend(Protocol):
    async def launch(self, app_id: str) -> dict[str, Any]: ...
    async def icon(self, icon: str) -> dict[str, str]: ...


class AppsProvider(Provider):
    topic = "apps"
    lazy = True  # la liste n'est relue que pendant qu'une page ou les Réglages la regardent

    def __init__(
        self,
        *,
        directories: Sequence[Path] | None = None,
        roots: Sequence[Path] | None = None,
        launcher: Launcher | None = None,
        env: Mapping[str, str] | None = None,
        home: Path | None = None,
        theme: Callable[[], str | None] = gsettings_icon_theme,
        interval: float = RESCAN_SECONDS,
    ):
        self._env = dict(os.environ if env is None else env)
        self._home = home or Path.home()
        self._roots = list(roots) if roots is not None else data_dirs(self._env, self._home)
        self._dirs = (
            list(directories)
            if directories is not None
            else [r / "applications" for r in self._roots]
        )
        self._launcher: Launcher = launcher or SystemLauncher()
        self._theme = theme
        self._interval = interval
        self._apps: list[DesktopApp] = []
        self._by_id: dict[str, DesktopApp] = {}
        self._icons: dict[str, str] = {}
        self._finder: IconFinder | None = None
        self._cache: OrderedDict[str, dict[str, str]] = OrderedDict()
        self._signature: tuple[Any, ...] | None = None

    # --- lecture de la liste ---

    @property
    def apps(self) -> list[DesktopApp]:
        return list(self._apps)

    @property
    def directories(self) -> list[Path]:
        return [d for d in self._dirs if d.is_dir()]

    def _stamp(self) -> tuple[Any, ...]:
        stamp = []
        for directory in self._dirs:
            try:
                stamp.append((str(directory), directory.stat().st_mtime_ns))
            except OSError:
                stamp.append((str(directory), None))
        return tuple(stamp)

    def _scan(self) -> None:
        apps = scan_applications(
            self._dirs,
            desktops=current_desktops(self._env),
            locales=current_locales(self._env),
            which=lambda name: shutil.which(name, path=self._env.get("PATH")),
        )
        self._apps = apps
        self._by_id = {app.id: app for app in apps}
        self._icons = {app.icon_id: app.icon for app in apps if app.icon and app.icon_id}
        self._signature = self._stamp()

    def snapshot(self) -> dict[str, Any]:
        return {
            "count": len(self._apps),
            "apps": [
                {"id": a.id, "name": a.name, "generic": a.generic, "icon": a.icon_id}
                for a in self._apps
            ],
        }

    async def refresh(self) -> bool:
        """Relit les dossiers s'ils ont changé (ou la première fois). Vrai si la liste a été relue."""
        if self._signature is not None and self._stamp() == self._signature:
            return False
        await asyncio.to_thread(self._scan)
        return True

    async def run(self, publish: Publish) -> None:
        while True:
            await self.refresh()
            await publish(self.snapshot())
            await asyncio.sleep(self._interval)

    # --- actions ---

    async def launch(self, app_id: str) -> dict[str, Any]:
        await self.refresh()
        app = self._by_id.get(app_id)
        if app is None:
            raise ActionError("not_found", "Cette application n'est plus installée.")
        try:
            await self._launcher.start(["gio", "launch", str(app.path)], label=app.name)
        except LaunchError as exc:
            raise ActionError("launch_failed", str(exc)) from None
        return {"started": True, "name": app.name}

    async def icon(self, icon: str) -> dict[str, str]:
        if icon in self._cache:
            self._cache.move_to_end(icon)
            return self._cache[icon]
        await self.refresh()
        wanted = self._icons.get(icon)
        if wanted is None:
            raise ActionError("unknown_icon", "Cette icône n'est plus d'actualité.")
        if self._finder is None:
            theme = await asyncio.to_thread(self._theme)
            self._finder = IconFinder(self._roots, theme, self._home)
        path = await asyncio.to_thread(self._finder.find, wanted)
        if path is None:
            raise ActionError("icon_failed", "Icône introuvable dans le thème.")
        mime, data = await asyncio.to_thread(read_icon, path)
        result = {"mime": mime, "data": base64.b64encode(data).decode("ascii")}
        self._cache[icon] = result
        while len(self._cache) > ICON_CACHE:
            self._cache.popitem(last=False)
        return result


# --- actions -----------------------------------------------------------------------------------


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class LaunchParams(_Strict):
    id: str = Field(pattern=r"^[A-Za-z0-9_.+-]{1,200}\.desktop$")


class IconParams(_Strict):
    icon: str = Field(pattern=r"^[0-9a-f]{16}$")


def register_apps_actions(registry: ActionRegistry, backend: AppsBackend) -> None:
    async def launch(_ctx: ActionContext, params: LaunchParams) -> dict[str, Any]:
        return await backend.launch(params.id)

    async def icon(_ctx: ActionContext, params: IconParams) -> dict[str, str]:
        return await backend.icon(params.icon)

    registry.register("apps.launch", launch, params=LaunchParams, timeout=8.0)
    registry.register("apps.icon", icon, params=IconParams, timeout=10.0)
