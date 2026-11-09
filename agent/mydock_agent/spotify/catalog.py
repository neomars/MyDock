"""Ce que l'API de Spotify renvoie, remis en forme pour l'écran : des fonctions pures, aucun accès réseau.

L'API a des réponses très riches (marchés, empreintes, liens…) et elle change : en février 2026 Spotify a retiré des
champs et renommé des objets pour les applications en « mode développement ». Rien n'est donc supposé ici : chaque
champ est lu avec prudence, et un élément incomplet (titre local, épisode, élément supprimé) est écarté sans faire
échouer toute la liste.

Chaque élément porte le nécessaire à l'écran : un `id`, une adresse `uri` à jouer, des textes courts, et `art`, un
identifiant opaque de pochette (`ArtRegistry`) : le dock ne reçoit jamais d'adresse web, il redemande l'image à
l'agent, qui la télécharge (voir `SpotifyService.image`).
"""

from __future__ import annotations

import re
import urllib.parse
from collections import OrderedDict
from typing import Any

from mydock_agent.collectors.music import art_id, clean_text

ID_RE = re.compile(r"^[A-Za-z0-9]{22}$")
URI_RE = re.compile(r"^spotify:(track|album|artist|playlist|episode|show):([A-Za-z0-9]{22})$")
TEXT_LIMIT = 160
GENRES_SHOWN = 3
ART_REMEMBERED = (
    800  # adresses de pochettes gardées en mémoire : les listes en montrent quelques dizaines
)
DEVICE_ID_RE = re.compile(r"^[A-Za-z0-9_.:-]{8,128}$")
#: Les types d'appareils que Spotify Connect annonce ; tout autre type devient « Unknown ».
DEVICE_TYPES = frozenset(
    {
        "Computer",
        "Tablet",
        "Smartphone",
        "Speaker",
        "CastAudio",
        "CastVideo",
        "TV",
        "AVR",
        "STB",
        "AudioDongle",
        "GameConsole",
        "Automobile",
        "Unknown",
    }
)


def parse_uri(uri: str) -> tuple[str, str] | None:
    """« spotify:album:4aawyAB9vmqN3uQ7FjRGTy » -> ("album", "4aawyAB9vmqN3uQ7FjRGTy")."""
    match = URI_RE.fullmatch(uri or "")
    return (match.group(1), match.group(2)) if match else None


def image_host_ok(url: str) -> bool:
    """Les pochettes de Spotify viennent de `*.scdn.co` et `*.spotifycdn.com`, en https : rien d'autre n'est ouvert."""
    parts = urllib.parse.urlsplit(url)
    host = (parts.hostname or "").lower()
    return parts.scheme == "https" and (
        host in ("scdn.co", "spotifycdn.com") or host.endswith((".scdn.co", ".spotifycdn.com"))
    )


class ArtRegistry:
    """Les adresses de pochettes déjà annoncées à l'écran, par identifiant opaque (les plus récentes d'abord gardées)."""

    def __init__(self, limit: int = ART_REMEMBERED):
        self._urls: OrderedDict[str, str] = OrderedDict()
        self._limit = limit

    def register(self, url: Any) -> str | None:
        if not isinstance(url, str) or not image_host_ok(url):
            return None
        ident = art_id(url)
        self._urls[ident] = url
        self._urls.move_to_end(ident)
        while len(self._urls) > self._limit:
            self._urls.popitem(last=False)
        return ident

    def url(self, ident: str) -> str | None:
        return self._urls.get(ident)


def pick_image(images: Any, want: int = 160) -> str | None:
    """L'adresse de la plus petite image qui atteint `want` pixels de large, sinon de la plus grande."""
    candidates: list[tuple[int, str]] = []
    for image in images if isinstance(images, list) else []:
        if isinstance(image, dict) and isinstance(image.get("url"), str):
            width = image.get("width")
            candidates.append((width if isinstance(width, int) else 0, image["url"]))
    if not candidates:
        return None
    big_enough = sorted(c for c in candidates if c[0] >= want)
    return (big_enough[0] if big_enough else max(candidates))[1]


def text(value: Any) -> str | None:
    return clean_text(value, TEXT_LIMIT)


