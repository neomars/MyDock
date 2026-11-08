"""Les actions `spotify.*` : la liste blanche de ce que le dock peut demander à Spotify.

Chaque paramètre est validé avant d'arriver au service : des identifiants de 22 caractères, des adresses
`spotify:<type>:<id>` strictes, une requête de recherche courte. Le dock n'envoie jamais d'adresse web.
"""

from __future__ import annotations

from typing import Any, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, field_validator

from mydock_common.actions import ActionContext, ActionRegistry, NoParams

ID = r"[A-Za-z0-9]{22}"


class SpotifyBackend(Protocol):
    """Ce que les actions `spotify.*` attendent d'un fournisseur (réel ou de démonstration)."""

    async def status(self) -> dict[str, Any]: ...
    async def search(self, query: str) -> dict[str, Any]: ...
    async def view(self, kind: str, ident: str) -> dict[str, Any]: ...
    async def library(self, section: str) -> dict[str, Any]: ...
    async def image(self, art: str, *, large: bool = False) -> dict[str, str]: ...
    async def launch(self) -> dict[str, Any]: ...
    async def play(self, uri: str, context: str | None = None) -> dict[str, Any]: ...


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SearchParams(_Strict):
    query: str = Field(min_length=1, max_length=120)

    @field_validator("query")
    @classmethod
    def tidy(cls, value: str) -> str:
        text = " ".join(value.split())  # espaces et sauts de ligne ramenés à un espace
        if not text or any(ord(c) < 32 or ord(c) == 127 for c in text):
            raise ValueError("requête vide ou illisible")
        return text


class ViewParams(_Strict):
    kind: Literal["album", "artist", "playlist"]
    id: str = Field(pattern=f"^{ID}$")


class LibraryParams(_Strict):
    section: Literal["playlists", "liked", "albums", "artists", "recent"]


class ImageParams(_Strict):
    art: str = Field(pattern=r"^[0-9a-f]{16}$")
    large: bool = Field(default=False, strict=True)


class PlayParams(_Strict):
    uri: str = Field(pattern=f"^spotify:(track|album|artist|playlist|episode|show):{ID}$")
    context: str | None = Field(default=None, pattern=f"^spotify:(album|playlist):{ID}$")


def register_spotify_actions(registry: ActionRegistry, backend: SpotifyBackend) -> None:
    async def status(_ctx: ActionContext, _params: NoParams) -> dict[str, Any]:
        return await backend.status()

    async def search(_ctx: ActionContext, params: SearchParams) -> dict[str, Any]:
        return await backend.search(params.query)

    async def view(_ctx: ActionContext, params: ViewParams) -> dict[str, Any]:
        return await backend.view(params.kind, params.id)

    async def library(_ctx: ActionContext, params: LibraryParams) -> dict[str, Any]:
        return await backend.library(params.section)

    async def image(_ctx: ActionContext, params: ImageParams) -> dict[str, str]:
        return await backend.image(params.art, large=params.large)

    async def launch(_ctx: ActionContext, _params: NoParams) -> dict[str, Any]:
        return await backend.launch()

    async def play(_ctx: ActionContext, params: PlayParams) -> dict[str, Any]:
        return await backend.play(params.uri, params.context)

    registry.register("spotify.status", status, timeout=15.0)
    registry.register("spotify.search", search, params=SearchParams, timeout=20.0)
    registry.register("spotify.view", view, params=ViewParams, timeout=25.0)
    registry.register("spotify.library", library, params=LibraryParams, timeout=20.0)
    registry.register("spotify.image", image, params=ImageParams, timeout=15.0)
    registry.register("spotify.launch", launch, timeout=40.0)
    registry.register("spotify.play", play, params=PlayParams, timeout=60.0)
