"""Thème « steam » : les jeux Steam installés, leurs jaquettes, le jeu en cours, et leur lancement.

Steam range ses données dans des fichiers texte au format VDF (« KeyValues » de Valve) :
`libraryfolders.vdf` liste les dossiers de bibliothèque (le disque système, un disque de jeux…) et
chaque jeu installé a un `appmanifest_<appid>.acf`. L'agent les lit, sans jamais parler à Steam.

Un même dossier Steam s'atteint souvent par plusieurs chemins (`~/.steam/steam` est un lien vers
`~/.local/share/Steam`) : tout est dédoublonné par chemin réel, sinon chaque jeu serait listé deux
fois. Les jaquettes sont celles du cache de Steam (ancien et nouveau découpage), réduites avant
l'envoi : un Pi n'a pas la mémoire de décoder quarante images de 600×900.

Le dock ne peut lancer qu'un jeu de la liste : l'agent construit lui-même `steam steam://rungameid/N`.
"""

from __future__ import annotations

import asyncio
import base64
import io
import logging
import re
import shutil
from collections import OrderedDict
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

import psutil
from pydantic import BaseModel, ConfigDict, Field

from mydock_agent.collectors.desktop import image_mime, sort_key
from mydock_agent.hub import Provider, Publish
from mydock_agent.launcher import Launcher, LaunchError, SystemLauncher
from mydock_common.actions import ActionContext, ActionError, ActionRegistry

log = logging.getLogger(__name__)

MAX_VDF_BYTES = 2 * 1024 * 1024
MAX_VDF_DEPTH = 24
MAX_GAMES = 400
COVER_LIMIT = 4 * 1024 * 1024
COVER_SEND_LIMIT = 350 * 1024
COVER_SIZE = (240, 360)
COVER_CACHE = 64
RUNNING_SECONDS = 3.0
STATE_FULLY_INSTALLED = 4
FLATPAK_ID = "com.valvesoftware.Steam"

# Ce que Steam installe comme « jeux » sans en être : pas de jaquette à toucher.
TOOL_NAME = re.compile(
    r"^(Proton\b|Steam Linux Runtime|Steamworks Common Redistributables|Steam Controller Configs)",
    re.IGNORECASE,
)
TOOL_APPIDS = frozenset({228980})  # Steamworks Common Redistributables


# --- format VDF --------------------------------------------------------------------------------

_TOKEN = re.compile(
    r"""\s+|//[^\n]*|(?P<open>\{)|(?P<close>\})|"(?P<quoted>(?:[^"\\]|\\.)*)"|(?P<bare>[^\s{}"]+)"""
)
_ESCAPES = {"n": "\n", "t": "\t", "\\": "\\", '"': '"'}


class VdfError(ValueError):
    """Texte VDF illisible."""


def _tokens(text: str) -> list[tuple[str, str]]:
    tokens: list[tuple[str, str]] = []
    position = 0
    while position < len(text):
        match = _TOKEN.match(text, position)
        if match is None:
            raise VdfError(f"caractère inattendu à la position {position}")
        position = match.end()
        if match["open"]:
            tokens.append(("{", "{"))
        elif match["close"]:
            tokens.append(("}", "}"))
        elif match["quoted"] is not None:
            value = re.sub(
                r"\\(.)", lambda m: _ESCAPES.get(m.group(1), m.group(1)), match["quoted"]
            )
            tokens.append(("s", value))
        elif match["bare"]:
            tokens.append(("s", match["bare"]))
    return tokens


def parse_vdf(text: str) -> dict[str, Any]:
    """Texte VDF -> dictionnaires imbriqués (les valeurs sont des textes, un doublon écrase le précédent)."""
    tokens = _tokens(text)

    def block(index: int, depth: int) -> tuple[dict[str, Any], int]:
        if depth > MAX_VDF_DEPTH:
            raise VdfError("imbrication excessive")
        result: dict[str, Any] = {}
        while index < len(tokens):
            kind, value = tokens[index]
            if kind == "}":
                if depth == 0:
                    raise VdfError("accolade fermante en trop")
                return result, index + 1
            if kind != "s":
                raise VdfError("clé attendue")
            index += 1
            if index >= len(tokens):
                raise VdfError("valeur manquante")
            kind, inner = tokens[index]
            if kind == "{":
                result[value], index = block(index + 1, depth + 1)
            elif kind == "s":
                result[value] = inner
                index += 1
            else:
                raise VdfError("valeur attendue")
        if depth:
            raise VdfError("accolade fermante manquante")
        return result, index

    parsed, _ = block(0, 0)
    return parsed


