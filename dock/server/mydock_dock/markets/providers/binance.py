"""Binance : les cryptos, par l'API publique du marché au comptant (sans clé ni compte).

Une seule requête donne le cours et la variation sur 24 h de toutes les cryptos suivies
(`/api/v3/ticker/24hr?symbols=[...]`) ; le mini-graphique vient des bougies de 30 minutes
(`/api/v3/klines`), redemandées seulement toutes les cinq minutes.

Binance coupe l'accès depuis certains pays (HTTP 451) : le miroir `data-api.binance.vision`,
réservé aux données de marché, prend le relais. Une paire inconnue fait échouer toute la requête
groupée (HTTP 400, « Invalid symbol ») : on la rejoue alors paire par paire pour que seule la
valeur fautive soit marquée « introuvable ».
"""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Callable, Sequence
from dataclasses import replace
from typing import Any

from mydock_dock.markets.errors import (
    BadResponse,
    Blocked,
    NetworkError,
    NotFound,
    ProviderError,
    Unsupported,
)
from mydock_dock.markets.http import Fetcher, HttpResponse
from mydock_dock.markets.limits import Gate
from mydock_dock.markets.model import (
    MAX_HISTORY_POINTS,
    SPARK_POINTS,
    HistoryPoint,
    Instrument,
    Quote,
    crypto_name,
    downsample,
    rounded,
)
from mydock_dock.markets.providers.base import (
    first_provider_error,
    number,
    parse_json,
    raise_for_status,
)

HOSTS = ("https://api.binance.com", "https://data-api.binance.vision")
SPARK_TTL = 300.0
INVALID_SYMBOL = -1121
# période -> (intervalle des bougies, nombre de bougies)
HISTORY_QUERY = {
    "1d": ("5m", 288),
    "5d": ("30m", 240),
    "1mo": ("4h", 180),
    "1y": ("1d", 365),
}
# En dollars, c'est la paire en USDT (le dollar stable) qui fait référence chez Binance.
QUOTE_ASSET = {"EUR": "EUR", "USD": "USDT"}


def pair_of(instrument: Instrument, currency: str) -> str:
    return instrument.symbol + QUOTE_ASSET[currency]


def parse_ticker(item: Any, instrument: Instrument, currency: str) -> Quote:
    if not isinstance(item, dict):
        raise BadResponse("Réponse inattendue de Binance.")
    price = number(item.get("lastPrice"))
    if price is None:
        raise BadResponse(f"Binance n'a pas de cours pour {pair_of(instrument, currency)}.")
    closed = number(item.get("closeTime"))
    return Quote(
        price=price,
        currency=currency,
        source="binance",
        name=crypto_name(instrument.symbol),
        change=number(item.get("priceChange")),
        change_pct=number(item.get("priceChangePercent")),
        high=number(item.get("highPrice")),
        low=number(item.get("lowPrice")),
        traded_at=closed / 1000 if closed else None,
    )


