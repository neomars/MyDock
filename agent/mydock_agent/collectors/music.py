"""Thème « music » : les lecteurs multimédias du PC, par MPRIS (D-Bus), sans aucune interrogation.

MPRIS est le protocole que parlent Spotify, VLC, Firefox, Brave, Rhythmbox… : chaque lecteur
apparaît sur le bus de session sous le nom `org.mpris.MediaPlayer2.<lecteur>` et signale ses
changements (lecture, pause, morceau, déplacement dans le morceau). L'agent n'interroge donc
jamais : il écoute, et ne publie que lorsque quelque chose change (au plus tous les 50 ms).

La position dans le morceau n'est pas un changement (elle avance toute seule) : on publie la
position au moment de l'événement et l'interface la prolonge ; la page Musique se recale avec
`music.position`, ce qui évite aussi qu'un instantané ancien en cache donne une position périmée.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import io
import ipaddress
import logging
import re
import socket
import time
import urllib.parse
import urllib.request
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict, Field

from mydock_agent.hub import Provider, Publish
from mydock_common.actions import ActionContext, ActionError, ActionRegistry, NoParams

log = logging.getLogger(__name__)

MPRIS_PREFIX = "org.mpris.MediaPlayer2."
OBJECT_PATH = "/org/mpris/MediaPlayer2"
ROOT_IFACE = "org.mpris.MediaPlayer2"
PLAYER_IFACE = "org.mpris.MediaPlayer2.Player"
PROPS_IFACE = "org.freedesktop.DBus.Properties"
DBUS_NAME = "org.freedesktop.DBus"
COALESCE_SECONDS = 0.05  # regroupe les salves de signaux (Chromium en émet plusieurs d'affilée)
RECONNECT_SECONDS = 10.0
CALL_TIMEOUT = 3.0
ADD_ATTEMPTS = 4
ADD_RETRY_SECONDS = 0.2
TEXT_LIMIT = 200
PREFERRED_PLAYER = "spotify"  # à égalité, Spotify passe en premier

STATUSES = {"Playing": "playing", "Paused": "paused", "Stopped": "stopped"}


# --- outils purs ---------------------------------------------------------------------------------


def unwrap(value: Any) -> Any:
    """Valeur Python d'une valeur D-Bus (les `Variant` sont ouverts, récursivement)."""
    inner = getattr(value, "value", value) if type(value).__name__ == "Variant" else value
    if isinstance(inner, dict):
        return {str(k): unwrap(v) for k, v in inner.items()}
    if isinstance(inner, list | tuple):
        return [unwrap(v) for v in inner]
    return inner


def clean_text(value: Any, limit: int = TEXT_LIMIT) -> str | None:
    """Texte affichable : sans caractères de contrôle, de longueur raisonnable."""
    if value is None:
        return None
    text = re.sub(r"[\x00-\x1f\x7f]+", " ", str(value)).strip()
    return text[:limit] or None


def base_id(player_id: str) -> str:
    """« brave.instance9888 » -> « brave » : un même navigateur relancé reste « le même »."""
    return re.sub(r"\.instance\d+$", "", player_id)


def art_id(url: str) -> str:
    return hashlib.sha1(url.encode(), usedforsecurity=False).hexdigest()[:16]


