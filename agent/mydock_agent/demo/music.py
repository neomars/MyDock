"""Démonstration de « music » : deux lecteurs fictifs (Spotify qui joue, Brave en pause).

Même format d'état que le vrai fournisseur MPRIS (`PlayerState`, `build_snapshot`) et mêmes
actions `music.*` : l'interface ne sait pas qu'elle parle à une démonstration.
"""

from __future__ import annotations

import asyncio
import base64
import time
from typing import Any

from mydock_agent.collectors.music import (
    PlayerState,
    art_id,
    base_id,
    build_snapshot,
    choose_active,
)
from mydock_agent.hub import Provider, Publish
from mydock_common.actions import ActionError

# (titre, artiste, album, durée en secondes)
TRACKS = (
    ("Nuit blanche", "Les Fantômes", "Néons", 222),
    ("Sous la pluie de Kyoto", "Mizu", "Saisons", 187),
    ("Autoroute 17", "Dalida Express", "Kilomètres", 241),
    ("Le dernier métro", "Aurore Vidal", "Terminus", 203),
)
VIDEO = ("Documentaire : les océans", "Chaîne Nature", "", 3_300)


def demo_art_svg(index: int, title: str) -> str:
    """Une pochette fictive : dégradé et initiales du titre (aucun fichier, aucun réseau)."""
    hue = (index * 67 + 200) % 360
    initials = "".join(word[0] for word in title.split()[:2] if word[0].isalpha()).upper()
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 240 240">'
        '<defs><linearGradient id="g" x1="0" y1="0" x2="1" y2="1">'
        f'<stop offset="0" stop-color="hsl({hue} 70% 55%)"/>'
        f'<stop offset="1" stop-color="hsl({(hue + 50) % 360} 70% 28%)"/></linearGradient></defs>'
        '<rect width="240" height="240" fill="url(#g)"/>'
        '<text x="120" y="145" font-family="sans-serif" font-size="84" font-weight="700" '
        f'text-anchor="middle" fill="rgba(255,255,255,.85)">{initials}</text></svg>'
    )


def _metadata(track: tuple[str, str, str, int], url: str, track_id: str) -> dict[str, Any]:
    title, artist, album, length = track
    return {
        "mpris:trackid": track_id,
        "mpris:length": length * 1_000_000,
        "mpris:artUrl": url,
        "xesam:title": title,
        "xesam:artist": [artist],
        "xesam:album": album,
    }


