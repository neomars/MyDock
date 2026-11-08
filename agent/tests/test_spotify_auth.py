"""Connexion à Spotify : PKCE, fichier du jeton, échanges avec accounts.spotify.com, commande « spotify login »."""

import json
import socket
import stat
import threading
import urllib.error
import urllib.parse
import urllib.request

import pytest

from mydock_agent.spotify import auth, http
from mydock_agent.spotify.auth import (
    SCOPES,
    AuthError,
    Credentials,
    TokenStore,
    authorize_url,
    challenge_of,
    exchange_code,
    new_verifier,
    refresh_token,
    run_login,
    whoami,
)
from mydock_agent.spotify.http import HttpError, HttpRequest, HttpResponse
from mydock_common.config import ConfigError

CLIENT_ID = "0123456789abcdef0123456789abcdef"


def reply(status=200, payload=None, headers=None, raw=None):
    body = raw if raw is not None else json.dumps(payload if payload is not None else {}).encode()
    return HttpResponse(status, headers or {}, body)


class FakeTransport:
    """Répond selon l'adresse demandée et garde la trace des requêtes."""

    def __init__(self, *replies):
        self.replies = list(replies)
        self.requests: list[HttpRequest] = []

    def __call__(self, request, timeout):
        self.requests.append(request)
        assert timeout > 0
        item = self.replies.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    def form(self, index=0):
        return dict(urllib.parse.parse_qsl(self.requests[index].body.decode()))


def free_port():
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


# --- PKCE (RFC 7636) ---------------------------------------------------------------------------------


def test_the_challenge_matches_the_example_of_rfc_7636():
    assert (
        challenge_of("dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk")
        == "E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM"
    )


def test_each_connection_gets_its_own_long_enough_verifier():
    seen = {new_verifier() for _ in range(20)}
    assert len(seen) == 20
    for verifier in seen:
        assert 43 <= len(verifier) <= 128
        assert all(c.isalnum() or c in "-._~" for c in verifier)
        assert "=" not in challenge_of(verifier)


def test_the_authorization_address_asks_for_pkce_and_no_secret():
    url = authorize_url(CLIENT_ID, 8888, "etat", "defi")
    parts = urllib.parse.urlsplit(url)
    query = dict(urllib.parse.parse_qsl(parts.query))
    assert (parts.scheme, parts.netloc, parts.path) == (
        "https",
        "accounts.spotify.com",
        "/authorize",
    )
    assert query["response_type"] == "code"
    assert query["client_id"] == CLIENT_ID
    assert query["code_challenge_method"] == "S256"
    assert query["code_challenge"] == "defi"
    assert query["state"] == "etat"
    assert query["redirect_uri"] == "http://127.0.0.1:8888/callback"
    assert "secret" not in url
    assert query["scope"].split() == list(SCOPES)


def test_the_requested_permissions_cannot_modify_the_account():
    """MyDock lit la bibliothèque et commande la lecture : jamais de quoi changer le profil ou lire l'e-mail."""
    assert "user-read-email" not in SCOPES
    assert "user-library-modify" not in SCOPES
    assert all(not s.startswith(("playlist-modify", "user-follow-modify")) for s in SCOPES)
    assert "user-modify-playback-state" in SCOPES
    assert "user-read-playback-state" in SCOPES
    assert "user-library-read" in SCOPES


# --- le fichier du jeton -----------------------------------------------------------------------------


def make_creds(**more):
    values = {
        "client_id": CLIENT_ID,
        "refresh_token": "renouvellement",
        "access_token": "acces",
        "expires_at": 5000.0,
        "scope": "user-read-private",
        "user": "Marie",
        "port": 8899,
    }
    values.update(more)
    return Credentials(**values)


def test_the_token_file_is_private_and_round_trips(tmp_path):
    store = TokenStore(tmp_path / "mydock" / "spotify.toml")
    assert store.load() is None  # jamais connecté
    store.save(make_creds())
    assert stat.S_IMODE(store.path.stat().st_mode) == 0o600
    assert store.load() == make_creds()
    text = store.path.read_text()
    assert text.startswith("# MyDock")
    assert "ne le publie jamais" in text


def test_a_token_file_open_to_others_is_refused(tmp_path):
    store = TokenStore(tmp_path / "spotify.toml")
    store.save(make_creds())
    store.path.chmod(0o644)
    with pytest.raises(ConfigError, match="chmod 600"):
        store.load()


