"""Le service Spotify : requêtes à l'API (simulée), renouvellement du jeton, lancement, lecture, actions `spotify.*`."""

import json
import os
import urllib.parse
from pathlib import Path

import pytest

from mydock_agent.collectors.desktop import DesktopApp
from mydock_agent.collectors.music import ArtError
from mydock_agent.spotify import service as svc
from mydock_agent.spotify.actions import register_spotify_actions
from mydock_agent.spotify.auth import Credentials, TokenStore
from mydock_agent.spotify.http import HttpError, HttpRequest, HttpResponse
from mydock_agent.spotify.service import (
    MSG_LOGIN,
    ApiError,
    SpotifyService,
    choose_device,
    describe,
    find_app,
    play_body,
)
from mydock_common.actions import ActionContext, ActionError, ActionRegistry
from mydock_common.protocol import Act

CLIENT_ID = "0123456789abcdef0123456789abcdef"
ID_A = "4aawyAB9vmqN3uQ7FjRGTy"
ID_B = "0ETFjACtuP2ADo6LFhL6HN"
ID_C = "1301WleyT98MSxVHPZCA6M"
TRACK = f"spotify:track:{ID_B}"
ALBUM = f"spotify:album:{ID_A}"
SNAP = DesktopApp(
    "spotify_spotify.desktop",
    Path("/var/lib/snapd/desktop/applications/spotify_spotify.desktop"),
    "Spotify",
    None,
    None,
    False,
)
DEB = DesktopApp(
    "spotify.desktop", Path("/usr/share/applications/spotify.desktop"), "Spotify", None, None, False
)


def reply(status=200, payload=None, headers=None):
    body = json.dumps(payload if payload is not None else {}).encode()
    return HttpResponse(status, headers or {}, body)


def error(status, message="", reason="", **headers):
    return reply(
        status, {"error": {"status": status, "message": message, "reason": reason}}, headers
    )


class Api:
    """L'API de Spotify en miniature : des réponses par (méthode, chemin) ; la dernière se répète."""

    def __init__(self):
        self.routes: dict[tuple[str, str], list] = {}
        self.calls: list[HttpRequest] = []

    def on(self, method, path, *answers):
        self.routes[(method, path)] = list(answers)

    def __call__(self, request, timeout):
        self.calls.append(request)
        parts = urllib.parse.urlsplit(request.url)
        key = (
            ("POST", "token")
            if parts.netloc == "accounts.spotify.com"
            else (request.method, parts.path.removeprefix("/v1"))
        )
        queue = self.routes.get(key)
        if not queue:
            return error(404, f"route inconnue {key}")
        item = queue.pop(0) if len(queue) > 1 else queue[0]
        if isinstance(item, Exception):
            raise item
        return item

    def hits(self, method, path):
        wanted = "/api/token" if path == "token" else path
        return [
            c
            for c in self.calls
            if (c.method, urllib.parse.urlsplit(c.url).path.removeprefix("/v1")) == (method, wanted)
        ]

    def query(self, call):
        return dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(call.url).query))


class Clock:
    def __init__(self, now=1000.0):
        self.now = now

    def __call__(self):
        return self.now

    async def sleep(self, seconds):
        self.now += seconds


class FakeMusic:
    def __init__(self, clock, *, running=True, appears_after=None):
        self.clock = clock
        self.running = running
        self.appears_at = None if appears_after is None else clock() + appears_after
        self.opened: list[tuple[str | None, str]] = []

    def has_player(self, base):
        assert base == "spotify"
        return self.running or (self.appears_at is not None and self.clock() >= self.appears_at)

    async def open_uri(self, player, uri):
        self.opened.append((player, uri))


class FakeApps:
    def __init__(self, apps=(SNAP,), launch_error=None):
        self.apps = list(apps)
        self.launched: list[str] = []
        self.launch_error = launch_error

    async def refresh(self):
        return False

    async def launch(self, app_id):
        self.launched.append(app_id)
        if self.launch_error:
            raise self.launch_error
        return {"launched": app_id}


def make_creds(**more):
    values = {
        "client_id": CLIENT_ID,
        "refresh_token": "renouvellement",
        "access_token": "acces",
        "expires_at": 5000.0,
        "user": "Marie",
    }
    values.update(more)
    return Credentials(**values)


