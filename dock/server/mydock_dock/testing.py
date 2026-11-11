"""Aides pour les tests du dock : un vrai serveur dock (uvicorn) sur un port éphémère.

Les tests ont aussi besoin d'un agent : voir `mydock_agent.testing` (jamais importé par le dock
en production).
"""

from __future__ import annotations

import asyncio
import contextlib
import socket
from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import uvicorn
from websockets.asyncio.client import ClientConnection, connect

from mydock_common import protocol as proto
from mydock_common.protocol import MAX_MESSAGE_BYTES
from mydock_dock.agent_link import backoff_delay
from mydock_dock.app import DockServer, create_app
from mydock_dock.config import DockConfig, init_config

TOKEN = "jeton-de-test-" + "x" * 30


@dataclass
class RunningDock:
    port: int
    server: DockServer
    config: DockConfig

    @property
    def origin(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    @property
    def ws_url(self) -> str:
        return f"ws://127.0.0.1:{self.port}/ws"

    @property
    def portal_origin(self) -> str:
        """L'adresse du portail (le serveur du réseau local), quand il tourne."""
        assert self.server.portal.port is not None, "le portail n'est pas démarré"
        return f"http://127.0.0.1:{self.server.portal.port}"

    @property
    def player_url(self) -> str:
        return f"ws://127.0.0.1:{self.port}/ws/player"

    def connect_player(self, *, origin: str | None = None) -> Any:
        """L'assistant de lecture de l'écran, simulé (jamais un navigateur : sans Origin)."""
        headers = {} if origin is None else {"Origin": origin}
        return connect(
            self.player_url,
            additional_headers=headers,
            open_timeout=3,
            ping_interval=None,
            proxy=None,
        )

    def connect(self, *, origin: str | None = "same") -> Any:
        """Navigateur simulé. `origin=None` : sans en-tête Origin ; sinon l'origine à annoncer."""
        headers = {} if origin is None else {"Origin": self.origin if origin == "same" else origin}
        return connect(
            self.ws_url,
            additional_headers=headers,
            open_timeout=3,
            ping_interval=None,
            proxy=None,
            max_size=MAX_MESSAGE_BYTES * 4,
        )


@contextlib.asynccontextmanager
async def running_dock(
    tmp_path: Path,
    *,
    agent_port: int | None = None,
    agent_host: str = "127.0.0.1",
    token: str = TOKEN,
    web_dir: Path | None = None,
    wol_mac: str | None = None,
    fast_retry: bool = True,
    demo_markets: bool = False,
    portal: bool = False,
    clients_dir: Path | None = None,
    **server_options: Any,
) -> AsyncIterator[RunningDock]:
    config_path = tmp_path / "dock.toml"
    config = init_config(
        config_path,
        pc_host=agent_host if agent_port else None,
        pc_port=agent_port,
        token=token,
        state_dir=tmp_path / "state",
        web_dir=web_dir,
    )
    if wol_mac:
        config.set_wol_mac(wol_mac)
    config.demo_markets = demo_markets
    # Le portail écoute le réseau : en test il est éteint, sauf demande, et alors sur un port libre
    # de la boucle locale (jamais le port 80 de la machine).
    config.portal_enabled = portal
    config.clients_dir = clients_dir if clients_dir is not None else tmp_path / "clients"
    server = DockServer(
        config,
        link_backoff=(lambda _attempt: 0.05) if fast_retry else backoff_delay,
        **server_options,
    )
    server.portal.bind_host, server.portal.bind_port = "127.0.0.1", 0
    uvicorn_server = uvicorn.Server(
        uvicorn.Config(
            create_app(server),
            host="127.0.0.1",
            port=0,
            log_config=None,
            log_level="warning",
            access_log=False,
            ws_ping_interval=None,
            ws_max_size=MAX_MESSAGE_BYTES,
        )
    )
    task = asyncio.create_task(uvicorn_server.serve())
    try:
        async with asyncio.timeout(5):
            while not uvicorn_server.started:
                await asyncio.sleep(0.01)
        port = uvicorn_server.servers[0].sockets[0].getsockname()[1]
        yield RunningDock(port, server, config)
    finally:
        uvicorn_server.should_exit = True
        await asyncio.wait_for(task, 10)


async def recv_json(ws: ClientConnection, timeout: float = 3.0) -> dict[str, Any]:
    return proto.loads(await asyncio.wait_for(ws.recv(), timeout))


async def recv_until(ws: ClientConnection, predicate: Any, timeout: float = 5.0) -> dict[str, Any]:
    async with asyncio.timeout(timeout):
        while True:
            message = await recv_json(ws, timeout)
            if predicate(message):
                return message


async def snapshot_of(ws: ClientConnection, topic: str, timeout: float = 5.0) -> dict[str, Any]:
    return await recv_until(ws, lambda m: m.get("t") == "snap" and m.get("topic") == topic, timeout)


async def send_json(ws: ClientConnection, message: dict[str, Any]) -> None:
    await ws.send(proto.dumps(message))


async def call(ws: ClientConnection, act_id: str, name: str, params: dict | None = None, **kw: Any):
    """Envoie une action et attend son « ack »."""
    message: dict[str, Any] = {"t": "act", "id": act_id, "a": name, "p": params or {}, **kw}
    await send_json(ws, message)
    return await recv_until(ws, lambda m: m.get("t") == "ack" and m.get("id") == act_id)


async def eventually(predicate: Any, timeout: float = 5.0, interval: float = 0.02) -> None:
    """Attend qu'une condition devienne vraie (échoue avec TimeoutError sinon)."""
    async with asyncio.timeout(timeout):
        while not predicate():
            await asyncio.sleep(interval)


class UdpSink(asyncio.DatagramProtocol):
    """Écoute UDP locale qui garde les paquets reçus (pour vérifier le Wake-on-LAN)."""

    def __init__(self) -> None:
        self.packets: list[bytes] = []
        self.transport: asyncio.DatagramTransport | None = None

    def connection_made(self, transport: asyncio.BaseTransport) -> None:
        self.transport = transport  # type: ignore[assignment]

    def datagram_received(self, data: bytes, addr: Any) -> None:
        self.packets.append(data)

    @property
    def port(self) -> int:
        assert self.transport is not None
        return self.transport.get_extra_info("sockname")[1]


async def start_udp_sink() -> UdpSink:
    loop = asyncio.get_running_loop()
    _transport, sink = await loop.create_datagram_endpoint(UdpSink, local_addr=("127.0.0.1", 0))
    return sink


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]