def read_vdf(path: Path) -> dict[str, Any]:
    """Un fichier VDF, ou {} s'il manque, est trop gros ou est illisible."""
    try:
        if path.stat().st_size > MAX_VDF_BYTES:
            return {}
        return parse_vdf(path.read_text(encoding="utf-8", errors="replace"))
    except (OSError, VdfError):
        return {}


def vget(node: Any, key: str, default: Any = None) -> Any:
    """Valeur d'une clé sans tenir compte de la casse (Steam écrit « AppState » et « appid »)."""
    if isinstance(node, dict):
        for name, value in node.items():
            if name.lower() == key.lower():
                return value
    return default


# --- dossiers et jeux --------------------------------------------------------------------------


def steam_roots(home: Path) -> list[Path]:
    """Les dossiers Steam existants (natif, liens `~/.steam`, Flatpak, Snap), sans doublon de chemin réel."""
    candidates = (
        home / ".local" / "share" / "Steam",
        home / ".steam" / "steam",
        home / ".steam" / "root",
        home / ".var" / "app" / FLATPAK_ID / ".local" / "share" / "Steam",
        home / "snap" / "steam" / "common" / ".local" / "share" / "Steam",
    )
    roots: list[Path] = []
    seen: set[Path] = set()
    for candidate in candidates:
        try:
            real = candidate.resolve(strict=True)
        except OSError:
            continue
        if real.is_dir() and real not in seen:
            seen.add(real)
            roots.append(real)
    return roots


def library_folders(root: Path) -> list[Path]:
    """Le dossier Steam lui-même et les autres bibliothèques déclarées dans `libraryfolders.vdf`."""
    found = [root]
    for relative in ("steamapps/libraryfolders.vdf", "config/libraryfolders.vdf"):
        data = vget(read_vdf(root / relative), "libraryfolders", {})
        for key, entry in data.items() if isinstance(data, dict) else ():
            # Nouveau format : « 0 » { « path » « /mnt/jeux » … } ; ancien : « 1 » « /mnt/jeux ».
            raw = vget(entry, "path") if isinstance(entry, dict) else entry
            if key.isdigit() and isinstance(raw, str) and raw.startswith("/"):
                found.append(Path(raw))
    libraries: list[Path] = []
    seen: set[Path] = set()
    for folder in found:
        try:
            real = folder.resolve(strict=True)
        except OSError:
            continue
        if real not in seen and (real / "steamapps").is_dir():
            seen.add(real)
            libraries.append(real)
    return libraries


@dataclass(frozen=True)
class Game:
    appid: int
    name: str
    size: int
    played: int | None  # dernière partie, sinon dernière mise à jour (secondes epoch)


def _integer(value: Any) -> int | None:
    try:
        return int(str(value))
    except ValueError:
        return None


def read_manifest(path: Path) -> Game | None:
    """Un jeu installé d'après son `appmanifest_*.acf` ; None pour un outil, un jeu non installé, un fichier abîmé."""
    state = vget(read_vdf(path), "AppState")
    appid = _integer(vget(state, "appid"))
    name = vget(state, "name")
    flags = _integer(vget(state, "StateFlags")) or 0
    if appid is None or not isinstance(name, str) or not name.strip():
        return None
    if not flags & STATE_FULLY_INSTALLED or appid in TOOL_APPIDS or TOOL_NAME.match(name):
        return None
    played = _integer(vget(state, "LastPlayed")) or _integer(vget(state, "LastUpdated"))
    return Game(
        appid=appid,
        name=re.sub(r"[\x00-\x1f\x7f]+", " ", name).strip()[:100],
        size=max(0, _integer(vget(state, "SizeOnDisk")) or 0),
        played=played or None,
    )


def read_games(libraries: Iterable[Path]) -> list[Game]:
    games: dict[int, Game] = {}
    for library in libraries:
        for manifest in sorted((library / "steamapps").glob("appmanifest_*.acf")):
            if (game := read_manifest(manifest)) is not None:
                games.setdefault(game.appid, game)
    return sorted(games.values(), key=lambda g: (sort_key(g.name), g.appid))[:MAX_GAMES]