class Rig:
    """Un service câblé sur de fausses dépendances, pour que les tests disent seulement ce qui compte."""

    def __init__(
        self, tmp_path, *, creds=True, running=True, appears_after=None, apps=(SNAP,), **more
    ):
        self.api = Api()
        self.clock = Clock()
        self.music = FakeMusic(self.clock, running=running, appears_after=appears_after)
        self.apps = FakeApps(apps, more.pop("launch_error", None))
        self.store = TokenStore(tmp_path / "spotify.toml")
        if creds is True:
            self.store.save(make_creds())
        elif creds:
            self.store.save(creds)
        self.fetched: list[str] = []
        self.prepared: list[int] = []
        self.service = SpotifyService(
            music=self.music,
            apps=self.apps,
            store=self.store,
            transport=self.api,
            clock=self.clock,
            sleep=self.clock.sleep,
            hostname=lambda: more.pop("hostname", "mon-pc"),
            fetch=self.fetch,
            prepare=self.prepare,
        )

    def fetch(self, url):
        self.fetched.append(url)
        return b"octets"

    def prepare(self, data, size):
        assert data == b"octets"
        self.prepared.append(size)
        return "image/jpeg", b"jpeg" + str(size).encode()


def album_json(ident=ID_A, name="Néons"):
    return {
        "id": ident,
        "name": name,
        "artists": [{"name": "Les Fantômes"}],
        "images": [{"url": "https://i.scdn.co/image/cover", "width": 640}],
        "tracks": {"items": [{"id": ID_B, "name": "Nuit blanche", "duration_ms": 1000}]},
    }


# --- fonctions pures ------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("failure", "code", "words"),
    [
        (ApiError(0), "unavailable", "injoignable"),
        (ApiError(401), "not_connected", "a expiré"),
        (ApiError(403, "PREMIUM_REQUIRED"), "premium", "Premium"),
        (ApiError(403, "", "Active premium subscription required"), "premium", "Premium"),
        (
            ApiError(403, "", "User not registered in the Developer Dashboard"),
            "forbidden",
            "User Management",
        ),
        (ApiError(403, "", "Forbidden"), "forbidden", "février 2026"),
        (ApiError(404), "not_found", "ne trouve pas"),
        (ApiError(429, retry_after=12), "busy", "12 s"),
        (ApiError(429), "busy", "5 s"),
        (ApiError(502), "unavailable", "problème"),
        (ApiError(400, "", "Bad request"), "failed", "400 : Bad request"),
    ],
)
def test_api_errors_become_sentences_that_say_what_to_do(failure, code, words):
    action_error = describe(failure)
    assert action_error.code == code
    assert words in action_error.message


def test_the_pc_is_recognised_among_the_spotify_connect_devices():
    phone = {"id": "tel", "name": "iPhone", "type": "Smartphone", "is_active": True}
    pc = {"id": "pc", "name": "Mon-PC", "type": "Computer", "is_active": False}
    web = {"id": "web", "name": "Web Player (Chrome)", "type": "Computer", "is_active": True}
    locked = {"id": "lock", "name": "mon-pc", "type": "Computer", "is_restricted": True}
    assert choose_device([phone, web, pc, locked], "mon-pc")["id"] == "pc"  # même nom, sans casse
    assert choose_device([phone, pc, web, locked], "autre")["id"] == "web"  # un ordinateur actif
    assert choose_device([phone, pc], "autre")["id"] == "pc"  # un ordinateur
    assert choose_device([phone], "autre")["id"] == "tel"  # un appareil actif
    assert choose_device([{"id": "a", "name": "Enceinte", "type": "Speaker"}], "x")["id"] == "a"
    assert choose_device([locked], "mon-pc") is None  # un appareil restreint n'obéit pas
    for nothing in (None, [], {}, "x", [None, {"name": "sans id"}, {"id": 3}]):
        assert choose_device(nothing, "mon-pc") is None


def test_what_to_play_follows_what_was_chosen():
    assert play_body(ALBUM, None) == {"context_uri": ALBUM}
    assert play_body(f"spotify:playlist:{ID_C}", None) == {
        "context_uri": f"spotify:playlist:{ID_C}"
    }
    assert play_body(f"spotify:artist:{ID_C}", None) == {"context_uri": f"spotify:artist:{ID_C}"}
    assert play_body(TRACK, ALBUM) == {"context_uri": ALBUM, "offset": {"uri": TRACK}}
    assert play_body(TRACK, f"spotify:artist:{ID_C}") == {
        "uris": [TRACK]
    }  # pas d'offset dans un artiste
    assert play_body(TRACK, None) == {"uris": [TRACK]}
    assert play_body(TRACK, "n'importe quoi") == {"uris": [TRACK]}
    assert play_body(TRACK, f"spotify:track:{ID_C}") == {
        "uris": [TRACK]
    }  # un morceau n'est pas un contexte
    assert play_body(f"spotify:episode:{ID_C}", None) == {"uris": [f"spotify:episode:{ID_C}"]}