@dataclass
class PlayerState:
    id: str  # nom de bus sans le préfixe : « spotify », « brave.instance9888 »
    owner: str  # nom unique sur le bus (« :1.57 ») : c'est lui qui signe les signaux
    identity: str = ""
    status: str = "stopped"
    title: str | None = None
    artist: str | None = None
    album: str | None = None
    length_ms: int | None = None
    position_ms: int | None = None
    position_t: float = 0.0  # horloge monotone de la lecture de la position
    rate: float = 1.0
    art_url: str | None = None
    track_id: str | None = None
    can_play: bool = True
    can_pause: bool = True
    can_next: bool = False
    can_previous: bool = False
    can_seek: bool = False
    activity: int = 0  # plus grand = a joué plus récemment
    props: dict[str, Any] = field(default_factory=dict, repr=False)

    @property
    def base(self) -> str:
        return base_id(self.id)

    def apply(self, props: dict[str, Any]) -> None:
        """Intègre des propriétés de l'interface Player (valeurs déjà ouvertes)."""
        for key, value in props.items():
            if key == "PlaybackStatus":
                self.status = STATUSES.get(str(value), "stopped")
            elif key == "Metadata" and isinstance(value, dict):
                self._apply_metadata(value)
            elif key == "Position" and isinstance(value, int | float):
                self.position_ms = max(0, int(value) // 1000)
                self.position_t = time.monotonic()
            elif key == "Rate" and isinstance(value, int | float) and value > 0:
                self.rate = float(value)
            elif key == "CanPlay":
                self.can_play = bool(value)
            elif key == "CanPause":
                self.can_pause = bool(value)
            elif key == "CanGoNext":
                self.can_next = bool(value)
            elif key == "CanGoPrevious":
                self.can_previous = bool(value)
            elif key == "CanSeek":
                self.can_seek = bool(value)

    def _apply_metadata(self, meta: dict[str, Any]) -> None:
        artist = meta.get("xesam:artist") or meta.get("xesam:albumArtist")
        if isinstance(artist, list):
            artist = ", ".join(str(a) for a in artist if a)
        length = meta.get("mpris:length")
        self.title = clean_text(meta.get("xesam:title"))
        self.artist = clean_text(artist)
        self.album = clean_text(meta.get("xesam:album"))
        self.length_ms = (
            int(length) // 1000 if isinstance(length, int | float) and length > 0 else None
        )
        url = meta.get("mpris:artUrl")
        self.art_url = url if isinstance(url, str) and url else None
        track = meta.get("mpris:trackid")
        self.track_id = str(track) if track else None

    def current_position_ms(self, now: float) -> int | None:
        """Position au moment `now` : celle relevée, prolongée si le morceau est en lecture."""
        if self.position_ms is None:
            return None
        position = self.position_ms
        if self.status == "playing":
            position += int((now - self.position_t) * 1000 * self.rate)
        if self.length_ms is not None:
            position = min(position, self.length_ms)
        return position

    def to_dict(self, now: float | None = None) -> dict[str, Any]:
        now = time.monotonic() if now is None else now
        return {
            "id": self.id,
            "name": self.identity or self.base,
            "status": self.status,
            "title": self.title,
            "artist": self.artist,
            "album": self.album,
            "length_ms": self.length_ms,
            "position_ms": self.current_position_ms(now),
            "rate": self.rate,
            "art": art_id(self.art_url) if self.art_url else None,
            "can": {
                "play": self.can_play,
                "pause": self.can_pause,
                "next": self.can_next,
                "previous": self.can_previous,
                "seek": self.can_seek,
            },
        }


def choose_active(players: list[PlayerState], preferred: str | None) -> PlayerState | None:
    """Le lecteur que le dock pilote : le choix explicite, sinon celui qui joue (Spotify d'abord)."""
    if not players:
        return None
    if preferred:
        for player in players:
            if player.base == preferred:
                return player

    def rank(p: PlayerState) -> tuple[int, int, int, str]:
        order = {"playing": 0, "paused": 1, "stopped": 2}[p.status]
        # À situation égale : le plus récemment joué, puis Spotify, puis l'ordre alphabétique.
        return (order, -p.activity, p.base != PREFERRED_PLAYER, p.id)

    return min(players, key=rank)


def build_snapshot(
    players: list[PlayerState], preferred: str | None, now: float | None = None
) -> dict[str, Any]:
    active = choose_active(players, preferred)
    ordered = sorted(players, key=lambda p: (p is not active, p.base != PREFERRED_PLAYER, p.id))
    return {"players": [p.to_dict(now) for p in ordered], "active": active.id if active else None}


# --- pochettes ---------------------------------------------------------------------------------

ART_MAX_BYTES = 4 * 1024 * 1024
ART_SEND_LIMIT = 350 * 1024
ART_SIZE = 480
MAGIC = (
    (b"\x89PNG\r\n\x1a\n", "image/png"),
    (b"\xff\xd8\xff", "image/jpeg"),
    (b"GIF87a", "image/gif"),
    (b"GIF89a", "image/gif"),
    (b"BM", "image/bmp"),
)


class ArtError(Exception):
    pass


def sniff_image(data: bytes) -> str | None:
    """Type d'image d'après les premiers octets (jamais d'après l'extension ni le serveur)."""
    for signature, mime in MAGIC:
        if data.startswith(signature):
            return mime
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return None


def _public_address(host: str, resolver: Callable[..., Any]) -> bool:
    try:
        infos = resolver(host, 443, type=socket.SOCK_STREAM)
    except OSError:
        return False
    addresses = [ipaddress.ip_address(info[4][0]) for info in infos]
    return bool(addresses) and all(a.is_global for a in addresses)


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args: Any, **kwargs: Any) -> None:
        return None