# --- jaquettes ---------------------------------------------------------------------------------

PORTRAIT = "library_600x900"
EXTENSIONS = ("jpg", "jpeg", "png", "webp", "svg")


def find_cover(roots: Iterable[Path], appid: int) -> Path | None:
    """La jaquette portrait du cache de Steam, à défaut sa bannière. Deux découpages existent :
    `<appid>_library_600x900.jpg` (ancien) et `<appid>/[<empreinte>/]library_600x900.jpg` (récent)."""
    for name in (PORTRAIT, "header"):
        for root in roots:
            cache = root / "appcache" / "librarycache"
            for extension in EXTENSIONS:
                for candidate in (
                    cache / f"{appid}_{name}.{extension}",
                    cache / str(appid) / f"{name}.{extension}",
                    *sorted(cache.glob(f"{appid}/*/{name}.{extension}")),
                ):
                    if candidate.is_file():
                        return candidate
    return None


def prepare_cover(data: bytes) -> tuple[str, bytes]:
    """Jaquette prête pour l'écran : réduite en JPEG si Pillow est là, sinon telle quelle si légère."""
    mime = image_mime(data)
    if mime is None:
        raise ActionError("cover_failed", "Ce fichier n'est pas une image.")
    if mime == "image/svg+xml":
        return mime, data
    try:
        from PIL import Image, ImageOps
    except ImportError:
        if len(data) > COVER_SEND_LIMIT:
            raise ActionError(
                "cover_failed", "Jaquette trop lourde (installe python3-pil)."
            ) from None
        return mime, data
    try:
        with Image.open(io.BytesIO(data)) as image:
            image = ImageOps.exif_transpose(image)
            image.thumbnail(COVER_SIZE)
            out = io.BytesIO()
            image.convert("RGB").save(out, "JPEG", quality=80, optimize=True)
    except (OSError, ValueError, Image.DecompressionBombError):
        raise ActionError("cover_failed", "Jaquette illisible.") from None
    return "image/jpeg", out.getvalue()


# --- jeu en cours ------------------------------------------------------------------------------


@dataclass(frozen=True)
class SteamProcesses:
    client: bool  # le client Steam tourne
    running: int | None  # le jeu lancé par Steam, s'il y en a un


def running_appid(command: Sequence[str]) -> int | None:
    """« reaper SteamLaunch AppId=1245620 -- … » -> 1245620 : Steam lance chaque jeu par `reaper`."""
    if "SteamLaunch" not in command:
        return None
    for argument in command:
        if argument.startswith("AppId="):
            return _integer(argument.partition("=")[2])
    return None


def scan_processes() -> SteamProcesses:
    client, running = False, None
    for process in psutil.process_iter(["name"]):
        name = process.info["name"]
        try:
            if name in ("steam", "steamwebhelper"):
                client = True
            elif name == "reaper":
                running = running_appid(process.cmdline()) or running
        except (psutil.Error, OSError):
            continue
    return SteamProcesses(client, running)


# --- le fournisseur ----------------------------------------------------------------------------


class SteamBackend(Protocol):
    async def launch(self, appid: int) -> dict[str, Any]: ...
    async def cover(self, appid: int) -> dict[str, str]: ...