def test_the_spotify_menu_entry_is_found_for_a_package_a_snap_or_a_flatpak():
    flatpak = DesktopApp("com.spotify.Client.desktop", Path("/x"), "Spotify", None, None, False)
    other = DesktopApp("vlc.desktop", Path("/y"), "VLC", None, None, False)
    renamed = DesktopApp("musique.desktop", Path("/z"), "SPOTIFY", None, None, False)
    assert find_app([other, SNAP]) is SNAP
    assert find_app([other, flatpak]) is flatpak
    assert find_app([SNAP, DEB]) is DEB  # le paquet .deb l'emporte
    assert find_app([other, renamed]) is renamed  # sinon par le nom
    assert find_app([other]) is None
    assert find_app([]) is None


# --- l'état ---------------------------------------------------------------------------------------------


async def test_status_without_an_account_says_how_to_connect(tmp_path):
    rig = Rig(tmp_path, creds=False)
    status = await rig.service.status()
    assert status["configured"] is False
    assert status["connected"] is False
    assert status["installed"] is True
    assert status["running"] is True
    assert status["message"] == MSG_LOGIN
    assert rig.api.calls == []


async def test_status_of_a_connected_account_is_cached_for_two_minutes(tmp_path):
    rig = Rig(tmp_path)
    rig.api.on("GET", "/me", reply(payload={"display_name": "Marie", "product": "premium"}))
    status = await rig.service.status()
    assert (status["connected"], status["user"], status["premium"]) == (True, "Marie", True)
    await rig.service.status()
    assert len(rig.api.hits("GET", "/me")) == 1
    rig.clock.now += 121
    await rig.service.status()
    assert len(rig.api.hits("GET", "/me")) == 2
    rig.api.on("GET", "/me", reply(payload={"id": "marie42", "product": "free"}))
    rig.clock.now += 121
    status = await rig.service.status()
    assert (status["user"], status["premium"]) == ("marie42", False)


async def test_status_reports_an_application_that_is_missing_or_closed(tmp_path):
    rig = Rig(tmp_path, creds=False, running=False, apps=())
    status = await rig.service.status()
    assert (status["installed"], status["running"]) == (False, False)


async def test_status_explains_why_the_account_is_refused(tmp_path):
    rig = Rig(tmp_path)
    rig.api.on("GET", "/me", error(403, "User not registered in the Developer Dashboard"))
    status = await rig.service.status()
    assert status["configured"] is True
    assert status["connected"] is False
    assert "User Management" in status["message"]


async def test_a_token_file_open_to_others_is_reported_instead_of_used(tmp_path):
    rig = Rig(tmp_path)
    rig.store.path.chmod(0o644)
    status = await rig.service.status()
    assert status["configured"] is False
    assert "chmod 600" in status["message"]
    assert rig.api.calls == []
    with pytest.raises(ActionError) as info:
        await rig.service.search("mizu")
    assert info.value.code == "not_connected"


async def test_a_new_login_is_picked_up_without_restarting_the_agent(tmp_path):
    rig = Rig(tmp_path)
    rig.api.on("GET", "/me", reply(payload={"display_name": "Marie"}))
    await rig.service.status()
    assert rig.api.hits("GET", "/me")[0].headers["Authorization"] == "Bearer acces"
    rig.store.save(make_creds(access_token="autre-jeton", user="Paul"))
    stamp = rig.store.path.stat().st_mtime_ns + 10**9
    os.utime(rig.store.path, ns=(stamp, stamp))
    await rig.service.status()
    assert rig.api.hits("GET", "/me")[-1].headers["Authorization"] == "Bearer autre-jeton"
    rig.store.delete()
    assert (await rig.service.status())["configured"] is False  # déconnecté : plus de jeton


# --- jeton : renouvellement ----------------------------------------------------------------------------


async def test_an_expired_token_is_renewed_before_the_call_and_saved(tmp_path):
    rig = Rig(tmp_path, creds=make_creds(expires_at=1030.0))  # moins d'une minute : à renouveler
    rig.api.on("POST", "token", reply(payload={"access_token": "neuf", "expires_in": 3600}))
    rig.api.on("GET", "/search", reply(payload={}))
    await rig.service.search("mizu")
    assert rig.api.calls[0].url.endswith("/api/token")
    assert rig.api.hits("GET", "/search")[0].headers["Authorization"] == "Bearer neuf"
    saved = rig.store.load()
    assert saved is not None
    assert (saved.access_token, saved.expires_at) == ("neuf", 4600.0)
    assert oct(rig.store.path.stat().st_mode & 0o777) == "0o600"


