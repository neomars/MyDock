"""Connexion à Spotify : le jeton de l'utilisateur, sans aucun secret.

C'est le flux OAuth « code d'autorisation avec PKCE » (RFC 7636), prévu pour les applications installées chez
l'utilisateur : aucun « client secret », donc rien à protéger dans le code ni à recopier sur le Pi.

1. `mydock-agent spotify login --client-id ID` ouvre la page de Spotify dans le navigateur de ce PC ;
2. l'utilisateur accepte ; Spotify le renvoie vers `http://127.0.0.1:8888/callback`, une adresse qui ne mène
   qu'à ce PC : la commande l'attend pendant quelques minutes, sur cette adresse seulement ;
3. elle échange le code contre un jeton et le range dans `~/.config/mydock/spotify.toml` (droits 600) ;
4. l'agent s'en sert ensuite, et le renouvelle seul (le jeton d'accès dure une heure).

Le « Client ID » est celui de l'application que l'utilisateur crée sur developer.spotify.com (il n'est pas secret).
"""

from __future__ import annotations

import base64
import hashlib
import http.server
import json
import logging
import os
import re
import secrets
import time
import urllib.parse
import webbrowser
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from mydock_agent.spotify.http import HttpError, HttpRequest, Transport, urllib_transport
from mydock_common.config import ConfigError, load_toml, write_toml_private

log = logging.getLogger(__name__)

AUTHORIZE_URL = "https://accounts.spotify.com/authorize"
TOKEN_URL = "https://accounts.spotify.com/api/token"
API_URL = "https://api.spotify.com/v1"
DEFAULT_PORT = 8888
LOGIN_TIMEOUT = 300.0
CLIENT_ID_RE = re.compile(r"^[0-9a-f]{32}$")
EXPIRY_MARGIN = 60.0  # on renouvelle un peu avant l'échéance

#: Ce que MyDock demande : voir la bibliothèque et l'état de la lecture, et lancer la lecture. Rien de plus :
#: jamais de quoi modifier la bibliothèque, les playlists ou le profil, ni lire l'adresse e-mail.
SCOPES = (
    "user-read-private",
    "user-read-playback-state",
    "user-modify-playback-state",
    "user-read-recently-played",
    "user-library-read",
    "user-follow-read",
    "playlist-read-private",
    "playlist-read-collaborative",
)

HEADER = (
    "MyDock - connexion à Spotify (agent du PC).\n"
    "Fichier privé (droits 600) : il contient le jeton de ton compte Spotify, ne le publie jamais.\n"
    "Pour le refaire ou le retirer : mydock-agent spotify login / logout."
)


class AuthError(Exception):
    """La connexion à Spotify ne peut pas continuer ; le message est affichable."""


def token_path() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / "mydock" / "spotify.toml"


# --- PKCE -----------------------------------------------------------------------------------------


def new_verifier() -> str:
    """Le secret à usage unique de cette connexion : 43 à 128 caractères parmi A-Z a-z 0-9 - . _ ~ (RFC 7636)."""
    return secrets.token_urlsafe(64)[:96]


