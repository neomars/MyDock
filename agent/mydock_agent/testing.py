"""Aides pour les tests : un vrai agent (uvicorn) sur un port éphémère, comme en production.

Utilisé par les tests de l'agent, du serveur dock et par la pile des tests de bout en bout.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass
from typing import Any

import uvicorn
from websockets.asyncio.client import ClientConnection, connect
from websockets.exceptions import ConnectionClosed

from mydock_agent.access import AccessGate
from mydock_agent.builtin_actions import register_builtin
from mydock_agent.hub import Hub, Provider, Publish
from mydock_agent.server import create_app
from mydock_common import protocol as proto
from mydock_common.actions import ActionRegistry
from mydock_common.auth import bearer_headers

TOKEN = "jeton-de-test-" + "x" * 30


class ScriptedProvider(Provider):
    """Fournisseur piloté par le test : compte ses démarrages et arrêts."""

    def __init__(self, topic: str, *, lazy: bool = True, initial: dict[str, Any] | None = None):
        self.topic = topic
        self.lazy = lazy
        self.initial = initial if initial is not None else {"n": 0}
        self.starts = 0
        self.stops = 0
        self._publish: Publish | None = None

    async def run(self, publish: Publish) -> None:
        self.starts += 1
        self._publish = publish
        try:
            await publish(dict(self.initial))
            await asyncio.Event().wait()
        finally:
            self.stops += 1
            self._publish = None

    async def push(self, data: dict[str, Any]) -> bool:
        assert self._publish is not None, "le fournisseur n'est pas démarré"
        return await self._publish(data)


@dataclass
class RunningAgent:
    port: int
    hub: Hub
    registry: ActionRegistry
    gate: AccessGate
    token: str = TOKEN

    @property
    def url(self) -> str:
        return f"ws://127.0.0.1:{self.port}/ws"

    def connect(self, token: str | None = None, *, headers: dict[str, str] | None = None):
        """`async with agent.connect() as ws` ; `headers={}` pour ne présenter aucun jeton."""
        if headers is None:
            headers = bearer_headers(token or self.token)
        return connect(
            self.url, additional_headers=headers, open_timeout=3, ping_interval=None, proxy=None
        )


@contextlib.asynccontextmanager
async def running_agent(
    providers: list[Provider] | None = None,
    *,
    demo: bool = True,
    max_failures: int = 5,
    block: float = 300.0,
    clock: Callable[[], float] | None = None,
    port: int = 0,
) -> AsyncIterator[RunningAgent]:
    hub = Hub(providers if providers is not None else [ScriptedProvider("host", lazy=False)])
    registry = ActionRegistry()
    register_builtin(registry, demo=demo)
    gate_kwargs: dict[str, Any] = {"max_failures": max_failures, "block": block}
    if clock is not None:
        gate_kwargs["clock"] = clock
    gate = AccessGate(TOKEN, (), lan_networks=list, **gate_kwargs)
    app = create_app(hub=hub, registry=registry, gate=gate, demo=demo)
    server = uvicorn.Server(
        uvicorn.Config(
            app,
            host="127.0.0.1",
            port=port,
            log_config=None,
            log_level="warning",
            access_log=False,
            ws_ping_interval=None,
            ws_max_size=proto.MAX_MESSAGE_BYTES,
        )
    )
    task = asyncio.create_task(server.serve())
    try:
        async with asyncio.timeout(5):
            while not server.started:
                await asyncio.sleep(0.01)
        actual_port = server.servers[0].sockets[0].getsockname()[1]
        yield RunningAgent(actual_port, hub, registry, gate)
    finally:
        server.should_exit = True
        await asyncio.wait_for(task, 5)


async def recv_json(ws: ClientConnection, timeout: float = 2.0) -> dict[str, Any]:
    return proto.loads(await asyncio.wait_for(ws.recv(), timeout))


async def recv_until(
    ws: ClientConnection, predicate: Callable[[dict[str, Any]], bool], timeout: float = 2.0
) -> dict[str, Any]:
    async with asyncio.timeout(timeout):
        while True:
            message = await recv_json(ws, timeout)
            if predicate(message):
                return message


async def send_json(ws: ClientConnection, message: dict[str, Any]) -> None:
    await ws.send(proto.dumps(message))


async def wait_closed(ws: ClientConnection, timeout: float = 3.0) -> ConnectionClosed:
    """Lit jusqu'à ce que le serveur ferme la connexion ; retourne l'exception de fermeture."""
    try:
        async with asyncio.timeout(timeout):
            while True:
                await ws.recv()
    except ConnectionClosed as closed:
        return closed
