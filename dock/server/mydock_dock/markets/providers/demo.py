"""Cotations de démonstration : des valeurs plausibles qui bougent doucement, sans réseau.

Sert au mode démo (`mydock-dock run --demo-markets`, captures d'écran, tests de bout en bout). Ce
ne sont que des courbes calculées : le prix à un instant donné est toujours le même, ce qui rend
les tests stables.
"""

from __future__ import annotations

import math
import time
import zlib
from collections.abc import Callable, Sequence
from dataclasses import dataclass

from mydock_dock.markets.errors import NotFound, ProviderError
from mydock_dock.markets.model import (
    SPARK_POINTS,
    HistoryPoint,
    Instrument,
    Quote,
    rounded,
)

DAY = 86_400.0
# période -> (nombre de points, secondes entre deux points)
HISTORY_STEPS = {"1d": (96, 900), "5d": (120, 3600), "1mo": (120, 21_600), "1y": (180, 172_800)}


@dataclass(frozen=True)
class DemoAsset:
    kind: str
    symbol: str
    name: str
    base: float  # en euros pour une crypto, dans la devise de la valeur sinon
    currency: str | None = None  # None : la devise choisie (cryptos)
    state: str | None = None
    swing: float = 0.02  # amplitude des oscillations


CATALOG: tuple[DemoAsset, ...] = (
    DemoAsset("crypto", "BTC", "Bitcoin", 61_250.0, swing=0.025),
    DemoAsset("crypto", "ETH", "Ethereum", 2_840.0, swing=0.03),
    DemoAsset("crypto", "SOL", "Solana", 128.4, swing=0.045),
    DemoAsset("crypto", "DOGE", "Dogecoin", 0.1234, swing=0.05),
    DemoAsset("crypto", "XRP", "XRP", 0.5321, swing=0.03),
    DemoAsset("stock", "MSTR", "Strategy Inc", 182.4, "USD", "open", 0.04),
    DemoAsset("stock", "AAPL", "Apple Inc.", 231.2, "USD", "open", 0.012),
    DemoAsset("stock", "^FCHI", "CAC 40", 7_512.3, "EUR", "closed", 0.008),
    DemoAsset("stock", "CW8.PA", "Amundi MSCI World UCITS ETF", 489.2, "EUR", "closed", 0.007),
    DemoAsset("stock", "EURUSD=X", "EUR/USD", 1.0832, "USD", None, 0.004),
)
BY_ID = {f"{a.kind}:{a.symbol}": a for a in CATALOG}
USD_PER_EUR = 1.08


def _phase(symbol: str) -> float:
    return (zlib.crc32(symbol.encode()) % 6283) / 1000.0


def level(asset: DemoAsset, at: float, currency: str) -> float:
    """Le prix de l'actif à l'instant `at` : deux oscillations lentes et un peu de bruit."""
    phase = _phase(asset.symbol)
    slow = math.sin(at / 9_000.0 + phase) * asset.swing
    fast = math.sin(at / 700.0 + 2 * phase) * asset.swing * 0.25
    noise = (
        (zlib.crc32(f"{asset.symbol}{int(at // 20)}".encode()) % 1000 / 1000.0 - 0.5)
        * asset.swing
        * 0.05
    )
    price = asset.base * (1 + slow + fast + noise)
    if asset.kind == "crypto" and currency == "USD":
        price *= USD_PER_EUR
    return price


def reference_time(asset: DemoAsset, now: float) -> float:
    """L'instant de la cotation : maintenant, ou la clôture de la veille si le marché est fermé."""
    if asset.state == "closed":
        return math.floor(now / DAY) * DAY - 2 * 3600
    return now


class DemoProvider:
    id = "demo"
    label = "Démonstration"

    def __init__(self, *, now: Callable[[], float] = time.time):
        self._now = now

    def supports(self, instrument: Instrument) -> bool:
        return True

    def _quote(self, asset: DemoAsset, currency: str) -> Quote:
        now = self._now()
        ref = reference_time(asset, now)
        # Marché fermé : la courbe est celle de la dernière séance, pas un palier jusqu'à minuit.
        step = 600.0 if asset.state == "closed" else 1_800.0
        price = level(asset, ref, currency)
        before = level(asset, ref - DAY, currency)
        change = price - before
        spark = tuple(
            rounded(level(asset, ref - (SPARK_POINTS - 1 - i) * step, currency)) or 0.0
            for i in range(SPARK_POINTS)
        )
        return Quote(
            price=round(price, 6),
            currency=currency if asset.kind == "crypto" else asset.currency,
            source="demo",
            name=asset.name,
            change=rounded(change),
            change_pct=rounded(change / before * 100, 4),
            high=round(max(spark), 6),
            low=round(min(spark), 6),
            state=asset.state,
            traded_at=ref,
            spark=spark,
        )

    async def quotes(
        self, instruments: Sequence[Instrument], currency: str
    ) -> dict[str, Quote | ProviderError]:
        out: dict[str, Quote | ProviderError] = {}
        for instrument in instruments:
            asset = BY_ID.get(instrument.id)
            out[instrument.id] = (
                self._quote(asset, currency)
                if asset
                else NotFound(f"« {instrument.symbol} » n'existe pas dans la démonstration.")
            )
        return out

    async def history(
        self, instrument: Instrument, range_: str, currency: str
    ) -> list[HistoryPoint]:
        asset = BY_ID.get(instrument.id)
        if asset is None:
            raise NotFound(f"« {instrument.symbol} » n'existe pas dans la démonstration.")
        count, step = HISTORY_STEPS[range_]
        end = reference_time(asset, self._now())
        return [
            HistoryPoint(
                int(end - (count - 1 - i) * step),
                round(level(asset, end - (count - 1 - i) * step, currency), 6),
            )
            for i in range(count)
        ]