def fetch_art(
    url: str,
    *,
    resolver: Callable[..., Any] = socket.getaddrinfo,
    opener: urllib.request.OpenerDirector | None = None,
    timeout: float = 5.0,
) -> bytes:
    """Octets de la pochette : fichier local ou https public. Lève ArtError sinon.

    L'adresse vient du lecteur : on ne lui fait pas confiance. Un fichier doit être une image
    (reconnue à ses premiers octets : une clé privée n'en est pas une), une adresse web doit être
    en https vers une machine publique (jamais le réseau local), sans redirection.
    """
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme == "file":
        if parsed.netloc not in ("", "localhost"):
            raise ArtError("Pochette sur une machine distante : refusée.")
        path = Path(urllib.parse.unquote(parsed.path))
        try:
            real = path.resolve(strict=True)
            if not real.is_file():
                raise ArtError("La pochette n'est pas un fichier.")
            if real.stat().st_size > ART_MAX_BYTES:
                raise ArtError("Pochette trop volumineuse.")
            data = real.read_bytes()
        except OSError:
            raise ArtError("Pochette illisible.") from None
    elif parsed.scheme == "https" and parsed.hostname:
        if not _public_address(parsed.hostname, resolver):
            raise ArtError("Pochette sur une adresse non publique : refusée.")
        opener = opener or urllib.request.build_opener(_NoRedirect)
        request = urllib.request.Request(url, headers={"User-Agent": "MyDock-agent"})
        try:
            with opener.open(request, timeout=timeout) as response:
                data = response.read(ART_MAX_BYTES + 1)
        except OSError:
            raise ArtError("Pochette injoignable.") from None
        if len(data) > ART_MAX_BYTES:
            raise ArtError("Pochette trop volumineuse.")
    else:
        raise ArtError("Adresse de pochette non prise en charge.")
    if sniff_image(data) is None:
        raise ArtError("Ce fichier n'est pas une image.")
    return data


def prepare_art(data: bytes) -> tuple[str, bytes]:
    """Image prête pour l'écran : réduite en JPEG si Pillow est là, sinon telle quelle si légère."""
    mime = sniff_image(data)
    if mime is None:
        raise ArtError("Ce fichier n'est pas une image.")
    try:
        from PIL import Image, ImageOps
    except ImportError:
        if len(data) > ART_SEND_LIMIT or mime == "image/bmp":
            raise ArtError("Pochette trop lourde (installe python3-pil pour la réduire).") from None
        return mime, data
    try:
        with Image.open(io.BytesIO(data)) as image:
            image = ImageOps.exif_transpose(image)
            image.thumbnail((ART_SIZE, ART_SIZE))
            if image.mode in ("RGBA", "LA", "P"):
                background = Image.new("RGB", image.size, (19, 25, 35))
                background.paste(image.convert("RGBA"), mask=image.convert("RGBA").split()[-1])
                image = background
            out = io.BytesIO()
            image.convert("RGB").save(out, "JPEG", quality=82, optimize=True)
    except (OSError, ValueError, Image.DecompressionBombError):
        raise ArtError("Pochette illisible.") from None
    return "image/jpeg", out.getvalue()


