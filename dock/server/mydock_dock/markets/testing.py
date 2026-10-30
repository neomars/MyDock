"""Aides de test pour la bourse : un faux accès réseau qui rejoue des réponses préparées.

Jamais importé en production (comme `mydock_dock.testing`).
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any

from mydock_dock.markets.errors import NetworkError
from mydock_dock.markets.http import DEFAULT_TIMEOUT, HttpResponse, build_url

Reply = HttpResponse | Exception | Callable[["Call"], "HttpResponse | Exception"]


@dataclass
class Call:
    url: str
    params: dict[str, str]
    headers: dict[str, str]
    timeout: float

    @property
    def full_url(self) -> str:
        return build_url(self.url, self.params)


def json_response(
    data: Any, status: int = 200, headers: Mapping[str, str] | None = None
) -> HttpResponse:
    return HttpResponse(
        status, {k.lower(): v for k, v in (headers or {}).items()}, json.dumps(data).encode()
    )


def text_response(
    text: str, status: int = 200, headers: Mapping[str, str] | None = None
) -> HttpResponse:
    return HttpResponse(status, {k.lower(): v for k, v in (headers or {}).items()}, text.encode())


@dataclass
class FakeFetcher:
    """Répond selon des routes : la première dont l'adresse contient le motif (ou que le prédicat accepte).

    Une route peut donner une liste de réponses, servies dans l'ordre (la dernière se répète).
    Une requête sans route est une erreur réseau, notée dans `unexpected`.
    """

    calls: list[Call] = field(default_factory=list)
    unexpected: list[str] = field(default_factory=list)
    _routes: list[tuple[Callable[[Call], bool], list[Reply]]] = field(default_factory=list)

    def on(self, match: str | Callable[[Call], bool], *replies: Reply) -> FakeFetcher:
        test = (lambda call: match in call.url) if isinstance(match, str) else match
        self._routes.append((test, list(replies)))
        return self

    def calls_to(self, fragment: str) -> list[Call]:
        return [c for c in self.calls if fragment in c.url]

    async def get(
        self,
        url: str,
        *,
        params: Mapping[str, str] | None = None,
        headers: Mapping[str, str] | None = None,
        timeout: float = DEFAULT_TIMEOUT,
    ) -> HttpResponse:
        call = Call(url, dict(params or {}), dict(headers or {}), timeout)
        self.calls.append(call)
        for test, replies in self._routes:
            if test(call):
                reply = replies.pop(0) if len(replies) > 1 else replies[0]
                if callable(reply):
                    reply = reply(call)
                if isinstance(reply, Exception):
                    raise reply
                return reply
        self.unexpected.append(call.full_url)
        raise NetworkError(f"requête non prévue par le test : {call.full_url}")
