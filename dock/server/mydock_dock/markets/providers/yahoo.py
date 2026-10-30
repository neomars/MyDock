"""Yahoo Finance : actions, indices, devises et cryptos, sans clé ni compte.

C'est l'accès que `dashboard_crypto` utilise déjà : l'API `v8/finance/chart`, une requête par valeur.
Yahoo refuse (HTTP 429) les clients sans `User-Agent` de navigateur ; on s'en donne un. La réponse
contient à la fois la cotation (`meta`) et la courbe du jour (`indicators`), donc une seule
requête fournit le prix, la variation depuis la clôture de la veille et le mini-graphique.

Une crypto s'écrit `BTC-EUR` chez Yahoo : le symbole suivi de la devise choisie.
"""

from __future__ import annotations

import asyncio
import time
import urllib.parse
from collections.abc import Callable, Sequence
from typing import Any

from mydock_dock.markets.errors import BadResponse, NotFound, ProviderError
from mydock_dock.markets.http import Fetcher
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

CHART_URL = "https://query2.finance.yahoo.com/v8/finance/chart/"
BROWSER_USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/126.0 Safari/537.36"
)
# période -> (range, interval) de l'API
HISTORY_QUERY = {
    "1d": ("1d", "5m"),
    "5d": ("5d", "15m"),
    "1mo": ("1mo", "1h"),
    "1y": ("1y", "1d"),
}


def yahoo_symbol(instrument: Instrument, currency: str) -> str:
    return f"{instrument.symbol}-{currency}" if instrument.kind == "crypto" else instrument.symbol


def _result(body: Any, label: str) -> dict[str, Any]:
    chart = body.get("chart") if isinstance(body, dict) else None
    if not isinstance(chart, dict):
        raise BadResponse(f"Réponse inattendue de {label}.")
    error = chart.get("error")
    if isinstance(error, dict):
        description = str(error.get("description") or error.get("code") or "erreur")
        if str(error.get("code", "")).lower() == "not found" or "delisted" in description.lower():
            raise NotFound(f"{label} ne connaît pas cette valeur.")
        raise BadResponse(f"{label} : {description}")
    results = chart.get("result")
    result = results[0] if isinstance(results, list) and results else None
    if not isinstance(result, dict):
        raise NotFound(f"{label} ne connaît pas cette valeur.")
    return result


def _series(result: dict[str, Any]) -> list[tuple[int, float, float | None, float | None]]:
    """(heure, clôture, plus haut, plus bas) des lignes complètes de la courbe."""
    stamps = result.get("timestamp")
    quote = ((result.get("indicators") or {}).get("quote") or [{}])[0]
    if not isinstance(stamps, list) or not isinstance(quote, dict):
        return []
    closes = quote.get("close") or []
    highs = quote.get("high") or []
    lows = quote.get("low") or []
    rows = []
    for i, stamp in enumerate(stamps):
        close = number(closes[i]) if i < len(closes) else None
        at = number(stamp)
        if close is None or at is None:
            continue
        rows.append(
            (
                int(at),
                close,
                number(highs[i]) if i < len(highs) else None,
                number(lows[i]) if i < len(lows) else None,
            )
        )
    return rows


def market_state(meta: dict[str, Any], now: float) -> str | None:
    """open / pre / post / closed d'après les horaires de séance que Yahoo donne ; None sans eux."""
    periods = meta.get("currentTradingPeriod")
    if not isinstance(periods, dict):
        return None

    def bounds(name: str) -> tuple[float, float] | None:
        period = periods.get(name)
        if not isinstance(period, dict):
            return None
        start, end = number(period.get("start")), number(period.get("end"))
        return (start, end) if start is not None and end is not None else None

    regular = bounds("regular")
    if regular is None:
        return None
    pre, post = bounds("pre"), bounds("post")
    if regular[0] <= now < regular[1]:
        return "open"
    if pre and pre[0] <= now < regular[0]:
        return "pre"
    if post and regular[1] <= now < post[1]:
        return "post"
    return "closed"