async def test_a_token_refused_by_the_api_is_renewed_once_and_the_call_retried(tmp_path):
    rig = Rig(tmp_path)
    rig.api.on("GET", "/search", error(401, "The access token expired"), reply(payload={}))
    rig.api.on("POST", "token", reply(payload={"access_token": "neuf"}))
    await rig.service.search("mizu")
    searches = rig.api.hits("GET", "/search")
    assert [c.headers["Authorization"] for c in searches] == ["Bearer acces", "Bearer neuf"]
    assert len(rig.api.hits("POST", "token")) == 1


async def test_a_token_that_stays_refused_asks_for_a_new_login(tmp_path):
    rig = Rig(tmp_path)
    rig.api.on("GET", "/search", error(401))
    rig.api.on("POST", "token", reply(payload={"access_token": "neuf"}))
    with pytest.raises(ActionError) as info:
        await rig.service.search("mizu")
    assert info.value.code == "not_connected"
    assert "mydock-agent spotify login" in info.value.message
    assert len(rig.api.hits("GET", "/search")) == 2  # une seule nouvelle tentative


async def test_a_revoked_refresh_token_asks_for_a_new_login(tmp_path):
    rig = Rig(tmp_path, creds=make_creds(expires_at=0.0))
    rig.api.on("POST", "token", reply(400, {"error": "invalid_grant"}))
    with pytest.raises(ActionError) as info:
        await rig.service.search("mizu")
    assert info.value.code == "not_connected"
    assert "révoqué" in info.value.message
    assert rig.api.hits("GET", "/search") == []


# --- erreurs du réseau et limites --------------------------------------------------------------------------


async def test_a_dead_network_is_reported_as_unavailable(tmp_path):
    rig = Rig(tmp_path)
    rig.api.on("GET", "/search", HttpError("URLError"))
    with pytest.raises(ActionError) as info:
        await rig.service.search("mizu")
    assert info.value.code == "unavailable"


async def test_a_rate_limit_pauses_every_call_until_it_is_over(tmp_path):
    rig = Rig(tmp_path)
    rig.api.on("GET", "/search", error(429, **{"retry-after": "7"}), reply(payload={}))
    with pytest.raises(ActionError) as info:
        await rig.service.search("mizu")
    assert (info.value.code, "7 s" in info.value.message) == ("busy", True)
    before = len(rig.api.calls)
    with pytest.raises(ActionError) as again:
        await rig.service.search("autre chose")
    assert again.value.code == "busy"
    assert len(rig.api.calls) == before  # Spotify n'est pas harcelé pendant la pause
    rig.clock.now += 8
    await rig.service.search("autre chose")
    assert len(rig.api.calls) == before + 1


async def test_a_server_error_with_a_garbled_body_is_still_understood(tmp_path):
    rig = Rig(tmp_path)
    rig.api.on("GET", "/search", HttpResponse(502, {}, b"<html>bad gateway</html>"))
    with pytest.raises(ActionError) as info:
        await rig.service.search("mizu")
    assert info.value.code == "unavailable"


async def test_oauth_style_errors_are_read_too(tmp_path):
    rig = Rig(tmp_path)
    rig.api.on(
        "GET", "/search", reply(400, {"error": "invalid_request", "error_description": "Bad stuff"})
    )
    with pytest.raises(ActionError) as info:
        await rig.service.search("mizu")
    assert "Bad stuff" in info.value.message


# --- chercher ------------------------------------------------------------------------------------------------


async def test_a_search_asks_for_the_four_kinds_and_normalises_the_answer(tmp_path):
    rig = Rig(tmp_path)
    rig.api.on(
        "GET",
        "/search",
        reply(
            payload={
                "tracks": {"items": [{"id": ID_B, "name": "Nuit blanche", "album": album_json()}]},
                "albums": {"items": [album_json()]},
            }
        ),
    )
    result = await rig.service.search("les fantômes")
    call = rig.api.hits("GET", "/search")[0]
    assert rig.api.query(call) == {
        "q": "les fantômes",
        "type": "track,album,artist,playlist",
        "limit": "10",
    }
    assert call.headers["Authorization"] == "Bearer acces"
    assert result["query"] == "les fantômes"
    assert [t["name"] for t in result["tracks"]] == ["Nuit blanche"]
    assert [a["name"] for a in result["albums"]] == ["Néons"]
    assert result["artists"] == []
    assert result["playlists"] == []
    assert result["tracks"][0]["art"] is not None
    json.dumps(result)  # tout est sérialisable pour l'écran