def parse_klines(body: Any) -> list[HistoryPoint]:
    if not isinstance(body, list):
        raise BadResponse("Réponse inattendue de Binance.")
    points = []
    for row in body:
        if not isinstance(row, list) or len(row) < 5:
            continue
        opened, close = number(row[0]), number(row[4])
        if opened is not None and close is not None:
            points.append(HistoryPoint(int(opened // 1000), close))
    return points


def _api_error(response: HttpResponse) -> tuple[int | None, str | None]:
    try:
        body = json.loads(response.body)
    except (ValueError, UnicodeDecodeError):
        return None, None
    if isinstance(body, dict):
        code = body.get("code")
        msg = body.get("msg")
        return code if isinstance(code, int) else None, msg if isinstance(msg, str) else None
    return None, None


class BinanceProvider:
    id = "binance"
    label = "Binance"

    def __init__(
        self,
        fetcher: Fetcher,
        *,
        hosts: Sequence[str] = HOSTS,
        gate: Gate | None = None,
        now: Callable[[], float] = time.time,
        monotonic: Callable[[], float] = time.monotonic,
    ):
        self._fetcher = fetcher
        self._hosts = list(hosts)
        self._gate = gate or Gate(interval=0.15, concurrency=3)
        self._now = now
        self._monotonic = monotonic
        self._sparks: dict[str, tuple[float, tuple[float, ...]]] = {}

    def supports(self, instrument: Instrument) -> bool:
        return instrument.kind == "crypto"

    # --- requêtes ------------------------------------------------------------------------

    async def _get(self, path: str, params: dict[str, str]) -> HttpResponse:
        """Essaie les serveurs dans l'ordre ; celui qui a répondu devient le premier choix."""
        first_error: ProviderError | None = None
        for index, host in enumerate(self._hosts):
            try:
                async with self._gate:
                    response = await self._fetcher.get(host + path, params=params)
            except NetworkError as exc:
                first_error = first_error or exc
                continue
            if response.status in (
                403,
                451,
            ):  # pays ou adresse refusés : le miroir répondra peut-être
                try:
                    raise_for_status(response, self.label)
                except Blocked as exc:
                    first_error = first_error or exc
                    continue
            if index:
                self._hosts.insert(0, self._hosts.pop(index))
            return response
        assert first_error is not None
        raise first_error

    async def _json(self, path: str, params: dict[str, str]) -> Any:
        response = await self._get(path, params)
        raise_for_status(response, self.label)
        return parse_json(response, self.label)

    # --- cotations -----------------------------------------------------------------------

    async def quotes(
        self, instruments: Sequence[Instrument], currency: str
    ) -> dict[str, Quote | ProviderError]:
        pairs = {pair_of(i, currency): i for i in instruments}
        if not pairs:
            return {}
        results: dict[str, Quote | ProviderError]
        try:
            results = await self._batch(pairs, currency)
        except NotFound:
            results = await self._one_by_one(pairs, currency)
        await self._add_sparks(results, pairs)
        down = first_provider_error(results)
        if down is not None:
            raise down
        return results

    async def _batch(
        self, pairs: dict[str, Instrument], currency: str
    ) -> dict[str, Quote | ProviderError]:
        listing = json.dumps(list(pairs), separators=(",", ":"))
        response = await self._get("/api/v3/ticker/24hr", {"symbols": listing})
        if response.status == 400 and _api_error(response)[0] == INVALID_SYMBOL:
            raise NotFound("Une paire est inconnue de Binance.")
        raise_for_status(response, self.label)
        body = parse_json(response, self.label)
        if not isinstance(body, list):
            raise BadResponse("Réponse inattendue de Binance.")
        by_pair = {t.get("symbol"): t for t in body if isinstance(t, dict)}
        out: dict[str, Quote | ProviderError] = {}
        for pair, instrument in pairs.items():
            item = by_pair.get(pair)
            if item is None:
                out[instrument.id] = NotFound(f"Binance n'a pas de paire {pair}.")
                continue
            try:
                out[instrument.id] = parse_ticker(item, instrument, currency)
            except ProviderError as exc:
                out[instrument.id] = exc
        return out

    async def _one_by_one(
        self, pairs: dict[str, Instrument], currency: str
    ) -> dict[str, Quote | ProviderError]:
        async def one(pair: str, instrument: Instrument) -> Quote | ProviderError:
            try:
                response = await self._get("/api/v3/ticker/24hr", {"symbol": pair})
                if response.status == 400 and _api_error(response)[0] == INVALID_SYMBOL:
                    raise NotFound(f"Binance n'a pas de paire {pair}.")
                raise_for_status(response, self.label)
                return parse_ticker(parse_json(response, self.label), instrument, currency)
            except ProviderError as exc:
                return exc

        done = await asyncio.gather(*(one(p, i) for p, i in pairs.items()))
        return {i.id: r for (_p, i), r in zip(pairs.items(), done, strict=True)}

    # --- mini-graphiques -----------------------------------------------------------------

    async def _add_sparks(
        self, results: dict[str, Quote | ProviderError], pairs: dict[str, Instrument]
    ) -> None:
        wanted = [
            (pair, instrument)
            for pair, instrument in pairs.items()
            if isinstance(results.get(instrument.id), Quote)
        ]
        sparks = await asyncio.gather(*(self._spark(pair) for pair, _ in wanted))
        for (_pair, instrument), spark in zip(wanted, sparks, strict=True):
            quote = results[instrument.id]
            if spark and isinstance(quote, Quote):
                results[instrument.id] = replace(quote, spark=spark)

    async def _spark(self, pair: str) -> tuple[float, ...]:
        cached = self._sparks.get(pair)
        if cached and self._monotonic() - cached[0] < SPARK_TTL:
            return cached[1]
        try:
            body = await self._json(
                "/api/v3/klines", {"symbol": pair, "interval": "30m", "limit": str(SPARK_POINTS)}
            )
            spark = tuple(rounded(p.close) or 0.0 for p in parse_klines(body))
        except ProviderError:
            return (
                cached[1] if cached else ()
            )  # le mini-graphique n'est jamais une raison d'échouer
        self._sparks[pair] = (self._monotonic(), spark)
        return spark

    # --- historique ----------------------------------------------------------------------

    async def history(
        self, instrument: Instrument, range_: str, currency: str
    ) -> list[HistoryPoint]:
        if instrument.kind != "crypto":
            raise Unsupported("Binance ne cote que des cryptos.")
        interval, limit = HISTORY_QUERY[range_]
        body = await self._json(
            "/api/v3/klines",
            {"symbol": pair_of(instrument, currency), "interval": interval, "limit": str(limit)},
        )
        return downsample(parse_klines(body), MAX_HISTORY_POINTS)
