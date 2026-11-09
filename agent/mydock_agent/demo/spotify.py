"""Démonstration de Spotify : un petit catalogue fictif, mêmes réponses que le vrai service, aucun réseau.

La recherche, les albums, les artistes, les playlists et la bibliothèque sortent de quelques lignes ci-dessous ;
« jouer » fait jouer le morceau au lecteur fictif (`DemoMusic`). L'interface ne sait pas qu'elle parle à une démonstration.
"""

from __future__ import annotations

import base64
import hashlib
import string
import unicodedata
from typing import Any

from mydock_agent.collectors.music import art_id
from mydock_agent.demo.music import DemoMusic, demo_art_svg
from mydock_agent.spotify.catalog import parse_uri
from mydock_common.actions import ActionError

_BASE62 = string.digits + string.ascii_letters

# artiste -> albums -> (titre, durée en secondes)
CATALOG: dict[str, dict[str, list[tuple[str, int]]]] = {
    "Les Fantômes": {
        "Néons": [
            ("Nuit blanche", 222),
            ("Lumières de ville", 198),
            ("Écho", 176),
            ("Sous les néons", 211),
        ],
        "Fréquences": [("Onde courte", 187), ("Signal", 205), ("Bruit blanc", 164)],
    },
    "Mizu": {
        "Saisons": [
            ("Sous la pluie de Kyoto", 187),
            ("Printemps", 172),
            ("Neige fine", 199),
            ("Les feuilles rouges", 233),
        ],
    },
    "Dalida Express": {
        "Kilomètres": [
            ("Autoroute 17", 241),
            ("Aire de repos", 188),
            ("Sortie 42", 215),
            ("Péage", 169),
        ],
    },
    "Aurore Vidal": {
        "Terminus": [("Le dernier métro", 203), ("Dernier train", 194), ("Quai 9", 228)],
        "Correspondances": [("Changement à Nation", 181), ("Ligne 4", 207)],
    },
}
#: Les appareils Spotify Connect de la démonstration : ce PC (son nom est celui de la machine), des enceintes en Wi-Fi…
DEVICES = (
    ("pc-demo", "Computer", 54),
    ("Salon (Sonos Era 100)", "Speaker", 35),
    ("Cuisine (Echo Dot)", "CastAudio", 20),
    ("iPhone de Marie", "Smartphone", 60),
    ("TV du salon", "TV", 25),
)
PLAYLISTS = {
    "Soirée calme": ("Spotify", ["Nuit blanche", "Printemps", "Quai 9", "Neige fine", "Écho"]),
    "Route de nuit": ("Toi", ["Autoroute 17", "Sortie 42", "Sous les néons", "Onde courte"]),
}


def spotify_id(key: str) -> str:
    """Un identifiant de 22 caractères, toujours le même pour la même clé."""
    number = int.from_bytes(hashlib.sha256(key.encode()).digest(), "big")
    out = []
    for _ in range(22):
        number, rest = divmod(number, 62)
        out.append(_BASE62[rest])
    return "".join(out)


def fold(text: str) -> str:
    return "".join(
        c for c in unicodedata.normalize("NFKD", text) if not unicodedata.combining(c)
    ).casefold()


