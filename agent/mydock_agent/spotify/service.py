"""Le service Spotify de l'agent : chercher, parcourir, jouer, lancer l'application.

Trois voies, de la plus directe à la plus lointaine :

- **MPRIS** (voir `collectors/music.py`) pour tout ce que fait déjà un lecteur : lecture, pause, suivant, position.
  Rien ne passe par le réseau ;
- **l'API web de Spotify** avec le jeton de l'utilisateur (voir `auth.py`) : recherche, albums, artistes, playlists,
  bibliothèque, et lecture d'un contenu précis sur l'appareil « ce PC » (Spotify Connect, compte Premium) ;
- **`OpenUri`** (MPRIS) quand l'API ne peut pas jouer (pas de Premium, appareil introuvable, pas de connexion) : le
  lecteur du PC ouvre lui-même l'album ou la playlist.

Tout est borné : des paramètres validés (identifiants de 22 caractères, adresses `spotify:` strictes), des appels
limités aux serveurs de Spotify, des pochettes téléchargées seulement depuis `scdn.co` et `spotifycdn.com`.
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import socket
import time
import urllib.parse
from collections import OrderedDict
from collections.abc import Awaitable, Callable
from typing import Any, Protocol

from mydock_agent.collectors.desktop import DesktopApp
from mydock_agent.collectors.music import ArtError, fetch_art, prepare_art
from mydock_agent.spotify import catalog
from mydock_agent.spotify.auth import (
    API_URL,
    AuthError,
    Credentials,
    TokenStore,
    refresh_token,
)
from mydock_agent.spotify.http import (
    HttpError,
    HttpRequest,
    HttpResponse,
    Transport,
    urllib_transport,
)
from mydock_common.actions import ActionError
from mydock_common.config import ConfigError

log = logging.getLogger(__name__)

PLAYER = (
    "spotify"  # le nom MPRIS (org.mpris.MediaPlayer2.spotify), identique en .deb, Snap et Flatpak
)
DESKTOP_IDS = (
    "spotify.desktop",
    "spotify_spotify.desktop",
    "spotify-client.desktop",
    "com.spotify.Client.desktop",
)
SEARCH_LIMIT = 10
LIBRARY_LIMIT = 50
CACHE_SECONDS = 60.0
ME_SECONDS = 120.0
LAUNCH_WAIT = 25.0
DEVICE_WAIT = 15.0
THUMB_SIZE = 160
LARGE_SIZE = 480
IMAGES_KEPT = 80
SEARCHES_KEPT = 16

MSG_LOGIN = "Spotify n'est pas connecté : sur le PC, lance « mydock-agent spotify login »."


class PlayerControl(Protocol):
    """Ce que le service demande au lecteur MPRIS (voir `MusicProvider`)."""

    def has_player(self, base: str) -> bool: ...
    async def open_uri(self, player: str | None, uri: str) -> None: ...


class AppLauncher(Protocol):
    """Ce que le service demande au lanceur d'applications (voir `AppsProvider`)."""

    @property
    def apps(self) -> list[DesktopApp]: ...
    async def refresh(self) -> bool: ...
    async def launch(self, app_id: str) -> dict[str, Any]: ...


class ApiError(Exception):
    """Spotify a répondu par une erreur (ou n'a pas répondu)."""

    def __init__(
        self, status: int, reason: str = "", message: str = "", retry_after: float | None = None
    ):
        super().__init__(f"{status} {reason} {message}".strip())
        self.status = status
        self.reason = reason
        self.message = message
        self.retry_after = retry_after