def artists_text(artists: Any) -> str | None:
    names = (
        [text(a.get("name")) for a in artists if isinstance(a, dict)]
        if isinstance(artists, list)
        else []
    )
    return ", ".join(n for n in names if n) or None


def valid_id(value: Any) -> str | None:
    return value if isinstance(value, str) and ID_RE.fullmatch(value) else None


def valid_device_id(value: Any) -> str | None:
    return value if isinstance(value, str) and DEVICE_ID_RE.fullmatch(value) else None


# --- éléments ------------------------------------------------------------------------------------


def track_item(
    raw: Any, art: ArtRegistry, *, album: dict[str, Any] | None = None
) -> dict[str, Any] | None:
    """Un morceau, ou None si ce n'en est pas un (titre local, épisode, élément supprimé…)."""
    if not isinstance(raw, dict):
        return None
    ident = valid_id(raw.get("id"))
    if ident is None or raw.get("type") not in (None, "track"):
        return None
    source = raw.get("album") if isinstance(raw.get("album"), dict) else (album or {})
    duration = raw.get("duration_ms")
    number = raw.get("track_number")
    return {
        "kind": "track",
        "id": ident,
        "uri": f"spotify:track:{ident}",
        "name": text(raw.get("name")) or "Sans titre",
        "artists": artists_text(raw.get("artists")),
        "album": text(source.get("name")),
        "album_id": valid_id(source.get("id")),
        "duration_ms": duration if isinstance(duration, int) and duration > 0 else None,
        "number": number if isinstance(number, int) and number > 0 else None,
        "explicit": raw.get("explicit") is True,
        "playable": raw.get("is_playable") is not False,
        "art": art.register(pick_image(source.get("images"))),
    }


def album_item(raw: Any, art: ArtRegistry) -> dict[str, Any] | None:
    if not isinstance(raw, dict):
        return None
    ident = valid_id(raw.get("id"))
    if ident is None:
        return None
    total = raw.get("total_tracks")
    year = str(raw.get("release_date") or "")[:4]
    return {
        "kind": "album",
        "id": ident,
        "uri": f"spotify:album:{ident}",
        "name": text(raw.get("name")) or "Sans titre",
        "artists": artists_text(raw.get("artists")),
        "year": year if year.isdigit() else None,
        "type": raw.get("album_type")
        if raw.get("album_type") in ("album", "single", "compilation")
        else None,
        "tracks": total if isinstance(total, int) and total >= 0 else None,
        "art": art.register(pick_image(raw.get("images"))),
    }


def artist_item(raw: Any, art: ArtRegistry) -> dict[str, Any] | None:
    if not isinstance(raw, dict):
        return None
    ident = valid_id(raw.get("id"))
    if ident is None:
        return None
    genres = raw.get("genres")
    shown = (
        [g for g in genres if isinstance(g, str)][:GENRES_SHOWN] if isinstance(genres, list) else []
    )
    return {
        "kind": "artist",
        "id": ident,
        "uri": f"spotify:artist:{ident}",
        "name": text(raw.get("name")) or "Artiste inconnu",
        "genres": text(", ".join(shown)) if shown else None,
        "art": art.register(pick_image(raw.get("images"))),
    }


def playlist_item(raw: Any, art: ArtRegistry) -> dict[str, Any] | None:
    if not isinstance(raw, dict):
        return None
    ident = valid_id(raw.get("id"))
    if ident is None:
        return None
    owner = raw.get("owner") if isinstance(raw.get("owner"), dict) else {}
    counted = raw.get("tracks") if isinstance(raw.get("tracks"), dict) else raw.get("items")
    total = counted.get("total") if isinstance(counted, dict) else None
    return {
        "kind": "playlist",
        "id": ident,
        "uri": f"spotify:playlist:{ident}",
        "name": text(raw.get("name")) or "Playlist sans nom",
        "owner": text(owner.get("display_name")),
        "tracks": total if isinstance(total, int) and total >= 0 else None,
        "art": art.register(pick_image(raw.get("images"))),
    }


