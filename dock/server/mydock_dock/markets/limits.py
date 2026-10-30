"""Politesse envers les fournisseurs : débit limité, requêtes simultanées bornées, pause après un refus.

Les fournisseurs gratuits ne publient pas toujours leurs quotas et coupent ceux qui insistent
(Yahoo répond 429 aux clients trop pressés). Chaque fournisseur a donc sa « porte » : un écart minimal
entre deux départs, un nombre maximal de requêtes en vol et, après un refus, une pause qui croît
(en respectant `Retry-After` quand le fournisseur le donne) jusqu'à la première réponse réussie.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from types import TracebackType

MAX_PAUSE = 900.0  # un quart d'heure : au-delà, on réessaie quand même
BASE_PAUSE = 5.0


class Backoff:
    """Pause croissante après des échecs successifs : 5 s, 10 s, 20 s… 15 min au plus."""

    def __init__(self, clock: Callable[[], float] = time.monotonic):
        self._clock = clock
        self.failures = 0
        self._until = 0.0

    def failed(self, retry_after: float | None = None) -> float:
        """Enregistre un échec ; retourne la pause décidée (secondes)."""
        self.failures += 1
        pause = BASE_PAUSE * 2 ** min(self.failures - 1, 10)
        if retry_after is not None:
            pause = max(pause if self.failures > 1 else 0.0, retry_after)
        pause = min(pause, MAX_PAUSE)
        self._until = self._clock() + pause
        return pause

    def succeeded(self) -> None:
        self.failures = 0
        self._until = 0.0

    @property
    def remaining(self) -> float:
        return max(0.0, self._until - self._clock())

    @property
    def paused(self) -> bool:
        return self.remaining > 0


class Gate:
    """Au plus `concurrency` requêtes en vol et au moins `interval` secondes entre deux départs."""

    def __init__(
        self,
        *,
        interval: float,
        concurrency: int = 2,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ):
        self._interval = interval
        self._slots = asyncio.Semaphore(concurrency)
        self._clock = clock
        self._sleep = sleep
        self._next_start = 0.0
        self._lock = asyncio.Lock()

    async def __aenter__(self) -> Gate:
        await self._slots.acquire()
        try:
            async with self._lock:  # les départs sont espacés, dans l'ordre d'arrivée
                wait = self._next_start - self._clock()
                if wait > 0:
                    await self._sleep(wait)
                self._next_start = max(self._next_start, self._clock()) + self._interval
        except BaseException:
            self._slots.release()
            raise
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self._slots.release()