async def test_the_same_search_is_served_from_memory_for_a_minute(tmp_path):
    rig = Rig(tmp_path)
    rig.api.on("GET", "/search", reply(payload={}))
    first = await rig.service.search("Mizu")
    assert await rig.service.search("mizu") is first  # sans tenir compte de la casse
    assert len(rig.api.hits("GET", "/search")) == 1
    rig.clock.now += 61
    await rig.service.search("mizu")
    assert len(rig.api.hits("GET", "/search")) == 2


async def test_only_the_latest_searches_are_remembered(tmp_path):
    rig = Rig(tmp_path)
    rig.api.on("GET", "/search", reply(payload={}))
    for n in range(svc.SEARCHES_KEPT + 3):
        await rig.service.search(f"recherche {n}")
    assert len(rig.service._searches) == svc.SEARCHES_KEPT


# --- parcourir -----------------------------------------------------------------------------------------------


async def test_an_album_page(tmp_path):
    rig = Rig(tmp_path)
    rig.api.on("GET", f"/albums/{ID_A}", reply(payload=album_json()))
    view = await rig.service.view("album", ID_A)
    assert view["item"]["name"] == "Néons"
    assert view["tracks"][0]["album"] == "Néons"


async def test_a_playlist_page_reads_the_entries_under_either_name(tmp_path):
    rig = Rig(tmp_path)
    meta = {
        "id": ID_C,
        "name": "Soirée",
        "owner": {"display_name": "Spotify"},
        "tracks": {"total": 1},
    }
    rig.api.on("GET", f"/playlists/{ID_C}", reply(payload=meta))
    rig.api.on("GET", f"/playlists/{ID_C}/tracks", error(404))
    rig.api.on(
        "GET",
        f"/playlists/{ID_C}/items",
        reply(payload={"items": [{"item": {"id": ID_B, "name": "Nuit blanche"}}]}),
    )
    view = await rig.service.view("playlist", ID_C)
    assert [t["name"] for t in view["tracks"]] == ["Nuit blanche"]
    assert "fields" in rig.api.query(rig.api.hits("GET", f"/playlists/{ID_C}")[0])
    assert len(rig.api.hits("GET", f"/playlists/{ID_C}/tracks")) == 1  # d'abord l'ancien nom


async def test_a_playlist_that_cannot_be_read_says_why(tmp_path):
    rig = Rig(tmp_path)
    rig.api.on("GET", f"/playlists/{ID_C}", reply(payload={"id": ID_C, "name": "Privée"}))
    rig.api.on("GET", f"/playlists/{ID_C}/tracks", error(403, "Forbidden"))
    with pytest.raises(ActionError) as info:
        await rig.service.view("playlist", ID_C)
    assert info.value.code == "forbidden"


async def test_an_artist_page_has_albums_and_top_tracks(tmp_path):
    rig = Rig(tmp_path)
    rig.api.on("GET", f"/artists/{ID_C}", reply(payload={"id": ID_C, "name": "Mizu"}))
    rig.api.on("GET", f"/artists/{ID_C}/albums", reply(payload={"items": [album_json()]}))
    rig.api.on(
        "GET",
        f"/artists/{ID_C}/top-tracks",
        reply(payload={"tracks": [{"id": ID_B, "name": "Sous la pluie de Kyoto"}]}),
    )
    view = await rig.service.view("artist", ID_C)
    assert view["item"]["name"] == "Mizu"
    assert [a["name"] for a in view["albums"]] == ["Néons"]
    assert [t["name"] for t in view["tracks"]] == ["Sous la pluie de Kyoto"]
    albums = rig.api.query(rig.api.hits("GET", f"/artists/{ID_C}/albums")[0])
    assert albums["include_groups"] == "album,single"


async def test_an_artist_without_top_tracks_falls_back_to_a_search(tmp_path):
    """Certaines applications n'ont plus l'endpoint « top-tracks » : la page se remplit autrement."""
    rig = Rig(tmp_path)
    rig.api.on("GET", f"/artists/{ID_C}", reply(payload={"id": ID_C, "name": "Mizu"}))
    rig.api.on("GET", f"/artists/{ID_C}/albums", error(403, "Forbidden"))
    rig.api.on("GET", f"/artists/{ID_C}/top-tracks", error(403, "Forbidden"))
    rig.api.on(
        "GET", "/search", reply(payload={"tracks": {"items": [{"id": ID_B, "name": "Printemps"}]}})
    )
    view = await rig.service.view("artist", ID_C)
    assert view["albums"] == []  # une rubrique qui échoue n'efface pas la page
    assert [t["name"] for t in view["tracks"]] == ["Printemps"]
    assert rig.api.query(rig.api.hits("GET", "/search")[0])["q"] == 'artist:"Mizu"'
    assert rig.api.query(rig.api.hits("GET", "/search")[0])["type"] == "track"


