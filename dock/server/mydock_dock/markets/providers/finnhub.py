"""Finnhub : actions américaines, en secours, avec une clé d'API gratuite (facultatif).

Utile si Yahoo cesse un jour de répondre. La clé se met dans `[markets] finnhub_key` du fichier
de configuration du Pi (droits 600) ; elle part dans l'en-tête `X-Finnhub-Token`, jamais dans
l'adresse (donc jamais dans un journal), et n'est jamais envoyée à l'écran.

L'offre gratuite ne donne que le cours du moment (pas d'historique ni de nom d'entreprise) : le
mini-graphique et le nom restent ceux des autres fournisseurs.
"""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from typing import Any

from mydock_dock.markets.errors import BadResponse, NotFound, ProviderError, Unsupported
from mydock_dock.markets.http import Fetcher
from mydock_dock.markets.limits import Gate
from mydock_dock.markets.model import HistoryPoint, Instrument, Quote
from mydock_dock.markets.providers.base import (
    first_provider_error,
    number,
    parse_json,
    raise_for_status,
)

QUOTE_URL = "https://finnhub.io/api/v1/quote"


def parse_quote(body: Any) -> Quote:
    if not isinstance(body, dict):
        raise BadResponse("Réponse inattendue de Finnhub.")
    price = number(body.get("c"))
    stamp = number(body.get("t"))
    if not price or not stamp:  # une valeur inconnue revient avec des zéros partout
        raise NotFound("Finnhub ne connaît pas cette valeur.")
    previous = number(body.get("pc"))
    change = number(body.get("d"))
    pct = number(body.get("dp"))
    if change is None and previous:
        change = price - previous
    if pct is None and previous and change is not None:
        pct = change / previous * 100
    return Quote(
        price=price,
        currency="USD",
        source="finnhub",
        change=change,
        change_pct=pct,
        high=number(body.get("h")) or None,
        low=number(body.get("l")) or None,
        traded_at=stamp,
    )


class FinnhubProvider:
    id = "finnhub"
    label = "Finnhub"

    def __init__(self, fetcher: Fetcher, api_key: str, *, gate: Gate | None = None):
        self._fetcher = fetcher
        self._key = api_key
        self._gate = gate or Gate(interval=1.1, concurrency=1)  # 60 requêtes par minute au plus

    def supports(self, instrument: Instrument) -> bool:
        return instrument.kind == "stock" and instrument.symbol.isascii()

    async def _one(self, instrument: Instrument) -> Quote:
        async with self._gate:
            response = await self._fetcher.get(
                QUOTE_URL,
                params={"symbol": instrument.symbol},
                headers={"X-Finnhub-Token": self._key},
            )
        raise_for_status(response, self.label)
        return parse_quote(parse_json(response, self.label))

    async def quotes(
        self, instruments: Sequence[Instrument], currency: str
    ) -> dict[str, Quote | ProviderError]:
        async def fetch(instrument: Instrument) -> Quote | ProviderError:
            try:
                return await self._one(instrument)
            except ProviderError as exc:
                return exc

        done = await asyncio.gather(*(fetch(i) for i in instruments))
        results = {i.id: r for i, r in zip(instruments, done, strict=True)}
        down = first_provider_error(results)
        if down is not None:
            raise down
        return results

    async def history(
        self, instrument: Instrument, range_: str, currency: str
    ) -> list[HistoryPoint]:
        raise Unsupported("L'historique n'est pas offert par Finnhub (offre gratuite).")