def describe(error: ApiError) -> ActionError:
    """L'erreur de l'API, en phrase pour l'écran (ce qu'il y a à faire, pas un code)."""
    text = error.message.lower()
    if error.status == 0:
        return ActionError("unavailable", "Spotify est injoignable depuis le PC.")
    if error.status == 401:
        return ActionError(
            "not_connected",
            "La connexion à Spotify a expiré : relance « mydock-agent spotify login » sur le PC.",
        )
    if error.status == 403:
        if error.reason == "PREMIUM_REQUIRED" or "premium" in text:
            return ActionError("premium", "Cette commande demande un compte Spotify Premium.")
        if "not registered" in text or "user management" in text or "allowlist" in text:
            return ActionError(
                "forbidden",
                "Ce compte Spotify n'est pas autorisé pour l'application : ajoute-le dans « User Management » "
                "du tableau de bord développeur.",
            )
        detail = f" ({error.message})" if error.message else ""
        return ActionError(
            "forbidden",
            f"Spotify refuse cette demande{detail}. Depuis février 2026 les applications en mode développement sont limitées.",
        )
    if error.status == 404:
        return ActionError("not_found", "Spotify ne trouve pas cet élément.")
    if error.status == 429:
        wait = int(error.retry_after or 5)
        return ActionError(
            "busy", f"Spotify demande de patienter {wait} s avant la prochaine requête."
        )
    if error.status >= 500:
        return ActionError(
            "unavailable", "Spotify a un problème en ce moment : réessaie dans un instant."
        )
    detail = f" : {error.message}" if error.message else ""
    return ActionError("failed", f"Spotify a répondu {error.status}{detail}.")


# --- choix de l'appareil et corps de la requête de lecture (fonctions pures) --------------------------


def choose_device(devices: Any, hostname: str) -> dict[str, Any] | None:
    """L'appareil Spotify Connect qui est ce PC : même nom que lui, sinon un « Computer » (actif d'abord)."""
    usable = (
        [
            d
            for d in devices
            if isinstance(d, dict) and isinstance(d.get("id"), str) and not d.get("is_restricted")
        ]
        if isinstance(devices, list)
        else []
    )
    named = [d for d in usable if str(d.get("name", "")).casefold() == hostname.casefold()]
    computers = [d for d in usable if d.get("type") == "Computer"]
    for group in (
        named,
        [d for d in computers if d.get("is_active")],
        computers,
        [d for d in usable if d.get("is_active")],
        usable,
    ):
        if group:
            return group[0]
    return None


def play_body(uri: str, context: str | None) -> dict[str, Any]:
    """Le corps de `PUT /me/player/play` : un contenu entier, ou un morceau (dans son album ou sa playlist)."""
    kind = (catalog.parse_uri(uri) or ("", ""))[0]
    if kind in ("album", "playlist", "artist", "show"):
        return {"context_uri": uri}
    wanted = catalog.parse_uri(context or "")
    if kind == "track" and wanted and wanted[0] in ("album", "playlist"):
        return {
            "context_uri": context,
            "offset": {"uri": uri},
        }  # l'API ne place pas un titre dans un artiste
    return {"uris": [uri]}


def find_app(apps: list[DesktopApp]) -> DesktopApp | None:
    """L'entrée de menu de Spotify : par son identifiant connu (.deb, Snap, Flatpak), sinon par son nom."""
    by_id = {app.id: app for app in apps}
    for ident in DESKTOP_IDS:
        if ident in by_id:
            return by_id[ident]
    return next((app for app in apps if app.name.casefold() == "spotify"), None)


# --- le service ----------------------------------------------------------------------------------