def device_item(raw: Any) -> dict[str, Any] | None:
    """Un appareil Spotify Connect (ce PC, une enceinte, un téléphone…), ou None s'il est inutilisable."""
    if not isinstance(raw, dict):
        return None
    ident = valid_device_id(raw.get("id"))
    if ident is None:
        return None
    kind = raw.get("type")
    volume = raw.get("volume_percent")
    return {
        "id": ident,
        "name": text(raw.get("name")) or "Appareil sans nom",
        "type": kind if kind in DEVICE_TYPES else "Unknown",
        "active": raw.get("is_active") is True,
        # Un appareil « restreint » n'accepte aucune commande à distance (enceinte d'un autre compte, par exemple).
        "restricted": raw.get("is_restricted") is True,
        "volume": volume if isinstance(volume, int) and 0 <= volume <= 100 else None,
        "can_volume": raw.get("supports_volume") is not False,
    }


def page_items(raw: Any, key: str | None = None) -> list[Any]:
    """Les éléments d'une page de l'API (`{"items": [...]}`), éventuellement rangée sous une clé (`"tracks"`)."""
    holder = raw.get(key) if key and isinstance(raw, dict) else raw
    items = holder.get("items") if isinstance(holder, dict) else None
    return items if isinstance(items, list) else []


def _compact(items: list[dict[str, Any] | None]) -> list[dict[str, Any]]:
    return [i for i in items if i is not None]


# --- réponses de l'API ---------------------------------------------------------------------------


def search_results(raw: Any, art: ArtRegistry) -> dict[str, list[dict[str, Any]]]:
    return {
        "tracks": _compact([track_item(t, art) for t in page_items(raw, "tracks")]),
        "albums": _compact([album_item(a, art) for a in page_items(raw, "albums")]),
        "artists": _compact([artist_item(a, art) for a in page_items(raw, "artists")]),
        "playlists": _compact([playlist_item(p, art) for p in page_items(raw, "playlists")]),
    }


def album_view(raw: Any, art: ArtRegistry) -> dict[str, Any] | None:
    item = album_item(raw, art)
    if item is None:
        return None
    tracks = _compact([track_item(t, art, album=raw) for t in page_items(raw, "tracks")])
    return {"kind": "album", "item": item, "tracks": tracks}


def entries_tracks(entries: list[Any], art: ArtRegistry) -> list[dict[str, Any]]:
    """Les morceaux d'une liste d'entrées de playlist ou de « titres likés » : `{"track": {...}}` (ou `{"item": ...}`)."""
    found: list[dict[str, Any] | None] = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        found.append(track_item(entry.get("track") or entry.get("item"), art))
    return _compact(found)


def playlist_view(meta: Any, entries: list[Any], art: ArtRegistry) -> dict[str, Any] | None:
    item = playlist_item(meta, art)
    if item is None:
        return None
    return {"kind": "playlist", "item": item, "tracks": entries_tracks(entries, art)}


def artist_view(
    meta: Any, albums: list[Any], tracks: list[Any], art: ArtRegistry
) -> dict[str, Any] | None:
    item = artist_item(meta, art)
    if item is None:
        return None
    return {
        "kind": "artist",
        "item": item,
        "albums": _compact([album_item(a, art) for a in albums]),
        "tracks": _compact([track_item(t, art) for t in tracks]),
    }


def library_section(section: str, raw: Any, art: ArtRegistry) -> dict[str, Any]:
    """Une rubrique de « Ta bibliothèque » : `{"section": "...", "kind": "...", "items": [...]}`."""
    if section == "playlists":
        return {
            "section": section,
            "kind": "playlist",
            "items": _compact([playlist_item(p, art) for p in page_items(raw)]),
        }
    if section == "albums":
        albums = [e.get("album") for e in page_items(raw) if isinstance(e, dict)]
        return {
            "section": section,
            "kind": "album",
            "items": _compact([album_item(a, art) for a in albums]),
        }
    if section == "artists":
        return {
            "section": section,
            "kind": "artist",
            "items": _compact([artist_item(a, art) for a in page_items(raw, "artists")]),
        }
    tracks = entries_tracks(page_items(raw), art)
    if section == "recent":
        seen: set[str] = set()
        tracks = [
            t for t in tracks if not (t["id"] in seen or seen.add(t["id"]))
        ]  # un titre réécouté n'apparaît qu'une fois
    return {"section": section, "kind": "track", "items": tracks}
