"""Freetv : les chaînes de la Freebox, d'après sa liste de lecture (playlist M3U).

La Freebox publie sur le réseau local `http://mafreebox.freebox.fr/freeboxtv/playlist.m3u` : une ligne `#EXTINF` (le
numéro et le nom de la chaîne) puis l'adresse du flux, le plus souvent `rtsp://…/fbxtv_pub/stream?namespace=1&service=201
&flavour=sd`. Une même chaîne peut y figurer en plusieurs qualités (`flavour` : `ld`, `sd`, `hd`, `auto`).

Le dock lit cette liste lui-même et n'envoie à l'écran que des identifiants : c'est le dock qui retrouve l'adresse du flux
quand on touche une chaîne, jamais le navigateur qui la donne. La liste n'est lue que sur le réseau local : une adresse
qui mènerait ailleurs, ou une redirection, est refusée.
"""

from __future__ import annotations

import asyncio
import hashlib
import ipaddress
import re
import socket
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from typing import Any

DEFAULT_PLAYLIST_URL = "http://mafreebox.freebox.fr/freeboxtv/playlist.m3u"
MAX_BYTES = 1024 * 1024
TIMEOUT = 8.0
CACHE_SECONDS = 600.0
STREAM_SCHEMES = ("rtsp", "rtp", "udp", "http", "https")
FLAVOURS = ("ld", "sd", "hd", "auto")
#: Les qualités, de la préférée à la dernière : « standard » d'abord (l'écran du dock est petit, et un Pi 4 décode
#: sans effort une définition standard), « HD » quand on l'a demandé.
ORDER_STANDARD = ("sd", "auto", "ld", "hd")
ORDER_HD = ("hd", "auto", "sd", "ld")

_NUMBERED = re.compile(r"^\s*(\d{1,4})\s*[-–—:.)]\s*(.+?)\s*$")
_QUALITY_TAG = re.compile(r"\s*\((HD|SD|LD|Auto)\)\s*$", re.IGNORECASE)


class FreeboxError(Exception):
    """La liste n'a pas pu être lue ; le message est affichable."""


@dataclass(frozen=True)
class Channel:
    id: str
    number: int | None
    name: str
    #: qualité -> adresse du flux (jamais envoyée à l'écran)
    variants: dict[str, str] = field(default_factory=dict, compare=False)

    def public(self) -> dict[str, Any]:
        """Ce que voit l'écran : de quoi afficher et choisir, pas l'adresse du flux."""
        qualities = [q for q in FLAVOURS if q in self.variants]
        return {"id": self.id, "number": self.number, "name": self.name, "qualities": qualities}

    def stream(self, hd: bool = False) -> tuple[str, str]:
        """(qualité, adresse) à lire : la préférée de celles que la chaîne propose."""
        for quality in ORDER_HD if hd else ORDER_STANDARD:
            if quality in self.variants:
                return quality, self.variants[quality]
        quality, url = next(iter(self.variants.items()))
        return quality, url


def _split_title(title: str) -> tuple[int | None, str]:
    match = _NUMBERED.match(title)
    if match:
        return int(match.group(1)), match.group(2)
    return None, title.strip()


def _flavour_of(url: str, tag: str | None) -> str:
    query = urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)
    value = (query.get("flavour") or [""])[0].lower()
    if value in FLAVOURS:
        return value
    if tag and tag.lower() in FLAVOURS:
        return tag.lower()
    return "auto"


def _without_flavour(url: str) -> str:
    parts = urllib.parse.urlsplit(url)
    query = [
        (k, v)
        for k, v in urllib.parse.parse_qsl(parts.query, keep_blank_values=True)
        if k != "flavour"
    ]
    return urllib.parse.urlunsplit(parts._replace(query=urllib.parse.urlencode(query)))


def parse_playlist(text: str) -> list[Channel]:
    """Les chaînes d'une liste M3U, dans l'ordre de la liste ; une chaîne proposée en plusieurs qualités n'en fait qu'une."""
    grouped: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    title: str | None = None
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.startswith("#EXTINF"):
            title = line.partition(",")[2].strip() or None
            continue
        if line.startswith("#"):
            continue
        scheme = urllib.parse.urlsplit(line).scheme.lower()
        if scheme not in STREAM_SCHEMES or len(line) > 500 or any(c in line for c in " \t\x00"):
            title = None
            continue
        number, name = _split_title(title or "")
        tag_match = _QUALITY_TAG.search(name)
        tag = tag_match.group(1) if tag_match else None
        name = _QUALITY_TAG.sub("", name).strip() or f"Chaîne {len(order) + 1}"
        key = _without_flavour(line)
        entry = grouped.get(key)
        if entry is None:
            entry = grouped[key] = {"number": number, "name": name, "variants": {}}
            order.append(key)
        entry["variants"].setdefault(_flavour_of(line, tag), line)
        title = None
    channels = []
    for key in order:
        entry = grouped[key]
        digest = hashlib.sha1(key.encode()).hexdigest()[:10]
        channels.append(Channel(digest, entry["number"], entry["name"][:80], entry["variants"]))
    return channels