@pytest.mark.parametrize(
    "content",
    [
        'client_id = "court"\nrefresh_token = "x"\n',
        f'client_id = "{CLIENT_ID}"\n',
        f'client_id = "{CLIENT_ID}"\nrefresh_token = ""\n',
        'client_id = 12\nrefresh_token = "x"\n',
        "ceci n'est pas du TOML = = =\n",
    ],
)
def test_an_incomplete_or_broken_token_file_asks_for_a_new_login(tmp_path, content):
    path = tmp_path / "spotify.toml"
    path.write_text(content)
    path.chmod(0o600)
    with pytest.raises(ConfigError):
        TokenStore(path).load()


def test_odd_optional_fields_fall_back_to_defaults(tmp_path):
    path = tmp_path / "spotify.toml"
    path.write_text(
        f'client_id = "{CLIENT_ID}"\nrefresh_token = "r"\nexpires_at = "bientôt"\nport = "x"\n'
    )
    path.chmod(0o600)
    creds = TokenStore(path).load()
    assert creds is not None
    assert (creds.expires_at, creds.port, creds.access_token) == (0.0, auth.DEFAULT_PORT, "")


def test_logging_out_deletes_the_file_once(tmp_path):
    store = TokenStore(tmp_path / "spotify.toml")
    store.save(make_creds())
    assert store.delete() is True
    assert store.delete() is False
    assert store.load() is None


