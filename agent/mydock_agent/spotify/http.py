"""Transport HTTP vers Spotify : une seule fonction, injectable dans les tests, et limitée aux serveurs de Spotify.

L'agent ne parle ici qu'à `accounts.spotify.com` (jetons) et `api.spotify.com` (données), en https, sans suivre
de redirection : aucune adresse venue du dock ni de l'API n'est jamais ouverte.
"""

from __future__ import annotations

import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass, field

ALLOWED_HOSTS = frozenset({"accounts.spotify.com", "api.spotify.com"})
MAX_BODY = 2 * 1024 * 1024
USER_AGENT = "MyDock-agent"


@dataclass(frozen=True)
class HttpRequest:
    method: str
    url: str
    headers: dict[str, str] = field(default_factory=dict)
    body: bytes | None = None


@dataclass(frozen=True)
class HttpResponse:
    status: int
    headers: dict[str, str]
    body: bytes


class HttpError(Exception):
    """Pas de réponse du tout : réseau coupé, nom introuvable, délai dépassé, hôte refusé."""


Transport = Callable[[HttpRequest, float], HttpResponse]


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args: object, **kwargs: object) -> None:
        return None


def check_url(url: str) -> None:
    parts = urllib.parse.urlsplit(url)
    if (
        parts.scheme != "https"
        or parts.hostname not in ALLOWED_HOSTS
        or parts.port not in (None, 443)
    ):
        raise HttpError("Adresse refusée : l'agent ne parle qu'aux serveurs de Spotify, en https.")


def urllib_transport(request: HttpRequest, timeout: float) -> HttpResponse:
    check_url(request.url)
    opener = urllib.request.build_opener(_NoRedirect)
    built = urllib.request.Request(
        request.url,
        data=request.body,
        method=request.method,
        headers={"User-Agent": USER_AGENT, "Accept": "application/json", **request.headers},
    )
    try:
        with opener.open(built, timeout=timeout) as response:
            return HttpResponse(
                response.status,
                {k.lower(): v for k, v in response.headers.items()},
                response.read(MAX_BODY),
            )
    except urllib.error.HTTPError as exc:  # une réponse d'erreur (401, 403, 429…) reste une réponse
        try:
            body = exc.read(MAX_BODY)
        except OSError:
            body = b""
        return HttpResponse(exc.code, {k.lower(): v for k, v in exc.headers.items()}, body)
    except (OSError, ValueError) as exc:  # URLError, délai, TLS, nom introuvable
        raise HttpError(f"{exc.__class__.__name__}") from None
