"""Objectifs chiffrés : moins de 2 % de CPU au repos, accusé d'action en quelques millisecondes."""

import asyncio
import socket
import statistics
import subprocess
import sys
import time

import psutil
import pytest
from websockets.asyncio.client import connect

from mydock_agent.demo import DEMO_TOKEN
from mydock_agent.testing import recv_json, running_agent, send_json
from mydock_common.auth import bearer_headers


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


async def test_ack_latency_on_loopback_is_far_below_the_50ms_budget():
    async with running_agent() as agent, agent.connect() as ws:
        await recv_json(ws)
        samples = []
        for i in range(300):
            started = time.perf_counter()
            await send_json(ws, {"t": "act", "id": f"p{i}", "a": "diag.ping"})
            await recv_json(ws)
            samples.append((time.perf_counter() - started) * 1000)
    samples.sort()
    p50, p95 = statistics.median(samples), samples[int(len(samples) * 0.95)]
    print(f"\nlatence agent (boucle locale) : p50 {p50:.2f} ms, p95 {p95:.2f} ms")
    assert p95 < 15, f"p95 = {p95:.1f} ms"


@pytest.mark.slow
async def test_idle_agent_uses_less_than_2_percent_cpu():
    """Vrai processus `mydock-agent run --demo`, un client abonné à « host », 10 s de repos."""
    port = free_port()
    process = subprocess.Popen(  # noqa: ASYNC220 - lancement unique, avant toute mesure
        [sys.executable, "-m", "mydock_agent", "run", "--demo", "--port", str(port)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        async with asyncio.timeout(10):
            while True:
                try:
                    socket.create_connection(("127.0.0.1", port), timeout=0.2).close()
                    break
                except OSError:
                    await asyncio.sleep(0.1)
        async with connect(
            f"ws://127.0.0.1:{port}/ws", additional_headers=bearer_headers(DEMO_TOKEN), proxy=None
        ) as ws:
            await recv_json(ws)
            await send_json(ws, {"t": "sub", "topics": ["host"]})
            await recv_json(ws)
            await asyncio.sleep(2)  # démarrage terminé
            proc = psutil.Process(process.pid)
            before = proc.cpu_times()
            started = time.monotonic()
            await asyncio.sleep(10)
            after = proc.cpu_times()
            elapsed = time.monotonic() - started
            used = (after.user + after.system) - (before.user + before.system)
            percent = used / elapsed * 100
            rss_mb = proc.memory_info().rss / 1e6
        print(f"\nagent au repos : {percent:.2f} % CPU sur {elapsed:.0f} s, {rss_mb:.0f} Mo RSS")
        assert percent < 2.0, f"{percent:.2f} % de CPU au repos"
        assert rss_mb < 150
    finally:
        process.terminate()
        process.wait(timeout=10)