def test_the_default_location_follows_the_xdg_config_home(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    assert auth.token_path() == tmp_path / "mydock" / "spotify.toml"
    monkeypatch.delenv("XDG_CONFIG_HOME")
    assert auth.token_path().parts[-3:] == (".config", "mydock", "spotify.toml")


def test_a_token_is_renewed_a_little_before_it_expires():
    creds = make_creds(expires_at=1000.0)
    assert not creds.expired(900.0)
    assert creds.expired(941.0)  # moins d'une minute avant l'échéance
    assert creds.expired(1000.0)
    assert make_creds(access_token="").expired(0.0)


# --- échanges avec accounts.spotify.com --------------------------------------------------------------


def test_the_code_is_exchanged_with_the_verifier_and_without_any_secret():
    transport = FakeTransport(
        reply(
            payload={
                "access_token": "A",
                "refresh_token": "R",
                "expires_in": 3600,
                "scope": "user-read-private",
            }
        )
    )
    creds = exchange_code(transport, CLIENT_ID, "le-code", 8888, "le-verifier", now=1000.0)
    assert (creds.access_token, creds.refresh_token, creds.expires_at) == ("A", "R", 4600.0)
    assert (creds.client_id, creds.port, creds.scope) == (CLIENT_ID, 8888, "user-read-private")
    request = transport.requests[0]
    assert (request.method, request.url) == ("POST", auth.TOKEN_URL)
    assert request.headers["Content-Type"] == "application/x-www-form-urlencoded"
    assert transport.form() == {
        "grant_type": "authorization_code",
        "code": "le-code",
        "redirect_uri": "http://127.0.0.1:8888/callback",
        "client_id": CLIENT_ID,
        "code_verifier": "le-verifier",
    }
    assert "Authorization" not in request.headers  # pas de secret de l'application


def test_refreshing_updates_the_token_in_place_and_keeps_the_old_refresh_token_if_none_comes():
    creds = make_creds(access_token="vieux", expires_at=1.0)
    transport = FakeTransport(reply(payload={"access_token": "neuf", "expires_in": 1800}))
    refresh_token(transport, creds, now=100.0)
    assert (creds.access_token, creds.expires_at, creds.refresh_token) == (
        "neuf",
        1900.0,
        "renouvellement",
    )
    assert transport.form() == {
        "grant_type": "refresh_token",
        "refresh_token": "renouvellement",
        "client_id": CLIENT_ID,
    }


def test_a_new_refresh_token_replaces_the_old_one():
    creds = make_creds()
    refresh_token(
        FakeTransport(reply(payload={"access_token": "n", "refresh_token": "R2"})), creds, 0.0
    )
    assert creds.refresh_token == "R2"
    assert creds.expires_at == 3600.0  # durée par défaut quand l'API n'en donne pas


@pytest.mark.parametrize(
    ("answer", "expected"),
    [
        (reply(400, {"error": "invalid_grant"}), "relance « mydock-agent spotify login »"),
        (reply(400, {"error": "invalid_client"}), "ne connaît pas ce Client ID"),
        (reply(503, raw=b"<html>oups</html>"), "code 503"),
        (reply(400, {"error": "autre", "error_description": "Détail"}), "Détail"),
        (reply(200, payload=[1, 2]), "code 200"),
        (reply(200, payload={"scope": "x"}), "pas donné de jeton"),
        (HttpError("URLError"), "injoignable"),
    ],
)
def test_a_failed_exchange_says_what_to_do(answer, expected):
    with pytest.raises(AuthError, match=expected):
        refresh_token(FakeTransport(answer), make_creds(), 0.0)


def test_the_first_exchange_must_bring_a_refresh_token():
    transport = FakeTransport(reply(payload={"access_token": "A"}))
    with pytest.raises(AuthError, match="renouvellement"):
        exchange_code(transport, CLIENT_ID, "c", 8888, "v", 0.0)


def test_whoami_gives_the_name_of_the_account():
    transport = FakeTransport(reply(payload={"display_name": "Marie", "id": "marie42"}))
    assert whoami(transport, make_creds()) == "Marie"
    request = transport.requests[0]
    assert request.url == f"{auth.API_URL}/me"
    assert request.headers["Authorization"] == "Bearer acces"
    assert whoami(FakeTransport(reply(payload={"id": "marie42"})), make_creds()) == "marie42"
    assert whoami(FakeTransport(reply(raw=b"pas du json")), make_creds()) == ""
    assert whoami(FakeTransport(reply(payload=["x"])), make_creds()) == ""


def test_whoami_explains_the_2026_restrictions_on_a_403():
    with pytest.raises(AuthError, match="Premium depuis février 2026"):
        whoami(FakeTransport(reply(403, {"error": {"message": "x"}})), make_creds())
    with pytest.raises(AuthError, match="code 500"):
        whoami(FakeTransport(reply(500)), make_creds())
    with pytest.raises(AuthError, match="injoignable"):
        whoami(FakeTransport(HttpError("Timeout")), make_creds())


# --- la commande « spotify login » -------------------------------------------------------------------


def knock(url, query):
    """Fait ce que ferait le navigateur de l'utilisateur après la page de Spotify : appelle l'adresse locale."""
    try:
        with urllib.request.urlopen(url + query, timeout=5) as response:
            return response.status, response.read().decode()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode()


class Browser:
    """Remplace l'ouverture du navigateur : lit l'état dans l'adresse de Spotify et rappelle le serveur local."""

    def __init__(self, port, *, answers):
        self.port = port
        self.answers = answers  # liste de fonctions (state -> requête) jouées dans l'ordre
        self.results: list[tuple[int, str]] = []
        self.url = ""
        self.thread: threading.Thread | None = None

    def __call__(self, url):
        self.url = url
        state = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(url).query))["state"]

        def play():
            base = f"http://127.0.0.1:{self.port}"
            for answer in self.answers:
                self.results.append(knock(base, answer(state)))

        self.thread = threading.Thread(target=play)
        self.thread.start()

    def finish(self):
        assert self.thread is not None
        self.thread.join(10)


def run(tmp_path, transport, browser, port, *, client_id=CLIENT_ID, timeout=5.0, **more):
    lines: list[str] = []
    store = TokenStore(tmp_path / "spotify.toml")
    code = run_login(
        client_id,
        port=port,
        transport=transport,
        store=store,
        timeout=timeout,
        out=lines.append,
        opener=browser,
        **more,
    )
    browser.finish()
    return code, "\n".join(lines), store


def test_a_complete_login_stores_a_private_token(tmp_path):
    port = free_port()
    transport = FakeTransport(
        reply(payload={"access_token": "A", "refresh_token": "R", "expires_in": 3600}),
        reply(payload={"display_name": "Marie"}),
    )
    browser = Browser(port, answers=[lambda state: f"/callback?code=XYZ&state={state}"])
    code, text, store = run(tmp_path, transport, browser, port)
    assert code == 0
    assert "Connecté à Spotify en tant que Marie" in text
    assert str(store.path) in text
    assert browser.results[0][0] == 200
    assert "Spotify est connecté à MyDock" in browser.results[0][1]
    creds = store.load()
    assert creds is not None
    assert (creds.refresh_token, creds.access_token, creds.user, creds.port) == (
        "R",
        "A",
        "Marie",
        port,
    )
    assert stat.S_IMODE(store.path.stat().st_mode) == 0o600
    # Le code reçu est échangé avec le verifier dont l'empreinte est dans l'adresse de Spotify.
    query = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(browser.url).query))
    assert query["code_challenge"] == challenge_of(transport.form(0)["code_verifier"])
    assert transport.form(0)["code"] == "XYZ"
    assert transport.requests[1].headers["Authorization"] == "Bearer A"