def challenge_of(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def redirect_uri(port: int) -> str:
    return f"http://127.0.0.1:{port}/callback"


def authorize_url(client_id: str, port: int, state: str, challenge: str) -> str:
    query = urllib.parse.urlencode(
        {
            "response_type": "code",
            "client_id": client_id,
            "scope": " ".join(SCOPES),
            "redirect_uri": redirect_uri(port),
            "state": state,
            "code_challenge_method": "S256",
            "code_challenge": challenge,
        }
    )
    return f"{AUTHORIZE_URL}?{query}"


# --- le fichier du jeton ----------------------------------------------------------------------


@dataclass
class Credentials:
    client_id: str
    refresh_token: str
    access_token: str = ""
    expires_at: float = 0.0
    scope: str = ""
    user: str = ""
    port: int = DEFAULT_PORT

    def expired(self, now: float) -> bool:
        return not self.access_token or now >= self.expires_at - EXPIRY_MARGIN


class TokenStore:
    def __init__(self, path: Path | None = None):
        self.path = path or token_path()

    def load(self) -> Credentials | None:
        """Le jeton enregistré, ou None si l'utilisateur ne s'est jamais connecté."""
        if not self.path.exists():
            return None
        raw = load_toml(self.path)  # refuse un fichier lisible par d'autres
        client_id = raw.get("client_id")
        refresh = raw.get("refresh_token")
        if (
            not isinstance(client_id, str)
            or not CLIENT_ID_RE.fullmatch(client_id)
            or not isinstance(refresh, str)
            or not refresh
        ):
            raise ConfigError(
                f"{self.path} est incomplet : relance « mydock-agent spotify login »."
            )
        expires = raw.get("expires_at", 0.0)
        port = raw.get("port", DEFAULT_PORT)
        return Credentials(
            client_id=client_id,
            refresh_token=refresh,
            access_token=str(raw.get("access_token", "")),
            expires_at=float(expires) if isinstance(expires, int | float) else 0.0,
            scope=str(raw.get("scope", "")),
            user=str(raw.get("user", "")),
            port=port if isinstance(port, int) else DEFAULT_PORT,
        )

    def save(self, creds: Credentials) -> None:
        write_toml_private(
            self.path,
            {
                "client_id": creds.client_id,
                "refresh_token": creds.refresh_token,
                "access_token": creds.access_token,
                "expires_at": creds.expires_at,
                "scope": creds.scope,
                "user": creds.user,
                "port": creds.port,
            },
            header=HEADER,
        )

    def delete(self) -> bool:
        try:
            self.path.unlink()
        except FileNotFoundError:
            return False
        return True


# --- échanges avec accounts.spotify.com -------------------------------------------------------------


def _post_form(transport: Transport, fields: dict[str, str]) -> dict[str, Any]:
    request = HttpRequest(
        "POST",
        TOKEN_URL,
        {"Content-Type": "application/x-www-form-urlencoded"},
        urllib.parse.urlencode(fields).encode(),
    )
    try:
        response = transport(request, 10.0)
    except HttpError:
        raise AuthError("Spotify est injoignable depuis ce PC.") from None
    try:
        payload = json.loads(response.body or b"{}")
    except ValueError:
        payload = {}
    if response.status != 200 or not isinstance(payload, dict):
        reason = payload.get("error") if isinstance(payload, dict) else None
        detail = payload.get("error_description") if isinstance(payload, dict) else None
        if reason == "invalid_grant":
            raise AuthError(
                "Spotify a refusé ou révoqué la connexion : relance « mydock-agent spotify login »."
            )
        if reason == "invalid_client":
            raise AuthError(
                "Spotify ne connaît pas ce Client ID : vérifie-le sur developer.spotify.com."
            )
        raise AuthError(
            f"Spotify a refusé l'échange (code {response.status}{': ' + str(detail) if detail else ''})."
        )
    return payload


def _apply_token(payload: dict[str, Any], creds: Credentials, now: float) -> None:
    access = payload.get("access_token")
    if not isinstance(access, str) or not access:
        raise AuthError("Spotify n'a pas donné de jeton.")
    lifetime = payload.get("expires_in")
    creds.access_token = access
    creds.expires_at = now + (float(lifetime) if isinstance(lifetime, int | float) else 3600.0)
    if isinstance(payload.get("refresh_token"), str) and payload["refresh_token"]:
        creds.refresh_token = payload["refresh_token"]  # Spotify peut en donner un nouveau
    if isinstance(payload.get("scope"), str):
        creds.scope = payload["scope"]


def exchange_code(
    transport: Transport, client_id: str, code: str, port: int, verifier: str, now: float
) -> Credentials:
    payload = _post_form(
        transport,
        {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": redirect_uri(port),
            "client_id": client_id,
            "code_verifier": verifier,
        },
    )
    refresh = payload.get("refresh_token")
    if not isinstance(refresh, str) or not refresh:
        raise AuthError("Spotify n'a pas donné de jeton de renouvellement.")
    creds = Credentials(client_id=client_id, refresh_token=refresh, port=port)
    _apply_token(payload, creds, now)
    return creds


def refresh_token(transport: Transport, creds: Credentials, now: float) -> None:
    """Renouvelle le jeton d'accès (modifie `creds` en place)."""
    payload = _post_form(
        transport,
        {
            "grant_type": "refresh_token",
            "refresh_token": creds.refresh_token,
            "client_id": creds.client_id,
        },
    )
    _apply_token(payload, creds, now)


# --- la commande « spotify login » ---------------------------------------------------------------

_PAGE = """<!doctype html><html lang="fr"><meta charset="utf-8"><title>MyDock</title>
<body style="font-family:sans-serif;background:#0b0e14;color:#eef2f8;text-align:center;padding:12vh 1rem">
<h1>{title}</h1><p>{text}</p></body></html>"""


class _CallbackServer(http.server.HTTPServer):
    result: dict[str, str] | None = None
    expected_state: str = ""


class _Callback(http.server.BaseHTTPRequestHandler):
    server: _CallbackServer
    server_version = "MyDock"

    def log_message(self, format: str, *args: object) -> None:
        return

    def _send(self, status: int, title: str, text: str) -> None:
        body = _PAGE.format(title=title, text=text).encode()
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Referrer-Policy", "no-referrer")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        url = urllib.parse.urlsplit(self.path)
        if url.path != "/callback":
            self._send(404, "Introuvable", "Cette adresse ne sert qu'à la connexion de MyDock.")
            return
        query = {k: v[0] for k, v in urllib.parse.parse_qs(url.query).items() if v}
        if not secrets.compare_digest(query.get("state", ""), self.server.expected_state):
            self._send(
                400, "Connexion refusée", "Cette réponse ne correspond pas à la connexion demandée."
            )
            return
        self.server.result = query
        if "code" in query:
            self._send(
                200,
                "Spotify est connecté à MyDock",
                "Tu peux fermer cet onglet et revenir au terminal.",
            )
        else:
            self._send(
                200, "Connexion annulée", "Rien n'a été enregistré. Tu peux fermer cet onglet."
            )


def login_instructions(port: int) -> str:
    return (
        "Avant de continuer, une seule fois, sur https://developer.spotify.com/dashboard :\n"
        "  1. « Create app » (le nom est libre, par exemple « MyDock ») ; coche « Web API ».\n"
        f"  2. Dans « Redirect URIs », ajoute exactement :  {redirect_uri(port)}  puis « Save ».\n"
        "  3. Ouvre « Settings » et copie le « Client ID » (32 caractères).\n"
        "Depuis février 2026, Spotify n'accepte les applications de ce type que si leur propriétaire a un\n"
        "compte Premium ; seul ce compte (et les 5 que tu ajoutes dans « User Management ») peut s'y connecter."
    )


def run_login(
    client_id: str,
    *,
    port: int = DEFAULT_PORT,
    transport: Transport = urllib_transport,
    store: TokenStore | None = None,
    open_browser: bool = True,
    timeout: float = LOGIN_TIMEOUT,
    now: Callable[[], float] = time.time,
    out: Callable[[str], None] = print,
    opener: Callable[[str], object] = webbrowser.open,
) -> int:
    """Connecte ce PC à un compte Spotify. Retourne le code de sortie de la commande."""
    client_id = client_id.strip().lower()
    if not CLIENT_ID_RE.fullmatch(client_id):
        out(
            "Le Client ID doit faire 32 caractères (chiffres et lettres a-f) : copie-le depuis ton application Spotify."
        )
        return 2
    store = store or TokenStore()
    verifier = new_verifier()
    state = secrets.token_urlsafe(24)
    try:
        server = _CallbackServer(("127.0.0.1", port), _Callback)
    except OSError as exc:
        out(
            f"Le port {port} de ce PC n'est pas libre ({exc.strerror or exc.__class__.__name__}) : relance avec --port UN_AUTRE (et ajoute la même adresse dans l'application Spotify)."
        )
        return 1
    server.expected_state = state
    server.timeout = 0.5
    url = authorize_url(client_id, port, state, challenge_of(verifier))
    out("Connexion de MyDock à ton compte Spotify.\n")
    out(login_instructions(port) + "\n")
    out(
        "Ouvre cette adresse dans le navigateur de ce PC (elle s'ouvre toute seule si c'est possible) :\n"
    )
    out(f"  {url}\n")
    if open_browser:
        try:
            opener(url)
        except Exception:
            log.debug("navigateur non ouvert", exc_info=True)
    out(
        f"J'attends la réponse de Spotify sur {redirect_uri(port)} (jusqu'à {int(timeout // 60)} minutes)…"
    )
    deadline = now() + timeout
    try:
        while server.result is None and now() < deadline:
            server.handle_request()
    except KeyboardInterrupt:
        out("\nInterrompu.")
        return 130
    finally:
        server.server_close()
    result = server.result
    if result is None:
        out("Pas de réponse de Spotify à temps : rien n'a été enregistré. Relance la commande.")
        return 1
    if "code" not in result:
        out(
            f"Connexion annulée côté Spotify ({result.get('error', 'refusée')}). Rien n'a été enregistré."
        )
        return 1
    try:
        creds = exchange_code(transport, client_id, result["code"], port, verifier, now())
    except AuthError as exc:
        out(str(exc))
        return 1
    try:
        creds.user = whoami(transport, creds)
    except AuthError as exc:
        # La connexion a réussi mais l'API refuse la suite : on le dit sans rien enregistrer d'inutilisable.
        out(str(exc))
        return 1
    store.save(creds)
    out(
        f"\nConnecté à Spotify{' en tant que ' + creds.user if creds.user else ''}. Jeton enregistré dans {store.path} (droits 600)."
    )
    out("La page Spotify du dock peut maintenant chercher et lancer de la musique.")
    return 0


def whoami(transport: Transport, creds: Credentials) -> str:
    """Le nom du compte (pour le message de réussite et la page du dock)."""
    request = HttpRequest("GET", f"{API_URL}/me", {"Authorization": f"Bearer {creds.access_token}"})
    try:
        response = transport(request, 10.0)
    except HttpError:
        raise AuthError("Spotify est injoignable depuis ce PC.") from None
    if response.status == 403:
        raise AuthError(
            "Spotify refuse l'accès à ce compte : il doit être celui du propriétaire de l'application "
            "(Premium depuis février 2026) ou figurer dans « User Management » du tableau de bord développeur."
        )
    if response.status != 200:
        raise AuthError(f"Spotify a refusé de donner le profil (code {response.status}).")
    try:
        profile = json.loads(response.body)
    except ValueError:
        return ""
    if not isinstance(profile, dict):
        return ""
    return str(profile.get("display_name") or profile.get("id") or "")[:80]