# --- accès D-Bus -------------------------------------------------------------------------------


@dataclass(frozen=True)
class Signal:
    sender: str
    path: str
    interface: str
    member: str
    body: list[Any]


class BusError(Exception):
    """Un appel D-Bus a échoué (le lecteur a disparu, refuse, ne répond pas…)."""


class Bus(Protocol):
    async def connect(self) -> None: ...
    async def disconnect(self) -> None: ...
    async def wait_for_disconnect(self) -> None: ...
    def on_signal(self, handler: Callable[[Signal], None]) -> None: ...
    async def add_match(self, rule: str) -> None: ...
    async def call(
        self,
        destination: str,
        path: str,
        interface: str,
        member: str,
        signature: str = "",
        body: list[Any] | None = None,
    ) -> list[Any]: ...


class DbusFastBus:
    """Bus de session via dbus-fast. `address` : un autre bus (tests), sinon le bus de session."""

    def __init__(self, address: str | None = None):
        self._address = address
        self._bus: Any = None
        self._handlers: list[Callable[[Signal], None]] = []

    async def connect(self) -> None:
        from dbus_fast import BusType
        from dbus_fast.aio import MessageBus

        try:
            bus = (
                MessageBus(bus_address=self._address)
                if self._address
                else MessageBus(bus_type=BusType.SESSION)
            )
            self._bus = await asyncio.wait_for(bus.connect(), CALL_TIMEOUT)
        except (OSError, TimeoutError, ValueError) as exc:
            raise BusError(
                f"Bus de session D-Bus injoignable ({exc.__class__.__name__})."
            ) from None
        self._bus.add_message_handler(self._dispatch)

    async def disconnect(self) -> None:
        if self._bus is not None:
            self._bus.disconnect()
            self._bus = None

    async def wait_for_disconnect(self) -> None:
        await self._bus.wait_for_disconnect()

    def on_signal(self, handler: Callable[[Signal], None]) -> None:
        self._handlers.append(handler)

    def _dispatch(self, message: Any) -> None:
        from dbus_fast import MessageType

        if message.message_type != MessageType.SIGNAL:
            return
        signal = Signal(
            message.sender or "",
            message.path or "",
            message.interface or "",
            message.member or "",
            [unwrap(item) for item in message.body],
        )
        for handler in list(self._handlers):
            handler(signal)

    async def add_match(self, rule: str) -> None:
        await self.call(DBUS_NAME, "/org/freedesktop/DBus", DBUS_NAME, "AddMatch", "s", [rule])

    async def call(
        self,
        destination: str,
        path: str,
        interface: str,
        member: str,
        signature: str = "",
        body: list[Any] | None = None,
    ) -> list[Any]:
        from dbus_fast import Message, MessageType

        message = Message(
            destination=destination,
            path=path,
            interface=interface,
            member=member,
            signature=signature,
            body=body or [],
        )
        try:
            reply = await asyncio.wait_for(self._bus.call(message), CALL_TIMEOUT)
        except (OSError, TimeoutError, AttributeError) as exc:
            raise BusError(f"{member} : {exc.__class__.__name__}") from None
        if reply is None or reply.message_type == MessageType.ERROR:
            raise BusError(f"{member} : {getattr(reply, 'error_name', 'pas de réponse')}")
        return [unwrap(item) for item in reply.body]


# --- le fournisseur ----------------------------------------------------------------------------


class MusicBackend(Protocol):
    """Ce que les actions `music.*` attendent d'un fournisseur (réel ou de démonstration)."""

    async def play_pause(self, player: str | None) -> None: ...
    async def next(self, player: str | None) -> None: ...
    async def previous(self, player: str | None) -> None: ...
    async def seek(self, player: str | None, position_ms: int) -> None: ...
    def select(self, player: str) -> None: ...
    async def position(self) -> dict[str, Any]: ...
    async def art(self, art: str) -> dict[str, str]: ...