def test_a_reply_with_the_wrong_state_is_ignored_and_the_right_one_still_works(tmp_path):
    port = free_port()
    transport = FakeTransport(
        reply(payload={"access_token": "A", "refresh_token": "R"}), reply(payload={"id": "u"})
    )
    browser = Browser(
        port,
        answers=[
            lambda state: "/callback?code=PIRATE&state=autre",
            lambda state: "/callback?code=PIRATE",
            lambda state: "/n-importe-quoi",
            lambda state: f"/callback?code=BON&state={state}",
        ],
    )
    code, _, store = run(tmp_path, transport, browser, port)
    assert code == 0
    assert [status for status, _ in browser.results] == [400, 400, 404, 200]
    assert transport.form(0)["code"] == "BON"  # le code du faux appel n'a servi à rien
    assert store.load() is not None


def test_a_refusal_on_the_spotify_page_stores_nothing(tmp_path):
    port = free_port()
    transport = FakeTransport()
    browser = Browser(port, answers=[lambda state: f"/callback?error=access_denied&state={state}"])
    code, text, store = run(tmp_path, transport, browser, port)
    assert code == 1
    assert "Connexion annulée côté Spotify (access_denied)" in text
    assert transport.requests == []
    assert store.load() is None


def test_no_answer_in_time_stores_nothing(tmp_path):
    port = free_port()
    code, text, store = run(tmp_path, FakeTransport(), Browser(port, answers=[]), port, timeout=0.6)
    assert code == 1
    assert "Pas de réponse de Spotify à temps" in text
    assert store.load() is None


def test_a_failing_exchange_or_profile_stores_nothing(tmp_path):
    port = free_port()
    browser = Browser(port, answers=[lambda state: f"/callback?code=C&state={state}"])
    code, text, store = run(
        tmp_path, FakeTransport(reply(400, {"error": "invalid_grant"})), browser, port
    )
    assert (code, store.load()) == (1, None)
    assert "refusé ou révoqué" in text

    port = free_port()
    browser = Browser(port, answers=[lambda state: f"/callback?code=C&state={state}"])
    transport = FakeTransport(
        reply(payload={"access_token": "A", "refresh_token": "R"}), reply(403)
    )
    code, text, store = run(tmp_path, transport, browser, port)
    assert (code, store.load()) == (1, None)  # un jeton que l'API refuse n'est pas gardé
    assert "User Management" in text


def test_a_wrong_client_id_is_refused_before_anything_is_opened(tmp_path):
    lines: list[str] = []
    opened: list[str] = []
    code = run_login(
        "pas-un-client-id",
        port=free_port(),
        store=TokenStore(tmp_path / "s.toml"),
        out=lines.append,
        opener=opened.append,
    )
    assert code == 2
    assert "32 caractères" in "\n".join(lines)
    assert opened == []


def test_a_busy_port_is_explained(tmp_path):
    with socket.socket() as taken:
        taken.bind(("127.0.0.1", 0))
        taken.listen()
        port = taken.getsockname()[1]
        lines: list[str] = []
        code = run_login(
            CLIENT_ID.upper(),  # les majuscules sont acceptées
            port=port,
            store=TokenStore(tmp_path / "s.toml"),
            out=lines.append,
            opener=lambda _url: None,
        )
    assert code == 1
    assert f"Le port {port}" in "\n".join(lines)
    assert "--port" in "\n".join(lines)


def test_the_login_prints_the_address_and_what_to_set_up_on_the_dashboard(tmp_path):
    port = free_port()
    browser = Browser(port, answers=[lambda state: f"/callback?error=access_denied&state={state}"])
    _, text, _ = run(tmp_path, FakeTransport(), browser, port)
    assert "https://developer.spotify.com/dashboard" in text
    assert f"http://127.0.0.1:{port}/callback" in text  # l'adresse de redirection à déclarer
    assert "https://accounts.spotify.com/authorize" in text
    assert "Premium" in text


