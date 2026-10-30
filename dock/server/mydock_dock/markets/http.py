"""Accès HTTPS aux fournisseurs : une seule porte, avec ses garde-fous.

Le dock n'appelle que des adresses fixes écrites dans les fournisseurs ; seuls des symboles validés
y sont insérés. Cette couche ajoute ce que `urllib` ne fait pas : délai total, taille maximale
de la réponse, pas de redirection hors de la même machine, décompression bornée. Elle ne renvoie
jamais d'exception pour un code HTTP (c'est le fournisseur qui le lit) : seulement pour un échec
réseau.
"""

from __future__ import annotations

import asyncio
import contextlib
import http.client
import socket
import ssl
import urllib.error
import urllib.parse
import urllib.request
import zlib
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Protocol

from mydock_dock import __version__
from mydock_dock.markets.errors import BadResponse, NetworkError

MAX_BODY_BYTES = 2 * 1024 * 1024
DEFAULT_TIMEOUT = 10.0
USER_AGENT = f"MyDock-dock/{__version__} (+https://github.com/neomars/MyDock)"


@dataclass(frozen=True)
class HttpResponse:
    status: int
    headers: Mapping[str, str]  # noms en minuscules
    body: bytes

    def header(self, name: str) -> str | None:
        return self.headers.get(name.lower())

    @property
    def retry_after(self) -> float | None:
        """Secondes à attendre d'après `Retry-After` (nombre de secondes seulement)."""
        raw = self.header("retry-after")
        try:
            value = float(raw) if raw is not None else None
        except ValueError:
            return None
        return value if value is not None and 0 <= value <= 3600 else None


class Fetcher(Protocol):
    async def get(
        self,
        url: str,
        *,
        params: Mapping[str, str] | None = None,
        headers: Mapping[str, str] | None = None,
        timeout: float = DEFAULT_TIMEOUT,
    ) -> HttpResponse:
        """GET. Lève NetworkError (pas de réponse) ou BadResponse (réponse démesurée)."""


class _SameHostRedirects(urllib.request.HTTPRedirectHandler):
    """Suit une redirection seulement vers la même machine, sans jamais repasser en clair."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # type: ignore[no-untyped-def]
        new = urllib.parse.urlsplit(newurl)
        old = urllib.parse.urlsplit(req.full_url)
        downgrade = old.scheme == "https" and new.scheme != "https"
        if downgrade or new.scheme not in ("http", "https") or new.hostname != old.hostname:
            return None
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def build_url(url: str, params: Mapping[str, str] | None) -> str:
    if not params:
        return url
    return f"{url}?{urllib.parse.urlencode(params, quote_via=urllib.parse.quote)}"


def _read_limited(handle: http.client.HTTPResponse | urllib.error.HTTPError, limit: int) -> bytes:
    body = handle.read(limit + 1)
    if len(body) > limit:
        raise BadResponse("Réponse démesurée : abandonnée.")
    return body


def _gunzip(body: bytes, limit: int) -> bytes:
    decoder = zlib.decompressobj(16 + zlib.MAX_WBITS)
    try:
        out = decoder.decompress(body, limit + 1)
    except zlib.error:
        raise BadResponse("Réponse compressée illisible.") from None
    if len(out) > limit or decoder.unconsumed_tail:
        raise BadResponse("Réponse démesurée : abandonnée.")
    return out


class UrllibFetcher:
    """Le vrai accès réseau (bibliothèque standard, dans un fil pour ne pas bloquer le serveur)."""

    def __init__(self, *, max_bytes: int = MAX_BODY_BYTES, context: ssl.SSLContext | None = None):
        self._max = max_bytes
        handlers: list[urllib.request.BaseHandler] = [_SameHostRedirects()]
        if context is not None:
            handlers.append(urllib.request.HTTPSHandler(context=context))
        self._opener = urllib.request.build_opener(*handlers)

    async def get(
        self,
        url: str,
        *,
        params: Mapping[str, str] | None = None,
        headers: Mapping[str, str] | None = None,
        timeout: float = DEFAULT_TIMEOUT,
    ) -> HttpResponse:
        full = build_url(url, params)
        return await asyncio.to_thread(self._get, full, dict(headers or {}), timeout)

    def _get(self, url: str, headers: dict[str, str], timeout: float) -> HttpResponse:
        request = urllib.request.Request(
            url,
            headers={
                "User-Agent": USER_AGENT,
                "Accept": "application/json",
                "Accept-Encoding": "gzip",
                **headers,
            },
        )
        host = urllib.parse.urlsplit(url).hostname or "?"
        try:
            with contextlib.closing(self._opener.open(request, timeout=timeout)) as handle:
                return self._response(
                    handle.status, handle.headers, _read_limited(handle, self._max)
                )
        except urllib.error.HTTPError as exc:  # le code est une réponse, pas un échec réseau
            with contextlib.closing(exc):
                return self._response(exc.code, exc.headers, _read_limited(exc, self._max))
        except TimeoutError:
            raise NetworkError(f"{host} ne répond pas (délai dépassé).") from None
        except urllib.error.URLError as exc:
            reason = exc.reason
            if isinstance(reason, TimeoutError):
                raise NetworkError(f"{host} ne répond pas (délai dépassé).") from None
            if isinstance(reason, socket.gaierror):
                raise NetworkError(
                    f"Le nom {host} est introuvable : le Pi n'a pas d'accès à Internet ou de DNS."
                ) from None
            if isinstance(reason, ssl.SSLError):
                raise NetworkError(f"Connexion sécurisée à {host} refusée (certificat).") from None
            raise NetworkError(f"Connexion à {host} impossible.") from None
        except (OSError, http.client.HTTPException):
            raise NetworkError(f"Connexion à {host} interrompue.") from None

    def _response(self, status: int, raw_headers: Mapping[str, str], body: bytes) -> HttpResponse:
        headers = {name.lower(): value for name, value in raw_headers.items()}
        if headers.get("content-encoding", "").lower() == "gzip":
            body = _gunzip(body, self._max)
        return HttpResponse(status, headers, body)