class SpotifyService:
    def __init__(
        self,
        *,
        music: PlayerControl,
        apps: AppLauncher,
        store: TokenStore | None = None,
        transport: Transport = urllib_transport,
        clock: Callable[[], float] = time.time,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        hostname: Callable[[], str] = socket.gethostname,
        fetch: Callable[[str], bytes] = fetch_art,
        prepare: Callable[[bytes, int], tuple[str, bytes]] = prepare_art,
        timeout: float = 10.0,
    ):
        self._music = music
        self._apps = apps
        self._store = store or TokenStore()
        self._transport = transport
        self._clock = clock
        self._sleep = sleep
        self._hostname = hostname
        self._fetch = fetch
        self._prepare = prepare
        self._timeout = timeout
        self._art = catalog.ArtRegistry()
        self._creds: Credentials | None = None
        self._stamp: int | None = None
        self._lock = asyncio.Lock()
        self._blocked_until = 0.0
        self._searches: OrderedDict[str, tuple[float, dict[str, Any]]] = OrderedDict()
        self._images: OrderedDict[tuple[str, bool], dict[str, str]] = OrderedDict()
        self._me: tuple[float, dict[str, Any]] | None = None

    # --- le jeton ---

    def _load(self) -> Credentials | None:
        """Le jeton enregistré (relu si `spotify login` l'a changé pendant que l'agent tournait)."""
        try:
            stamp = self._store.path.stat().st_mtime_ns
        except OSError:
            self._creds, self._stamp = None, None
            return None
        if self._creds is None or stamp != self._stamp:
            try:
                self._creds = self._store.load()
            except ConfigError as exc:
                raise ActionError("not_connected", str(exc)) from None
            self._stamp = stamp
            self._me = None
        return self._creds

    async def _token(self, *, force: bool = False) -> str:
        async with self._lock:
            creds = self._load()
            if creds is None:
                raise ActionError("not_connected", MSG_LOGIN)
            now = self._clock()
            if force or creds.expired(now):
                try:
                    await asyncio.to_thread(refresh_token, self._transport, creds, now)
                except AuthError as exc:
                    raise ActionError("not_connected", str(exc)) from None
                try:
                    self._store.save(creds)
                    self._stamp = self._store.path.stat().st_mtime_ns
                except OSError:
                    log.warning("Le jeton Spotify renouvelé n'a pas pu être enregistré.")
            return creds.access_token

    # --- les requêtes ---

    async def _request(
        self, method: str, path: str, params: dict[str, Any] | None = None, body: Any = None
    ) -> Any:
        """Un appel à l'API. Lève `ApiError` (erreur de Spotify) ou `ActionError` (pas connecté)."""
        wait = self._blocked_until - self._clock()
        if wait > 0:
            raise ApiError(429, retry_after=wait)
        for attempt in (1, 2):
            token = await self._token(force=attempt == 2)
            query = f"?{urllib.parse.urlencode(params)}" if params else ""
            headers = {"Authorization": f"Bearer {token}"}
            if body is not None:
                headers["Content-Type"] = "application/json"
            request = HttpRequest(
                method,
                f"{API_URL}{path}{query}",
                headers,
                json.dumps(body).encode() if body is not None else None,
            )
            try:
                response = await asyncio.to_thread(self._transport, request, self._timeout)
            except HttpError:
                raise ApiError(0) from None
            if response.status == 401 and attempt == 1:
                continue  # le jeton a été révoqué ou a expiré plus tôt que prévu : un renouvellement, puis on retente
            return self._answer(response)
        raise ApiError(401)  # pragma: no cover - la boucle rend toujours la main avant

    def _answer(self, response: HttpResponse) -> Any:
        try:
            payload = json.loads(response.body) if response.body else None
        except ValueError:
            payload = None
        if 200 <= response.status < 300:
            return payload
        error = payload.get("error") if isinstance(payload, dict) else None
        if isinstance(error, dict):
            reason, message = str(error.get("reason") or ""), str(error.get("message") or "")
        elif isinstance(error, str):
            reason, message = "", str((payload or {}).get("error_description") or error)
        else:
            reason = message = ""
        retry = None
        if response.status == 429:
            try:
                retry = float(response.headers.get("retry-after", "5"))
            except ValueError:
                retry = 5.0
            self._blocked_until = self._clock() + retry
        raise ApiError(response.status, reason, message[:160], retry)

    async def _get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        try:
            return await self._request("GET", path, params)
        except ApiError as exc:
            raise describe(exc) from None

    # --- état ---

    async def status(self) -> dict[str, Any]:
        """Où en est Spotify : installé, lancé, connecté (et à quel compte)."""
        await self._apps.refresh()
        result: dict[str, Any] = {
            "installed": find_app(self._apps.apps) is not None,
            "running": self._music.has_player(PLAYER),
            "configured": False,
            "connected": False,
            "user": None,
            "premium": None,
            "message": None,
        }
        try:
            result["configured"] = self._load() is not None
        except ActionError as exc:
            result["message"] = exc.message
            return result
        if not result["configured"]:
            result["message"] = MSG_LOGIN
            return result
        try:
            me = await self._profile()
        except ActionError as exc:
            result["message"] = exc.message
            return result
        result["connected"] = True
        result["user"] = catalog.text(me.get("display_name") or me.get("id"))
        product = me.get("product")
        result["premium"] = product == "premium" if isinstance(product, str) else None
        return result

    async def _profile(self) -> dict[str, Any]:
        if self._me and self._clock() - self._me[0] < ME_SECONDS:
            return self._me[1]
        me = await self._get("/me")
        profile = me if isinstance(me, dict) else {}
        self._me = (self._clock(), profile)
        return profile

    # --- chercher et parcourir ---

    async def search(self, query: str) -> dict[str, Any]:
        key = query.casefold()
        cached = self._searches.get(key)
        if cached and self._clock() - cached[0] < CACHE_SECONDS:
            return cached[1]
        raw = await self._get(
            "/search", {"q": query, "type": "track,album,artist,playlist", "limit": SEARCH_LIMIT}
        )
        result = {"query": query, **catalog.search_results(raw, self._art)}
        self._searches[key] = (self._clock(), result)
        while len(self._searches) > SEARCHES_KEPT:
            self._searches.popitem(last=False)
        return result

    async def view(self, kind: str, ident: str) -> dict[str, Any]:
        if kind == "album":
            view = catalog.album_view(await self._get(f"/albums/{ident}"), self._art)
        elif kind == "playlist":
            view = await self._playlist(ident)
        else:
            view = await self._artist(ident)
        if view is None:
            raise ActionError("not_found", "Spotify ne trouve pas cet élément.")
        return view

    async def _playlist(self, ident: str) -> dict[str, Any] | None:
        meta = await self._get(
            f"/playlists/{ident}",
            {"fields": "id,name,owner(display_name),images,tracks(total),items(total)"},
        )
        entries: Any = None
        for suffix in (
            "tracks",
            "items",
        ):  # le second nom : voir le guide de migration de février 2026
            try:
                entries = await self._request(
                    "GET", f"/playlists/{ident}/{suffix}", {"limit": LIBRARY_LIMIT}
                )
                break
            except ApiError as exc:
                if exc.status not in (400, 404, 410):
                    raise describe(exc) from None
        return catalog.playlist_view(meta, catalog.page_items(entries), self._art)

    async def _artist(self, ident: str) -> dict[str, Any] | None:
        meta = await self._get(f"/artists/{ident}")
        album_page, top = await asyncio.gather(
            self._get(
                f"/artists/{ident}/albums",
                {"include_groups": "album,single", "limit": SEARCH_LIMIT},
            ),
            self._get(f"/artists/{ident}/top-tracks", {"market": "from_token"}),
            return_exceptions=True,
        )
        albums = catalog.page_items(album_page) if not isinstance(album_page, BaseException) else []
        tracks: list[Any] = top.get("tracks", []) if isinstance(top, dict) else []
        if not tracks and isinstance(meta, dict) and meta.get("name"):
            # Certaines applications n'ont plus « top-tracks » : une recherche par artiste donne le même genre de liste.
            try:
                found = await self._get(
                    "/search",
                    {"q": f'artist:"{meta["name"]}"', "type": "track", "limit": SEARCH_LIMIT},
                )
                tracks = catalog.page_items(found, "tracks")
            except ActionError:
                tracks = []
        return catalog.artist_view(meta, albums, tracks, self._art)

    async def library(self, section: str) -> dict[str, Any]:
        paths: dict[str, tuple[str, dict[str, Any]]] = {
            "playlists": ("/me/playlists", {"limit": LIBRARY_LIMIT}),
            "liked": ("/me/tracks", {"limit": LIBRARY_LIMIT}),
            "albums": ("/me/albums", {"limit": LIBRARY_LIMIT}),
            "artists": ("/me/following", {"type": "artist", "limit": LIBRARY_LIMIT}),
            "recent": ("/me/player/recently-played", {"limit": LIBRARY_LIMIT}),
        }
        path, params = paths[section]
        return catalog.library_section(section, await self._get(path, params), self._art)

    async def image(self, art: str, *, large: bool = False) -> dict[str, str]:
        key = (art, large)
        if key in self._images:
            self._images.move_to_end(key)
            return self._images[key]
        url = self._art.url(art)
        if url is None:
            raise ActionError("unknown_art", "Cette pochette n'est plus d'actualité.")
        try:
            data = await asyncio.to_thread(self._fetch, url)
            mime, image = await asyncio.to_thread(
                self._prepare, data, LARGE_SIZE if large else THUMB_SIZE
            )
        except ArtError as exc:
            raise ActionError("art_failed", str(exc)) from None
        result = {"mime": mime, "data": base64.b64encode(image).decode("ascii")}
        self._images[key] = result
        while len(self._images) > IMAGES_KEPT:
            self._images.popitem(last=False)
        return result

    # --- lancer et jouer ---

    async def launch(self) -> dict[str, Any]:
        """Démarre l'application Spotify du PC (s'il ne tourne pas) et attend qu'il se manifeste."""
        if self._music.has_player(PLAYER):
            return {"running": True, "started": False}
        await self._apps.refresh()
        app = find_app(self._apps.apps)
        if app is None:
            raise ActionError(
                "not_installed",
                "Spotify n'est pas installé sur ce PC (ni en paquet .deb, ni en Snap, ni en Flatpak).",
            )
        await self._apps.launch(
            app.id
        )  # ActionError « launch_failed » avec la raison, le cas échéant
        waited = 0.0
        while not self._music.has_player(PLAYER):
            if waited >= LAUNCH_WAIT:
                raise ActionError(
                    "launch_timeout",
                    "Spotify a été lancé mais ne s'est pas encore annoncé : il démarre peut-être encore, réessaie dans un instant.",
                )
            await self._sleep(0.5)
            waited += 0.5
        return {"running": True, "started": True}

    async def play(self, uri: str, context: str | None = None) -> dict[str, Any]:
        """Joue `uri` (un morceau, un album, une playlist, un artiste), dans son `context` pour un morceau."""
        started = (await self.launch())["started"]
        if self._has_token():
            try:
                await self._play_with_api(uri, context)
                return {"via": "api", "started": started}
            except ApiError as exc:
                if exc.status == 429:
                    raise describe(exc) from None
                log.info("Lecture par l'API impossible (%s) : on passe par le lecteur du PC.", exc)
            except ActionError as exc:
                if exc.code != "not_connected":
                    raise
        # Le lecteur du PC ouvre lui-même ce qu'on lui montre : un morceau seul, ou le contenu entier (album, playlist).
        await self._music.open_uri(PLAYER, context or uri)
        return {"via": "player", "started": started}

    def _has_token(self) -> bool:
        try:
            return self._load() is not None
        except ActionError:
            return False

    async def _play_with_api(self, uri: str, context: str | None) -> None:
        device = await self._wait_device()
        await self._request(
            "PUT", "/me/player/play", {"device_id": device["id"]}, play_body(uri, context)
        )

    async def _wait_device(self) -> dict[str, Any]:
        """L'appareil Spotify Connect de ce PC : il apparaît quelques secondes après le lancement de l'application."""
        waited = 0.0
        while True:
            reply = await self._request("GET", "/me/player/devices")
            device = choose_device(
                reply.get("devices") if isinstance(reply, dict) else None, self._hostname()
            )
            if device is not None:
                return device
            if waited >= DEVICE_WAIT:
                raise ApiError(404, "NO_ACTIVE_DEVICE", "Aucun appareil Spotify Connect")
            await self._sleep(1.0)
            waited += 1.0