class MusicProvider(Provider):
    topic = "music"
    lazy = True  # démarré par le premier abonné : le dock suit la musique tant qu'il est connecté

    def __init__(self, bus_factory: Callable[[], Bus] = DbusFastBus):
        self._bus_factory = bus_factory
        self._bus: Bus | None = None
        self._players: dict[str, PlayerState] = {}  # clé : nom de bus complet
        self._events: asyncio.Queue[Signal | None] = asyncio.Queue()
        self._preferred: str | None = None
        self._activity = 0
        self._art_cache: OrderedDict[str, dict[str, str]] = OrderedDict()

    # --- cycle de vie ---

    async def run(self, publish: Publish) -> None:
        while True:
            try:
                await self._session(publish)
            except (BusError, OSError) as exc:
                log.warning("Musique : %s", exc)
                await publish({"unavailable": _unavailable_message(exc)})
            self._bus = None
            self._players.clear()
            await asyncio.sleep(RECONNECT_SECONDS)

    async def _session(self, publish: Publish) -> None:
        bus = self._bus_factory()
        await bus.connect()
        self._bus = bus
        try:
            bus.on_signal(self._events.put_nowait)
            await bus.add_match(
                "type='signal',sender='org.freedesktop.DBus',interface='org.freedesktop.DBus',"
                "member='NameOwnerChanged',arg0namespace='org.mpris.MediaPlayer2'"
            )
            await bus.add_match(
                f"type='signal',path='{OBJECT_PATH}',interface='{PROPS_IFACE}',member='PropertiesChanged'"
            )
            await bus.add_match(
                f"type='signal',path='{OBJECT_PATH}',interface='{PLAYER_IFACE}',member='Seeked'"
            )
            await self._discover()
            await publish(self.snapshot())
            watcher = asyncio.create_task(bus.wait_for_disconnect())
            getter: asyncio.Task[Signal | None] | None = None
            try:
                while True:
                    getter = asyncio.create_task(self._events.get())
                    done, _ = await asyncio.wait(
                        {getter, watcher}, return_when=asyncio.FIRST_COMPLETED
                    )
                    if watcher in done:
                        raise BusError("Le bus de session s'est fermé.")
                    await self.handle(getter.result())
                    await asyncio.sleep(COALESCE_SECONDS)  # laisse arriver la suite de la salve
                    while not self._events.empty():
                        await self.handle(self._events.get_nowait())
                    await publish(self.snapshot())
            finally:
                watcher.cancel()
                if getter is not None:
                    getter.cancel()
        finally:
            await bus.disconnect()

    # --- état ---

    def snapshot(self) -> dict[str, Any]:
        return build_snapshot(list(self._players.values()), self._preferred)

    def _by_id(self, player_id: str | None) -> PlayerState:
        if player_id:
            for player in self._players.values():
                if player.id == player_id or player.base == player_id:
                    return player
            raise ActionError("no_player", "Ce lecteur n'est plus là.")
        active = choose_active(list(self._players.values()), self._preferred)
        if active is None:
            raise ActionError("no_player", "Aucun lecteur multimédia n'est ouvert.")
        return active

    async def _discover(self) -> None:
        assert self._bus is not None
        (names,) = await self._bus.call(DBUS_NAME, "/org/freedesktop/DBus", DBUS_NAME, "ListNames")
        for name in names:
            if name.startswith(MPRIS_PREFIX):
                await self._add_player(name)

    async def _add_player(self, name: str) -> None:
        assert self._bus is not None
        for attempt in range(ADD_ATTEMPTS):
            try:
                (owner,) = await self._bus.call(
                    DBUS_NAME, "/org/freedesktop/DBus", DBUS_NAME, "GetNameOwner", "s", [name]
                )
                (props,) = await self._bus.call(
                    name, OBJECT_PATH, PROPS_IFACE, "GetAll", "s", [PLAYER_IFACE]
                )
                break
            except BusError:
                # Un lecteur peut annoncer son nom avant d'avoir exposé ses propriétés.
                if attempt + 1 == ADD_ATTEMPTS:
                    return
                await asyncio.sleep(ADD_RETRY_SECONDS)
        player = PlayerState(id=name.removeprefix(MPRIS_PREFIX), owner=owner)
        try:
            (root,) = await self._bus.call(
                name, OBJECT_PATH, PROPS_IFACE, "GetAll", "s", [ROOT_IFACE]
            )
            player.identity = clean_text(root.get("Identity")) or ""
        except BusError:
            pass
        player.apply(props)
        if player.status == "playing":
            self._activity += 1
            player.activity = self._activity
        self._players[name] = player

    async def _read_position(self, player: PlayerState) -> None:
        assert self._bus is not None
        try:
            (value,) = await self._bus.call(
                MPRIS_PREFIX + player.id,
                OBJECT_PATH,
                PROPS_IFACE,
                "Get",
                "ss",
                [PLAYER_IFACE, "Position"],
            )
        except BusError:
            player.position_ms = None  # certains lecteurs (navigateurs) ne le donnent pas
            return
        player.apply({"Position": value})

    def _owned_by(self, sender: str) -> PlayerState | None:
        return next((p for p in self._players.values() if p.owner == sender), None)

    async def handle(self, event: Signal | None) -> None:
        """Intègre un signal D-Bus dans l'état (appelé par la boucle, un à la fois)."""
        if event is None:
            return
        if event.member == "NameOwnerChanged" and len(event.body) == 3:
            name, _old, new = event.body
            if not str(name).startswith(MPRIS_PREFIX):
                return
            if new:
                await self._add_player(name)
            else:
                self._players.pop(name, None)
            return
        player = self._owned_by(event.sender)
        if player is None:
            return
        if event.member == "Seeked" and event.body:
            player.apply({"Position": event.body[0]})
        elif event.member == "PropertiesChanged" and len(event.body) >= 3:
            interface, changed, invalidated = event.body[:3]
            if interface != PLAYER_IFACE:
                return
            previous = player.status
            await self._merge(player, dict(changed), list(invalidated))
            if player.status == "playing" and previous != "playing":
                self._activity += 1
                player.activity = self._activity
                if self._preferred and player.base != self._preferred:
                    self._preferred = None  # un autre lecteur démarre : on le suit

    async def _merge(
        self, player: PlayerState, changed: dict[str, Any], invalidated: list[str]
    ) -> None:
        assert self._bus is not None
        for prop in invalidated:  # « ce champ a changé, relis-le »
            try:
                (value,) = await self._bus.call(
                    MPRIS_PREFIX + player.id,
                    OBJECT_PATH,
                    PROPS_IFACE,
                    "Get",
                    "ss",
                    [PLAYER_IFACE, prop],
                )
                changed[prop] = value
            except BusError:
                continue
        player.apply(changed)
        if {"PlaybackStatus", "Metadata", "Rate"} & changed.keys():
            await self._read_position(player)  # la position n'est pas signalée : on la relève

    # --- actions ---

    async def _call_player(
        self, player: PlayerState, member: str, signature: str = "", body: list[Any] | None = None
    ) -> None:
        assert self._bus is not None
        try:
            await self._bus.call(
                MPRIS_PREFIX + player.id, OBJECT_PATH, PLAYER_IFACE, member, signature, body
            )
        except BusError:
            raise ActionError("failed", f"{player.identity or player.id} ne répond pas.") from None

    async def play_pause(self, player: str | None) -> None:
        target = self._by_id(player)
        await self._call_player(target, "PlayPause")

    async def next(self, player: str | None) -> None:
        target = self._by_id(player)
        if not target.can_next:
            raise ActionError("unsupported", "Ce lecteur n'a pas de morceau suivant.")
        await self._call_player(target, "Next")

    async def previous(self, player: str | None) -> None:
        target = self._by_id(player)
        if not target.can_previous:
            raise ActionError("unsupported", "Ce lecteur n'a pas de morceau précédent.")
        await self._call_player(target, "Previous")

    async def seek(self, player: str | None, position_ms: int) -> None:
        target = self._by_id(player)
        if not target.can_seek:
            raise ActionError(
                "unsupported", "Ce lecteur ne permet pas de se déplacer dans le morceau."
            )
        if target.length_ms is not None:
            position_ms = min(position_ms, target.length_ms)
        if target.track_id:
            await self._call_player(
                target, "SetPosition", "ox", [target.track_id, position_ms * 1000]
            )
        else:
            current = target.position_ms or 0
            await self._call_player(target, "Seek", "x", [(position_ms - current) * 1000])

    def select(self, player: str) -> None:
        target = self._by_id(player)
        self._preferred = target.base

    async def position(self) -> dict[str, Any]:
        target = self._by_id(None)
        await self._read_position(target)
        return {"id": target.id, "status": target.status, "position_ms": target.position_ms}

    async def art(self, art: str) -> dict[str, str]:
        if art in self._art_cache:
            self._art_cache.move_to_end(art)
            return self._art_cache[art]
        url = next(
            (p.art_url for p in self._players.values() if p.art_url and art_id(p.art_url) == art),
            None,
        )
        if url is None:
            raise ActionError("unknown_art", "Cette pochette n'est plus d'actualité.")
        try:
            data = await asyncio.to_thread(fetch_art, url)
            mime, image = await asyncio.to_thread(prepare_art, data)
        except ArtError as exc:
            raise ActionError("art_failed", str(exc)) from None
        result = {"mime": mime, "data": base64.b64encode(image).decode("ascii")}
        self._art_cache[art] = result
        while len(self._art_cache) > 8:
            self._art_cache.popitem(last=False)
        return result