def test_a_browser_that_cannot_open_does_not_stop_the_login(tmp_path):
    port = free_port()

    def broken(_url):
        raise RuntimeError("pas de navigateur")

    lines: list[str] = []
    code = run_login(
        CLIENT_ID,
        port=port,
        store=TokenStore(tmp_path / "s.toml"),
        out=lines.append,
        opener=broken,
        timeout=0.6,
    )
    assert code == 1  # on a attendu la réponse, qui n'est pas venue
    assert "Pas de réponse" in "\n".join(lines)


def test_the_browser_is_not_opened_when_asked_not_to(tmp_path):
    opened: list[str] = []
    code = run_login(
        CLIENT_ID,
        port=free_port(),
        store=TokenStore(tmp_path / "s.toml"),
        out=lambda _t: None,
        opener=opened.append,
        open_browser=False,
        timeout=0.6,
    )
    assert code == 1
    assert opened == []


# --- le transport : seulement les serveurs de Spotify ---------------------------------------------


@pytest.mark.parametrize(
    "url",
    [
        "http://api.spotify.com/v1/me",
        "https://evil.example/v1/me",
        "https://api.spotify.com.evil.example/v1/me",
        "https://api.spotify.com:8443/v1/me",
        "https://user@evil.example/",
        "https://api.spotify.com@evil.example/",
        "file:///etc/passwd",
        "ftp://api.spotify.com/",
        "",
    ],
)
def test_the_transport_never_talks_to_another_server(url):
    with pytest.raises(HttpError):
        http.urllib_transport(HttpRequest("GET", url), 5.0)


@pytest.mark.parametrize(
    "url", [f"{auth.API_URL}/me", auth.TOKEN_URL, "https://api.spotify.com:443/v1/me"]
)
def test_the_two_servers_of_spotify_are_allowed(url):
    http.check_url(url)


class FakeOpener:
    def __init__(self, outcome):
        self.outcome = outcome
        self.seen = None

    def open(self, request, timeout):
        self.seen = (request, timeout)
        if isinstance(self.outcome, Exception):
            raise self.outcome
        return self.outcome


class FakeResponse:
    status = 200
    headers = {"Content-Type": "application/json", "Retry-After": "3"}

    def __init__(self, body=b'{"ok": true}'):
        self.body = body

    def read(self, limit):
        return self.body[:limit]

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False


def test_the_transport_sends_the_request_and_reads_a_bounded_answer(monkeypatch):
    opener = FakeOpener(FakeResponse())
    monkeypatch.setattr(urllib.request, "build_opener", lambda *handlers: opener)
    response = http.urllib_transport(
        HttpRequest("POST", auth.TOKEN_URL, {"X-Test": "1"}, b"a=b"), 7.0
    )
    assert response.status == 200
    assert response.headers["retry-after"] == "3"  # noms d'en-têtes en minuscules
    assert json.loads(response.body) == {"ok": True}
    request, timeout = opener.seen
    assert timeout == 7.0
    assert request.get_method() == "POST"
    assert request.data == b"a=b"
    assert request.get_header("X-test") == "1"
    assert request.get_header("User-agent") == "MyDock-agent"


def test_an_error_status_is_an_answer_but_a_dead_network_is_an_error(monkeypatch):
    error = urllib.error.HTTPError(
        auth.API_URL,
        429,
        "Too Many",
        {"Retry-After": "9"},
        None,  # type: ignore[arg-type]
    )
    error.read = lambda limit=-1: b'{"error": {"status": 429}}'  # type: ignore[method-assign]
    monkeypatch.setattr(urllib.request, "build_opener", lambda *h: FakeOpener(error))
    response = http.urllib_transport(HttpRequest("GET", f"{auth.API_URL}/me"), 5.0)
    assert (response.status, response.headers["retry-after"]) == (429, "9")
    for failure in (urllib.error.URLError("dns"), TimeoutError(), ConnectionResetError()):
        monkeypatch.setattr(urllib.request, "build_opener", lambda *h, f=failure: FakeOpener(f))
        with pytest.raises(HttpError):
            http.urllib_transport(HttpRequest("GET", f"{auth.API_URL}/me"), 5.0)


def test_redirections_are_never_followed():
    handler = http._NoRedirect()
    assert handler.redirect_request() is None
