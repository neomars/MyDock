"""Connexions sortantes du dock (cotations, films de l'économiseur) : un chemin qui ne reste pas bloqué.

`socket.create_connection` essaie les adresses d'un nom une à une, avec le délai entier pour chacune. Sur un réseau
domestique dont l'IPv6 est annoncé mais ne mène nulle part (cas courant), la première adresse (IPv6) fait donc attendre
tout le délai, puis la suivante, et la demande finit « délai dépassé » alors que l'IPv4 marchait. Ici : IPv4 et IPv6
en alternance en commençant par l'IPv4, quelques secondes par adresse, au plus quatre essais.
"""

from __future__ import annotations

import http.client
import socket
import ssl
import urllib.request
from collections.abc import Callable
from typing import Any

ATTEMPT_TIMEOUT = 3.0  # par adresse
MAX_ATTEMPTS = 4


def ordered_addresses(infos: list[Any]) -> list[Any]:
    """Les adresses d'un nom, en alternant IPv4 et IPv6 à partir de l'IPv4 : une famille en panne ne bloque pas l'autre."""
    v4 = [i for i in infos if i[0] == socket.AF_INET]
    v6 = [i for i in infos if i[0] != socket.AF_INET]
    mixed: list[Any] = []
    for index in range(max(len(v4), len(v6))):
        if index < len(v4):
            mixed.append(v4[index])
        if index < len(v6):
            mixed.append(v6[index])
    return mixed


def connect_fast(
    address: tuple[str, int],
    timeout: float | None = None,
    source_address: tuple[str, int] | None = None,
) -> socket.socket:
    """Comme `socket.create_connection`, sans attendre le délai entier sur chaque adresse."""
    host, port = address
    infos = ordered_addresses(socket.getaddrinfo(host, port, 0, socket.SOCK_STREAM))
    attempt = (
        ATTEMPT_TIMEOUT if not isinstance(timeout, (int, float)) else min(timeout, ATTEMPT_TIMEOUT)
    )
    error: OSError | None = None
    for family, kind, proto, _name, sockaddr in infos[:MAX_ATTEMPTS]:
        sock: socket.socket | None = None
        try:
            sock = socket.socket(family, kind, proto)
            sock.settimeout(attempt)
            if source_address:
                sock.bind(source_address)
            sock.connect(sockaddr)
            sock.settimeout(timeout if isinstance(timeout, (int, float)) else None)
            return sock
        except OSError as exc:
            error = exc
            if sock is not None:
                sock.close()
    if error is not None:
        raise error
    raise OSError(f"Aucune adresse pour {host}")


class FastHTTPSConnection(http.client.HTTPSConnection):
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._create_connection: Callable[..., socket.socket] = connect_fast  # type: ignore[assignment]


class FastHTTPSHandler(urllib.request.HTTPSHandler):
    def https_open(self, req: urllib.request.Request) -> http.client.HTTPResponse:
        return self.do_open(FastHTTPSConnection, req, context=self._context)  # type: ignore[attr-defined,arg-type]


def build_opener(
    *handlers: urllib.request.BaseHandler, context: ssl.SSLContext | None = None
) -> urllib.request.OpenerDirector:
    """Un ouvreur urllib dont les connexions https passent par `connect_fast`."""
    return urllib.request.build_opener(FastHTTPSHandler(context=context), *handlers)