class SteamProvider(Provider):
    topic = "steam"
    lazy = True  # on ne regarde les processus que pendant que la page Steam est ouverte

    def __init__(
        self,
        *,
        home: Path | None = None,
        roots: Sequence[Path] | None = None,
        scanner: Callable[[], SteamProcesses] = scan_processes,
        launcher: Launcher | None = None,
        which: Callable[[str], str | None] = shutil.which,
        interval: float = RUNNING_SECONDS,
    ):
        self._home = home or Path.home()
        self._fixed_roots = list(roots) if roots is not None else None
        self._scanner = scanner
        self._launcher: Launcher = launcher or SystemLauncher()
        self._which = which
        self._interval = interval
        self._roots: list[Path] = []
        self._games: list[Game] = []
        self._processes = SteamProcesses(False, None)
        self._signature: tuple[Any, ...] | None = None
        self._covers: OrderedDict[int, dict[str, str]] = OrderedDict()

    # --- bibliothèque ---

    @property
    def roots(self) -> list[Path]:
        return list(self._roots)

    @property
    def games(self) -> list[Game]:
        return list(self._games)

    @property
    def processes(self) -> SteamProcesses:
        return self._processes

    def _discover(self) -> list[Path]:
        return self._fixed_roots if self._fixed_roots is not None else steam_roots(self._home)

    def _stamp(self, roots: list[Path]) -> tuple[Any, ...]:
        stamp: list[Any] = []
        for root in roots:
            for folder in library_folders(root):
                try:
                    stamp.append((str(folder), (folder / "steamapps").stat().st_mtime_ns))
                except OSError:
                    stamp.append((str(folder), None))
        return (*sorted(map(str, roots)), *stamp)

    def _load(self) -> None:
        roots = self._discover()
        stamp = self._stamp(roots)
        if stamp == self._signature:
            return
        libraries: list[Path] = []
        for root in roots:
            libraries.extend(p for p in library_folders(root) if p not in libraries)
        self._roots, self._games, self._signature = roots, read_games(libraries), stamp

    def snapshot(self) -> dict[str, Any]:
        if not self._roots:
            return {
                "unavailable": "Steam n'est pas installé sur ce PC (aucun dossier Steam trouvé).",
                "games": [],
                "client": self._processes.client,
                "running": None,
            }
        return {
            "client": self._processes.client,
            "running": self._processes.running,
            "games": [
                {"appid": g.appid, "name": g.name, "size": g.size, "played": g.played}
                for g in self._games
            ],
        }

    async def refresh(self) -> None:
        await asyncio.to_thread(self._load)

    async def run(self, publish: Publish) -> None:
        while True:
            await self.refresh()
            self._processes = await asyncio.to_thread(self._scanner)
            await publish(self.snapshot())
            await asyncio.sleep(self._interval)

    # --- actions ---

    def _command(self, appid: int) -> list[str]:
        url = f"steam://rungameid/{appid}"
        if self._which("steam"):
            return ["steam", url]
        flatpak = any(FLATPAK_ID in str(root) for root in self._roots)
        if flatpak and self._which("flatpak"):
            return ["flatpak", "run", FLATPAK_ID, url]
        if self._which("xdg-open"):
            return ["xdg-open", url]
        raise ActionError(
            "launch_failed", "Impossible de démarrer Steam : « steam » est introuvable."
        )

    async def launch(self, appid: int) -> dict[str, Any]:
        await self.refresh()
        game = next((g for g in self._games if g.appid == appid), None)
        if game is None:
            raise ActionError("not_found", "Ce jeu n'est plus installé.")
        try:
            await self._launcher.start(self._command(appid), label=game.name)
        except LaunchError as exc:
            raise ActionError("launch_failed", str(exc)) from None
        return {"started": True, "name": game.name}

    async def cover(self, appid: int) -> dict[str, str]:
        if appid in self._covers:
            self._covers.move_to_end(appid)
            return self._covers[appid]
        await self.refresh()
        if not any(g.appid == appid for g in self._games):
            raise ActionError("not_found", "Ce jeu n'est plus installé.")
        path = await asyncio.to_thread(find_cover, self._roots, appid)
        if path is None:
            raise ActionError("no_cover", "Pas de jaquette dans le cache de Steam.")
        try:
            if path.stat().st_size > COVER_LIMIT:
                raise ActionError("cover_failed", "Jaquette trop volumineuse.")
            data = await asyncio.to_thread(path.read_bytes)
        except OSError:
            raise ActionError("cover_failed", "Jaquette illisible.") from None
        mime, image = await asyncio.to_thread(prepare_cover, data)
        result = {"mime": mime, "data": base64.b64encode(image).decode("ascii")}
        self._covers[appid] = result
        while len(self._covers) > COVER_CACHE:
            self._covers.popitem(last=False)
        return result


# --- actions -----------------------------------------------------------------------------------


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class GameParams(_Strict):
    appid: int = Field(ge=1, le=2_000_000_000, strict=True)


def register_steam_actions(registry: ActionRegistry, backend: SteamBackend) -> None:
    async def launch(_ctx: ActionContext, params: GameParams) -> dict[str, Any]:
        return await backend.launch(params.appid)

    async def cover(_ctx: ActionContext, params: GameParams) -> dict[str, str]:
        return await backend.cover(params.appid)

    registry.register("steam.launch", launch, params=GameParams, timeout=10.0)
    registry.register("steam.cover", cover, params=GameParams, timeout=10.0)