def _unavailable_message(exc: Exception) -> str:
    if isinstance(exc, BusError):
        return f"{exc} Nouvelle tentative dans {int(RECONNECT_SECONDS)} s."
    return "Le bus de session D-Bus est injoignable. Nouvelle tentative dans 10 s."


# --- actions ----------------------------------------------------------------------------------


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


PlayerId = Field(default=None, pattern=r"^[A-Za-z0-9_.-]{1,80}$")


class PlayerParams(_Strict):
    player: str | None = PlayerId


class SeekParams(_Strict):
    position_ms: int = Field(ge=0, le=86_400_000, strict=True)
    player: str | None = PlayerId


class SelectParams(_Strict):
    player: str = Field(pattern=r"^[A-Za-z0-9_.-]{1,80}$")


class ArtParams(_Strict):
    art: str = Field(pattern=r"^[0-9a-f]{16}$")


def register_music_actions(registry: ActionRegistry, backend: MusicBackend) -> None:
    async def play_pause(_ctx: ActionContext, params: PlayerParams) -> dict[str, bool]:
        await backend.play_pause(params.player)
        return {"done": True}

    async def next_track(_ctx: ActionContext, params: PlayerParams) -> dict[str, bool]:
        await backend.next(params.player)
        return {"done": True}

    async def previous_track(_ctx: ActionContext, params: PlayerParams) -> dict[str, bool]:
        await backend.previous(params.player)
        return {"done": True}

    async def seek(_ctx: ActionContext, params: SeekParams) -> dict[str, bool]:
        await backend.seek(params.player, params.position_ms)
        return {"done": True}

    async def select(_ctx: ActionContext, params: SelectParams) -> dict[str, bool]:
        backend.select(params.player)
        return {"done": True}

    async def position(_ctx: ActionContext, _params: NoParams) -> dict[str, Any]:
        return await backend.position()

    async def art(_ctx: ActionContext, params: ArtParams) -> dict[str, str]:
        return await backend.art(params.art)

    registry.register("music.play_pause", play_pause, params=PlayerParams, timeout=4.0)
    registry.register("music.next", next_track, params=PlayerParams, timeout=4.0)
    registry.register("music.previous", previous_track, params=PlayerParams, timeout=4.0)
    registry.register("music.seek", seek, params=SeekParams, timeout=4.0)
    registry.register("music.select", select, params=SelectParams)
    registry.register("music.position", position, timeout=4.0)
    registry.register("music.art", art, params=ArtParams, timeout=15.0)