# --- lecture de la liste, sur le réseau local seulement -----------------------------------------------------------------


def valid_playlist_url(url: object) -> str:
    """L'adresse de la liste : http(s), sans identifiants ni espace ; sinon ValueError (message affichable)."""
    if not isinstance(url, str) or not url.strip():
        raise ValueError("Il faut l'adresse de la liste (http://…).")
    value = url.strip()
    parts = urllib.parse.urlsplit(value)
    if (
        parts.scheme not in ("http", "https")
        or not parts.hostname
        or parts.username
        or parts.password
        or len(value) > 300
        or any(c.isspace() or ord(c) < 32 for c in value)
    ):
        raise ValueError("L'adresse de la liste doit être http:// ou https://, sans identifiants.")
    return value


def _is_local(address: str) -> bool:
    ip = ipaddress.ip_address(address.split("%")[0])
    return ip.is_private or ip.is_loopback or ip.is_link_local


def _check_local(hostname: str, port: int) -> None:
    try:
        infos = socket.getaddrinfo(hostname, port, type=socket.SOCK_STREAM)
    except OSError:
        raise FreeboxError(
            f"« {hostname} » est introuvable : la Freebox est-elle sur le même réseau que le dock ?"
        ) from None
    if not infos or not all(_is_local(info[4][0]) for info in infos):
        raise FreeboxError(
            f"« {hostname} » n'est pas sur le réseau local : le dock ne lit la liste que chez toi."
        )


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # type: ignore[no-untyped-def]
        return None


def fetch_playlist(url: str, timeout: float = TIMEOUT) -> str:
    """Le texte de la liste ; lève FreeboxError. Bloquant : à lancer dans un fil."""
    try:
        value = valid_playlist_url(url)
    except ValueError as exc:
        raise FreeboxError(str(exc)) from None
    parts = urllib.parse.urlsplit(value)
    _check_local(parts.hostname or "", parts.port or (443 if parts.scheme == "https" else 80))
    opener = urllib.request.build_opener(_NoRedirect())
    request = urllib.request.Request(value, headers={"User-Agent": "mydock-dock"})
    try:
        with opener.open(request, timeout=timeout) as response:
            body = response.read(MAX_BYTES + 1)
    except urllib.error.HTTPError as exc:
        if 300 <= exc.code < 400:
            raise FreeboxError("La Freebox renvoie vers une autre adresse : refusé.") from None
        raise FreeboxError(
            f"La Freebox répond « {exc.code} {exc.reason} » à cette adresse."
        ) from None
    except (OSError, ValueError) as exc:
        reason = getattr(exc, "reason", exc)
        raise FreeboxError(f"La liste des chaînes est injoignable ({reason}).") from None
    if len(body) > MAX_BYTES:
        raise FreeboxError("La liste des chaînes est trop longue (1 Mo au plus).")
    text = body.decode("utf-8", "replace")
    if "#EXTM3U" not in text[:200] and "#EXTINF" not in text:
        raise FreeboxError("Cette adresse ne donne pas une liste de chaînes (M3U).")
    return text


class ChannelList:
    """Les chaînes en mémoire, relues au plus toutes les dix minutes (ou sur demande)."""

    def __init__(self, url_source: Any, *, clock: Any = time.monotonic) -> None:
        self._url = url_source  # callable -> str : l'adresse choisie dans Réglages
        self._clock = clock
        self._channels: list[Channel] = []
        self._fetched_at: float | None = None
        self._fetched_url: str | None = None
        self._lock = asyncio.Lock()

    @property
    def channels(self) -> list[Channel]:
        return list(self._channels)

    def find(self, channel_id: str) -> Channel | None:
        return next((c for c in self._channels if c.id == channel_id), None)

    async def get(self, *, refresh: bool = False) -> tuple[list[Channel], str | None]:
        """(chaînes, avertissement). En cas d'échec on garde la liste connue en le disant ; sans liste, FreeboxError."""
        async with self._lock:
            url = self._url()
            fresh = (
                self._fetched_at is not None
                and self._fetched_url == url
                and self._clock() - self._fetched_at < CACHE_SECONDS
            )
            if fresh and not refresh:
                return self.channels, None
            try:
                text = await asyncio.to_thread(fetch_playlist, url)
            except FreeboxError as exc:
                if self._channels and self._fetched_url == url:
                    return self.channels, f"{exc} (liste gardée de la dernière lecture)"
                raise
            channels = parse_playlist(text)
            if not channels:
                raise FreeboxError("La liste ne contient aucune chaîne.")
            self._channels, self._fetched_at, self._fetched_url = channels, self._clock(), url
            return self.channels, None