async def test_an_unknown_item_is_not_found(tmp_path):
    rig = Rig(tmp_path)
    rig.api.on("GET", f"/albums/{ID_A}", reply(payload={}))
    with pytest.raises(ActionError) as info:
        await rig.service.view("album", ID_A)
    assert info.value.code == "not_found"
    rig.api.on("GET", f"/albums/{ID_B}", error(404))
    with pytest.raises(ActionError) as missing:
        await rig.service.view("album", ID_B)
    assert missing.value.code == "not_found"


@pytest.mark.parametrize(
    ("section", "path", "extra"),
    [
        ("playlists", "/me/playlists", {}),
        ("liked", "/me/tracks", {}),
        ("albums", "/me/albums", {}),
        ("artists", "/me/following", {"type": "artist"}),
        ("recent", "/me/player/recently-played", {}),
    ],
)
async def test_each_section_of_the_library_has_its_own_endpoint(tmp_path, section, path, extra):
    rig = Rig(tmp_path)
    rig.api.on("GET", path, reply(payload={}))
    result = await rig.service.library(section)
    assert (result["section"], result["items"]) == (section, [])
    assert rig.api.query(rig.api.hits("GET", path)[0]) == {"limit": "50", **extra}


# --- pochettes -----------------------------------------------------------------------------------------------


async def test_a_cover_is_downloaded_once_resized_and_kept(tmp_path):
    rig = Rig(tmp_path)
    rig.api.on("GET", f"/albums/{ID_A}", reply(payload=album_json()))
    art = (await rig.service.view("album", ID_A))["item"]["art"]
    first = await rig.service.image(art)
    assert first["mime"] == "image/jpeg"
    assert first["data"]  # base64
    assert await rig.service.image(art) is first
    assert rig.fetched == ["https://i.scdn.co/image/cover"]
    large = await rig.service.image(art, large=True)
    assert large is not first
    assert rig.prepared == [svc.THUMB_SIZE, svc.LARGE_SIZE]


async def test_an_address_the_screen_never_saw_cannot_be_fetched(tmp_path):
    rig = Rig(tmp_path)
    with pytest.raises(ActionError) as info:
        await rig.service.image("0123456789abcdef")
    assert info.value.code == "unknown_art"
    assert rig.fetched == []


async def test_a_cover_that_fails_to_load_is_reported(tmp_path):
    rig = Rig(tmp_path)
    rig.api.on("GET", f"/albums/{ID_A}", reply(payload=album_json()))
    art = (await rig.service.view("album", ID_A))["item"]["art"]

    def refuse(_url):
        raise ArtError("Pochette refusée")

    rig.service._fetch = refuse
    with pytest.raises(ActionError) as info:
        await rig.service.image(art)
    assert (info.value.code, info.value.message) == ("art_failed", "Pochette refusée")


async def test_only_the_latest_covers_are_kept_in_memory(tmp_path):
    rig = Rig(tmp_path)
    rig.api.on("GET", f"/albums/{ID_A}", reply(payload=album_json()))
    art = (await rig.service.view("album", ID_A))["item"]["art"]
    for _ in range(3):
        await rig.service.image(art)
    for n in range(svc.IMAGES_KEPT + 5):
        rig.service._images[(f"{n:016x}", False)] = {"mime": "x", "data": "y"}
        rig.service._images.move_to_end((f"{n:016x}", False))
    await rig.service.image(art, large=True)
    assert len(rig.service._images) <= svc.IMAGES_KEPT


# --- lancer l'application -------------------------------------------------------------------------------------


async def test_launching_does_nothing_when_spotify_already_runs(tmp_path):
    rig = Rig(tmp_path)
    assert await rig.service.launch() == {"running": True, "started": False}
    assert rig.apps.launched == []


async def test_launching_starts_the_snap_and_waits_for_it_to_show_up(tmp_path):
    rig = Rig(tmp_path, running=False, appears_after=3.0)
    assert await rig.service.launch() == {"running": True, "started": True}
    assert rig.apps.launched == ["spotify_spotify.desktop"]
    assert 3.0 <= rig.clock.now - 1000.0 < 4.5


async def test_launching_gives_up_politely_when_spotify_never_shows_up(tmp_path):
    rig = Rig(tmp_path, running=False)
    with pytest.raises(ActionError) as info:
        await rig.service.launch()
    assert info.value.code == "launch_timeout"
    assert rig.clock.now - 1000.0 >= svc.LAUNCH_WAIT