def parse_quote(
    body: Any, instrument: Instrument, *, now: float, label: str = "Yahoo Finance"
) -> Quote:
    result = _result(body, label)
    meta = result.get("meta")
    if not isinstance(meta, dict):
        raise BadResponse(f"Réponse inattendue de {label}.")
    rows = _series(result)
    price = number(meta.get("regularMarketPrice"))
    if price is None and rows:
        price = rows[-1][1]
    if price is None:
        raise BadResponse(f"{label} n'a pas de cours pour {instrument.symbol}.")
    previous = number(meta.get("chartPreviousClose"))
    if previous is None:
        previous = number(meta.get("previousClose"))
    change = price - previous if previous else None
    change_pct = change / previous * 100 if change is not None and previous else None
    high = number(meta.get("regularMarketDayHigh"))
    low = number(meta.get("regularMarketDayLow"))
    if rows:
        highs = [r[2] for r in rows if r[2] is not None]
        lows = [r[3] for r in rows if r[3] is not None]
        high = high if high is not None else (max(highs) if highs else None)
        low = low if low is not None else (min(lows) if lows else None)
    name = (
        crypto_name(instrument.symbol)
        if instrument.kind == "crypto"
        else (meta.get("longName") or meta.get("shortName"))
    )
    currency = meta.get("currency")
    traded = number(meta.get("regularMarketTime"))
    return Quote(
        price=price,
        currency=currency if isinstance(currency, str) and currency else None,
        source="yahoo",
        name=str(name) if name else None,
        change=rounded(change),
        change_pct=rounded(change_pct, 4),
        high=high,
        low=low,
        state=None if instrument.kind == "crypto" else market_state(meta, now),
        traded_at=traded,
        spark=tuple(rounded(r[1]) or 0.0 for r in downsample(rows, SPARK_POINTS)),
    )


def parse_history(body: Any, label: str = "Yahoo Finance") -> list[HistoryPoint]:
    rows = _series(_result(body, label))
    return [
        HistoryPoint(at, close) for at, close, _high, _low in downsample(rows, MAX_HISTORY_POINTS)
    ]


class YahooProvider:
    id = "yahoo"
    label = "Yahoo Finance"

    def __init__(
        self,
        fetcher: Fetcher,
        *,
        gate: Gate | None = None,
        now: Callable[[], float] = time.time,
        base_url: str = CHART_URL,
    ):
        self._fetcher = fetcher
        self._gate = gate or Gate(interval=0.4, concurrency=2)
        self._now = now
        self._base = base_url

    def supports(self, instrument: Instrument) -> bool:
        return True

    async def _chart(self, symbol: str, range_: str, interval: str) -> Any:
        url = self._base + urllib.parse.quote(symbol, safe="")
        async with self._gate:
            response = await self._fetcher.get(
                url,
                params={"range": range_, "interval": interval, "includePrePost": "false"},
                headers={"User-Agent": BROWSER_USER_AGENT},
            )
        if response.status == 404:  # Yahoo explique « symbol may be delisted » dans le corps
            try:
                return parse_json(response, self.label)
            except ProviderError:
                raise NotFound(f"{self.label} ne connaît pas cette valeur.") from None
        raise_for_status(response, self.label)
        return parse_json(response, self.label)

    async def _one(self, instrument: Instrument, currency: str) -> Quote:
        body = await self._chart(yahoo_symbol(instrument, currency), "1d", "5m")
        return parse_quote(body, instrument, now=self._now(), label=self.label)

    async def quotes(
        self, instruments: Sequence[Instrument], currency: str
    ) -> dict[str, Quote | ProviderError]:
        async def fetch(instrument: Instrument) -> Quote | ProviderError:
            try:
                return await self._one(instrument, currency)
            except ProviderError as exc:
                return exc

        done = await asyncio.gather(*(fetch(i) for i in instruments))
        results = {
            instrument.id: result for instrument, result in zip(instruments, done, strict=True)
        }
        down = first_provider_error(results)
        if down is not None:
            raise down
        return results

    async def history(
        self, instrument: Instrument, range_: str, currency: str
    ) -> list[HistoryPoint]:
        query = HISTORY_QUERY[range_]
        body = await self._chart(yahoo_symbol(instrument, currency), *query)
        return parse_history(body, self.label)