class DemoMusic(Provider):
    topic = "music"
    lazy = True

    def __init__(self) -> None:
        self._index = 0
        self._preferred: str | None = None
        self._activity = 1
        self._changed = asyncio.Event()
        self.spotify = PlayerState(
            id="spotify",
            owner=":demo.1",
            identity="Spotify",
            can_next=True,
            can_previous=True,
            can_seek=True,
            activity=1,
        )
        self.brave = PlayerState(
            id="brave.instance4242",
            owner=":demo.2",
            identity="Brave",
            can_seek=True,
        )
        self.spotify.status = "playing"
        self.spotify.shuffle = False  # Spotify propose lecture aléatoire et répétition ; Brave non
        self.spotify.loop = "none"
        self._custom_art: dict[
            str, str
        ] = {}  # pochettes des morceaux choisis depuis la page Spotify
        self._load(self.spotify, 0)
        self.brave.status = "paused"
        self.brave.apply(
            {
                "Metadata": _metadata(VIDEO, "demo://art/video", "/org/chromium/video/1"),
                "Position": 754_000_000,
            }
        )

    @property
    def players(self) -> list[PlayerState]:
        return [self.spotify, self.brave]

    def _load(self, player: PlayerState, index: int) -> None:
        self._index = index % len(TRACKS)
        player.apply(
            {
                "Metadata": _metadata(
                    TRACKS[self._index],
                    f"demo://art/{self._index}",
                    f"/demo/track/{self._index}",
                ),
                "Position": 0,
            }
        )

    def _pick(self, player: str | None) -> PlayerState:
        if player:
            for candidate in self.players:
                if candidate.id == player or candidate.base == player:
                    return candidate
            raise ActionError("no_player", "Ce lecteur n'est plus là.")
        active = choose_active(self.players, self._preferred)
        assert active is not None
        return active

    def _freeze(self, player: PlayerState) -> None:
        """Fige la position actuelle avant un changement d'état (lecture, pause, déplacement)."""
        now = time.monotonic()
        current = player.current_position_ms(now)
        if current is not None:
            player.apply({"Position": current * 1000})

    def snapshot(self) -> dict[str, Any]:
        return build_snapshot(self.players, self._preferred)

    # --- actions ---

    async def play_pause(self, player: str | None) -> None:
        target = self._pick(player)
        self._freeze(target)
        target.status = "paused" if target.status == "playing" else "playing"
        if target.status == "playing":
            self._activity += 1
            target.activity = self._activity
            if self._preferred and target.base != self._preferred:
                self._preferred = None
        self._changed.set()

    async def next(self, player: str | None) -> None:
        target = self._pick(player)
        if not target.can_next:
            raise ActionError("unsupported", "Ce lecteur n'a pas de morceau suivant.")
        self._load(target, self._index + 1)
        self._changed.set()

    async def previous(self, player: str | None) -> None:
        target = self._pick(player)
        if not target.can_previous:
            raise ActionError("unsupported", "Ce lecteur n'a pas de morceau précédent.")
        self._load(target, self._index - 1)
        self._changed.set()

    async def seek(self, player: str | None, position_ms: int) -> None:
        target = self._pick(player)
        if not target.can_seek:
            raise ActionError(
                "unsupported", "Ce lecteur ne permet pas de se déplacer dans le morceau."
            )
        if target.length_ms is not None:
            position_ms = min(position_ms, target.length_ms)
        target.apply({"Position": position_ms * 1000})
        self._changed.set()

    async def set_shuffle(self, player: str | None, enabled: bool) -> None:
        target = self._pick(player)
        if target.shuffle is None:
            raise ActionError("unsupported", "Ce lecteur n'a pas de lecture aléatoire.")
        target.shuffle = enabled
        self._changed.set()

    async def set_loop(self, player: str | None, mode: str) -> None:
        target = self._pick(player)
        if target.loop is None:
            raise ActionError("unsupported", "Ce lecteur n'a pas de répétition.")
        target.loop = mode
        self._changed.set()

    async def open_uri(self, player: str | None, uri: str) -> None:
        raise ActionError(
            "unsupported", "La démonstration joue par la page Spotify, pas par une adresse."
        )

    def has_player(self, base: str) -> bool:
        return any(p.base == base for p in self.players)

    def play_track(self, title: str, artist: str, album: str, seconds: int, art_key: str) -> None:
        """Fait jouer à « Spotify » un morceau de la démonstration (choisi dans la recherche ou la bibliothèque)."""
        url = f"demo://art/{art_key}"
        self._custom_art[art_id(url)] = demo_art_svg(sum(map(ord, art_key)) % 360, album or title)
        self.spotify.apply(
            {
                "Metadata": _metadata(
                    (title, artist, album, seconds), url, f"/demo/track/{art_key}"
                ),
                "Position": 0,
            }
        )
        self.spotify.status = "playing"
        self._activity += 1
        self.spotify.activity = self._activity
        self._preferred = None
        self._changed.set()

    def select(self, player: str) -> None:
        self._preferred = base_id(self._pick(player).id)
        self._changed.set()

    async def position(self) -> dict[str, Any]:
        target = self._pick(None)
        return {
            "id": target.id,
            "status": target.status,
            "position_ms": target.current_position_ms(time.monotonic()),
        }

    async def art(self, art: str) -> dict[str, str]:
        for index, track in enumerate(TRACKS):
            if art_id(f"demo://art/{index}") == art:
                svg = demo_art_svg(index, track[0])
                return {"mime": "image/svg+xml", "data": base64.b64encode(svg.encode()).decode()}
        if art in self._custom_art:
            svg = self._custom_art[art]
            return {"mime": "image/svg+xml", "data": base64.b64encode(svg.encode()).decode()}
        if art == art_id("demo://art/video"):
            svg = demo_art_svg(9, VIDEO[0])
            return {"mime": "image/svg+xml", "data": base64.b64encode(svg.encode()).decode()}
        raise ActionError("unknown_art", "Cette pochette n'est plus d'actualité.")

    # --- fournisseur ---

    def _seconds_to_end(self) -> float | None:
        player = self.spotify
        if player.status != "playing" or player.length_ms is None:
            return None
        remaining = player.length_ms - (player.current_position_ms(time.monotonic()) or 0)
        return max(0.05, remaining / 1000)

    async def run(self, publish: Publish) -> None:
        while True:
            await publish(self.snapshot())
            try:
                await asyncio.wait_for(self._changed.wait(), self._seconds_to_end())
            except TimeoutError:
                self._load(self.spotify, self._index + 1)  # fin du morceau : le suivant
            self._changed.clear()
            await asyncio.sleep(0.02)