async def test_launching_without_the_application_installed_says_so(tmp_path):
    rig = Rig(tmp_path, running=False, apps=())
    with pytest.raises(ActionError) as info:
        await rig.service.launch()
    assert info.value.code == "not_installed"
    assert "Snap" in info.value.message


async def test_a_launch_failure_keeps_the_reason_given_by_the_launcher(tmp_path):
    failure = ActionError("launch_failed", "Le lancement de « Spotify » a échoué (code 1).")
    rig = Rig(tmp_path, running=False, launch_error=failure)
    with pytest.raises(ActionError) as info:
        await rig.service.launch()
    assert info.value.code == "launch_failed"
    assert "code 1" in info.value.message


# --- jouer ---------------------------------------------------------------------------------------------------


def devices(rig, *found):
    rig.api.on("GET", "/me/player/devices", reply(payload={"devices": list(found)}))


PC = {"id": "pc-id", "name": "mon-pc", "type": "Computer", "is_active": False}


async def test_a_track_is_played_in_its_album_on_this_pc_through_the_api(tmp_path):
    rig = Rig(tmp_path)
    devices(rig, {"id": "tel", "name": "iPhone", "type": "Smartphone", "is_active": True}, PC)
    rig.api.on("PUT", "/me/player/play", reply(204))
    result = await rig.service.play(TRACK, ALBUM)
    assert result == {"via": "api", "started": False}
    call = rig.api.hits("PUT", "/me/player/play")[0]
    assert rig.api.query(call) == {"device_id": "pc-id"}
    assert json.loads(call.body) == {"context_uri": ALBUM, "offset": {"uri": TRACK}}
    assert call.headers["Content-Type"] == "application/json"
    assert rig.music.opened == []


async def test_spotify_is_started_first_when_it_is_closed(tmp_path):
    rig = Rig(tmp_path, running=False, appears_after=2.0)
    devices(rig, PC)
    rig.api.on("PUT", "/me/player/play", reply(204))
    assert await rig.service.play(ALBUM) == {"via": "api", "started": True}
    assert rig.apps.launched == ["spotify_spotify.desktop"]
    assert json.loads(rig.api.hits("PUT", "/me/player/play")[0].body) == {"context_uri": ALBUM}


async def test_the_pc_appears_among_the_devices_a_few_seconds_after_the_launch(tmp_path):
    rig = Rig(tmp_path)
    rig.api.on(
        "GET",
        "/me/player/devices",
        reply(payload={"devices": []}),
        reply(payload={"devices": [PC]}),
    )
    rig.api.on("PUT", "/me/player/play", reply(204))
    assert (await rig.service.play(ALBUM))["via"] == "api"
    assert len(rig.api.hits("GET", "/me/player/devices")) == 2


async def test_without_a_device_the_player_of_the_pc_opens_it_itself(tmp_path):
    rig = Rig(tmp_path)
    devices(rig)
    result = await rig.service.play(TRACK, ALBUM)
    assert result == {"via": "player", "started": False}
    assert rig.music.opened == [("spotify", ALBUM)]  # l'album entier, au premier morceau
    assert rig.clock.now - 1000.0 >= svc.DEVICE_WAIT


async def test_a_free_account_still_plays_through_the_player(tmp_path):
    rig = Rig(tmp_path)
    devices(rig, PC)
    rig.api.on(
        "PUT",
        "/me/player/play",
        error(403, "Player command failed: Premium required", "PREMIUM_REQUIRED"),
    )
    assert await rig.service.play(TRACK) == {"via": "player", "started": False}
    assert rig.music.opened == [("spotify", TRACK)]


async def test_without_an_account_nothing_goes_through_the_network(tmp_path):
    rig = Rig(tmp_path, creds=False)
    assert (await rig.service.play(ALBUM))["via"] == "player"
    assert rig.api.calls == []
    assert rig.music.opened == [("spotify", ALBUM)]


async def test_a_revoked_login_falls_back_to_the_player_too(tmp_path):
    rig = Rig(tmp_path, creds=make_creds(expires_at=0.0))
    rig.api.on("POST", "token", reply(400, {"error": "invalid_grant"}))
    assert (await rig.service.play(ALBUM))["via"] == "player"


async def test_a_rate_limit_is_reported_instead_of_hiding_behind_the_player(tmp_path):
    rig = Rig(tmp_path)
    rig.api.on("GET", "/me/player/devices", error(429, **{"retry-after": "3"}))
    with pytest.raises(ActionError) as info:
        await rig.service.play(ALBUM)
    assert info.value.code == "busy"
    assert rig.music.opened == []