class DemoSpotify:
    def __init__(self, music: DemoMusic) -> None:
        self._music = music
        self._arts: dict[str, tuple[int, str]] = {}  # identifiant de pochette -> (teinte, titre)
        self.tracks: list[dict[str, Any]] = []
        self.albums: dict[str, dict[str, Any]] = {}
        self.artists: dict[str, dict[str, Any]] = {}
        for a, (artist, albums) in enumerate(CATALOG.items()):
            artist_id = spotify_id(f"artist:{artist}")
            self.artists[artist_id] = {
                "kind": "artist",
                "id": artist_id,
                "uri": f"spotify:artist:{artist_id}",
                "name": artist,
                "genres": "pop, électro",
                "art": self._art(artist, a),
            }
            for b, (album, titles) in enumerate(albums.items()):
                album_id = spotify_id(f"album:{artist}:{album}")
                cover = self._art(album, a * 5 + b + 1)
                tracks = []
                for n, (title, seconds) in enumerate(titles, start=1):
                    ident = spotify_id(f"track:{artist}:{album}:{title}")
                    tracks.append({
                        "kind": "track", "id": ident, "uri": f"spotify:track:{ident}", "name": title, "artists": artist,
                        "album": album, "album_id": album_id, "duration_ms": seconds * 1000, "number": n,
                        "explicit": False, "playable": True, "art": cover, "seconds": seconds,
                    })  # fmt: skip
                self.tracks.extend(tracks)
                self.albums[album_id] = {
                    "kind": "album", "id": album_id, "uri": f"spotify:album:{album_id}", "name": album, "artists": artist,
                    "year": str(2018 + a + b), "type": "album", "tracks": len(tracks), "art": cover, "artist_id": artist_id,
                    "_tracks": tracks,
                }  # fmt: skip
        self.outputs: list[dict[str, Any]] = [
            {
                "id": hashlib.sha1(name.encode(), usedforsecurity=False).hexdigest(),
                "name": name,
                "type": kind,
                "active": index == 0,
                "restricted": False,
                "volume": volume,
                "can_volume": True,
                "this_pc": index == 0,
            }
            for index, (name, kind, volume) in enumerate(DEVICES)
        ]
        self.playlists: dict[str, dict[str, Any]] = {}
        for p, (name, (owner, titles)) in enumerate(PLAYLISTS.items()):
            ident = spotify_id(f"playlist:{name}")
            members = [t for title in titles for t in self.tracks if t["name"] == title]
            self.playlists[ident] = {
                "kind": "playlist", "id": ident, "uri": f"spotify:playlist:{ident}", "name": name, "owner": owner,
                "tracks": len(members), "art": self._art(name, 40 + p), "_tracks": members,
            }  # fmt: skip

    def _art(self, title: str, hue: int) -> str:
        ident = art_id(f"demo://spotify/{title}")
        self._arts[ident] = (hue, title)
        return ident

    @staticmethod
    def _public(item: dict[str, Any]) -> dict[str, Any]:
        return {
            k: v
            for k, v in item.items()
            if not k.startswith("_") and k not in ("seconds", "artist_id")
        }

    # --- SpotifyBackend ---

    async def status(self) -> dict[str, Any]:
        return {
            "installed": True,
            "running": True,
            "configured": True,
            "connected": True,
            "user": "Démo",
            "premium": True,
            "message": None,
        }

    async def search(self, query: str) -> dict[str, Any]:
        wanted = fold(query)
        match = lambda *fields: any(wanted in fold(f) for f in fields if f)  # noqa: E731
        return {
            "query": query,
            "tracks": [
                self._public(t) for t in self.tracks if match(t["name"], t["artists"], t["album"])
            ][:10],
            "albums": [
                self._public(a) for a in self.albums.values() if match(a["name"], a["artists"])
            ][:10],
            "artists": [a for a in self.artists.values() if match(a["name"])][:10],
            "playlists": [self._public(p) for p in self.playlists.values() if match(p["name"])][
                :10
            ],
        }

    async def view(self, kind: str, ident: str) -> dict[str, Any]:
        if kind == "album" and ident in self.albums:
            album = self.albums[ident]
            return {
                "kind": "album",
                "item": self._public(album),
                "tracks": [self._public(t) for t in album["_tracks"]],
            }
        if kind == "playlist" and ident in self.playlists:
            playlist = self.playlists[ident]
            return {
                "kind": "playlist",
                "item": self._public(playlist),
                "tracks": [self._public(t) for t in playlist["_tracks"]],
            }
        if kind == "artist" and ident in self.artists:
            artist = self.artists[ident]
            albums = [self._public(a) for a in self.albums.values() if a["artist_id"] == ident]
            top = [self._public(t) for t in self.tracks if t["artists"] == artist["name"]][:5]
            return {"kind": "artist", "item": artist, "albums": albums, "tracks": top}
        raise ActionError("not_found", "Spotify ne trouve pas cet élément.")

    async def library(self, section: str) -> dict[str, Any]:
        if section == "playlists":
            return {
                "section": section,
                "kind": "playlist",
                "items": [self._public(p) for p in self.playlists.values()],
            }
        if section == "albums":
            return {
                "section": section,
                "kind": "album",
                "items": [self._public(a) for a in self.albums.values()],
            }
        if section == "artists":
            return {"section": section, "kind": "artist", "items": list(self.artists.values())}
        count = 5 if section == "recent" else 12
        return {
            "section": section,
            "kind": "track",
            "items": [self._public(t) for t in self.tracks[:count]],
        }

    async def image(self, art: str, *, large: bool = False) -> dict[str, str]:
        if art not in self._arts:
            raise ActionError("unknown_art", "Cette pochette n'est plus d'actualité.")
        hue, title = self._arts[art]
        svg = demo_art_svg(hue, title)
        return {"mime": "image/svg+xml", "data": base64.b64encode(svg.encode()).decode()}

    async def launch(self) -> dict[str, Any]:
        return {"running": True, "started": False}

    async def play(self, uri: str, context: str | None = None) -> dict[str, Any]:
        kind, ident = parse_uri(uri) or ("", "")
        if kind == "track":
            track = next((t for t in self.tracks if t["id"] == ident), None)
        elif kind == "album" and ident in self.albums:
            track = self.albums[ident]["_tracks"][0]
        elif kind == "playlist" and ident in self.playlists:
            track = self.playlists[ident]["_tracks"][0]
        elif kind == "artist" and ident in self.artists:
            track = next(t for t in self.tracks if t["artists"] == self.artists[ident]["name"])
        else:
            track = None
        if track is None:
            raise ActionError("not_found", "Spotify ne trouve pas cet élément.")
        self._music.play_track(
            track["name"], track["artists"], track["album"], track["seconds"], track["album"]
        )
        return {"via": "api", "started": False}

    async def devices(self) -> dict[str, Any]:
        ordered = sorted(self.outputs, key=lambda d: (not d["this_pc"], d["name"].casefold()))
        return {"devices": [dict(d) for d in ordered]}

    def _output(self, device_id: str) -> dict[str, Any]:
        found = next((d for d in self.outputs if d["id"] == device_id), None)
        if found is None:
            raise ActionError(
                "unknown_device",
                "Cet appareil n'est plus visible : ouvre la liste pour l'actualiser.",
            )
        return found

    async def transfer(self, device_id: str, play: bool = True) -> dict[str, Any]:
        target = self._output(device_id)
        for device in self.outputs:
            device["active"] = device is target
        return {"device": device_id}

    async def set_volume(self, device_id: str, percent: int) -> dict[str, Any]:
        self._output(device_id)["volume"] = percent
        return {"device": device_id, "volume": percent}
