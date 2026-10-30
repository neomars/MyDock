import asyncio

import pytest

from mydock_dock.markets.limits import MAX_PAUSE, Backoff, Gate


class Clock:
    def __init__(self, now: float = 100.0):
        self.now = now

    def __call__(self) -> float:
        return self.now


def test_the_pause_doubles_after_each_failure_and_is_capped():
    clock = Clock()
    backoff = Backoff(clock)
    assert not backoff.paused
    pauses = [backoff.failed() for _ in range(12)]
    assert pauses[:5] == [5, 10, 20, 40, 80]
    assert pauses[-1] == MAX_PAUSE
    assert backoff.paused
    assert backoff.remaining == MAX_PAUSE
    clock.now += MAX_PAUSE + 1
    assert not backoff.paused
    assert backoff.remaining == 0


def test_a_retry_after_from_the_provider_is_obeyed():
    backoff = Backoff(Clock())
    assert backoff.failed(retry_after=42) == 42  # la première fois, le fournisseur décide
    assert backoff.failed(retry_after=3) == 10  # ensuite, jamais moins que notre propre pause
    assert backoff.failed(retry_after=90) == 90
    assert backoff.failed(retry_after=100_000) == MAX_PAUSE


def test_a_success_forgets_the_failures():
    backoff = Backoff(Clock())
    backoff.failed()
    backoff.failed()
    backoff.succeeded()
    assert backoff.failures == 0
    assert not backoff.paused
    assert backoff.failed() == 5


async def test_the_gate_spaces_the_starts_in_arrival_order():
    clock = Clock(0.0)
    slept: list[float] = []

    async def sleep(seconds: float) -> None:
        slept.append(round(seconds, 3))
        clock.now += seconds

    gate = Gate(interval=0.5, concurrency=5, clock=clock, sleep=sleep)
    starts: list[float] = []

    async def worker() -> None:
        async with gate:
            starts.append(clock.now)

    await asyncio.gather(*(worker() for _ in range(4)))
    assert starts == [0.0, 0.5, 1.0, 1.5]
    assert slept == [0.5, 0.5, 0.5]


async def test_the_gate_bounds_the_requests_in_flight():
    gate = Gate(interval=0, concurrency=2)
    running = 0
    peak = 0

    async def worker() -> None:
        nonlocal running, peak
        async with gate:
            running += 1
            peak = max(peak, running)
            await asyncio.sleep(0.01)
            running -= 1

    await asyncio.gather(*(worker() for _ in range(8)))
    assert peak == 2


async def test_a_cancelled_waiter_gives_its_slot_back():
    gate = Gate(interval=0, concurrency=1)
    async with gate:
        waiting = asyncio.create_task(gate.__aenter__())
        await asyncio.sleep(0.01)
        waiting.cancel()
        with pytest.raises(asyncio.CancelledError):
            await waiting
    async with asyncio.timeout(1):  # la place est libre : on n'attend pas indéfiniment
        async with gate:
            pass