# --- actions : la liste blanche et ses paramètres ----------------------------------------------------------


class Backend:
    def __init__(self):
        self.calls: list[tuple] = []

    def __getattr__(self, name):
        async def call(*args, **kwargs):
            self.calls.append((name, args, kwargs))
            return {"ok": name}

        return call


async def invoke(registry, name, params=None):
    return await registry.invoke(
        Act("a1", name, params or {}, None), ActionContext(1, "192.168.1.60")
    )


def actions():
    backend = Backend()
    registry = ActionRegistry()
    register_spotify_actions(registry, backend)
    return backend, registry


def test_the_spotify_actions_are_exactly_these_and_none_is_sensitive():
    _, registry = actions()
    assert registry.names == [
        "spotify.image",
        "spotify.launch",
        "spotify.library",
        "spotify.play",
        "spotify.search",
        "spotify.status",
        "spotify.view",
    ]
    assert not any(registry.is_sensitive(n) for n in registry.names)


async def test_valid_requests_reach_the_service(tmp_path):
    backend, registry = actions()
    assert (await invoke(registry, "spotify.search", {"query": "  les   fantômes \n"}))["ok"]
    assert (await invoke(registry, "spotify.view", {"kind": "album", "id": ID_A}))["ok"]
    assert (await invoke(registry, "spotify.library", {"section": "liked"}))["ok"]
    assert (await invoke(registry, "spotify.image", {"art": "0123456789abcdef", "large": True}))[
        "ok"
    ]
    assert (await invoke(registry, "spotify.play", {"uri": TRACK, "context": ALBUM}))["ok"]
    assert (await invoke(registry, "spotify.play", {"uri": ALBUM}))["ok"]
    assert (await invoke(registry, "spotify.status"))["ok"]
    assert (await invoke(registry, "spotify.launch"))["ok"]
    assert [c[0] for c in backend.calls] == [
        "search", "view", "library", "image", "play", "play", "status", "launch",
    ]  # fmt: skip
    assert backend.calls[0][1] == ("les fantômes",)  # espaces et saut de ligne ramenés à un espace
    assert backend.calls[3] == ("image", ("0123456789abcdef",), {"large": True})
    assert backend.calls[4][1] == (TRACK, ALBUM)
    assert backend.calls[5][1] == (ALBUM, None)


@pytest.mark.parametrize(
    ("name", "params"),
    [
        ("spotify.search", {}),
        ("spotify.search", {"query": ""}),
        ("spotify.search", {"query": "   "}),
        ("spotify.search", {"query": "x" * 121}),
        ("spotify.search", {"query": "a\x00b"}),
        ("spotify.search", {"query": "a", "limit": 500}),
        ("spotify.search", {"query": 12}),
        ("spotify.view", {"kind": "track", "id": ID_A}),
        ("spotify.view", {"kind": "album", "id": "court"}),
        ("spotify.view", {"kind": "album", "id": ID_A + "x"}),
        ("spotify.view", {"kind": "album", "id": "../../etc/passwd/xxxx"}),
        ("spotify.view", {"kind": "album"}),
        ("spotify.library", {"section": "secrets"}),
        ("spotify.library", {}),
        ("spotify.image", {"art": "https://i.scdn.co/image/abc"}),
        ("spotify.image", {"art": "0123456789ABCDEF"}),
        ("spotify.image", {"art": "0123456789abcdef", "large": "yes"}),
        ("spotify.play", {"uri": f"https://open.spotify.com/track/{ID_B}"}),
        ("spotify.play", {"uri": f"spotify:user:{ID_B}"}),
        ("spotify.play", {"uri": f"spotify:local:{ID_B}"}),
        ("spotify.play", {"uri": TRACK + "\nspotify:track:" + ID_A}),
        ("spotify.play", {"uri": TRACK, "context": f"spotify:track:{ID_A}"}),
        ("spotify.play", {"uri": TRACK, "context": f"spotify:artist:{ID_A}"}),
        ("spotify.play", {"uri": TRACK, "context": "https://evil.example"}),
        ("spotify.play", {"uri": TRACK, "device_id": "x"}),
        ("spotify.status", {"x": 1}),
        ("spotify.launch", {"command": "rm -rf /"}),
    ],
)
async def test_bad_parameters_never_reach_the_service(name, params):
    backend, registry = actions()
    answer = await invoke(registry, name, params)
    assert answer["ok"] is False
    assert answer["e"]["code"] == "bad_params"
    assert backend.calls == []
