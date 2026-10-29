"""Serveur WebSocket de l'agent (FastAPI).

Un seul point d'entrée : ``/ws``. Avant même de regarder le message, deux barrières :
  1. l'adresse du client doit être autorisée (sinon refus muet, quel que soit le jeton) ;
  2. l'en-tête ``Authorization: Bearer`` doit porter le bon jeton (échecs comptés par adresse).
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from starlette.types import ASGIApp, Receive, Scope, Send

from mydock_agent import __version__
from mydock_agent.access import AccessGate
from mydock_agent.hub import ClientConnection, Hub
from mydock_common import protocol as proto
from mydock_common.actions import ActionContext, ActionRegistry
from mydock_common.auth import parse_bearer

log = logging.getLogger(__name__)

MAX_INFLIGHT_ACTIONS = 32
MAX_CONSECUTIVE_BAD_MESSAGES = 10
_REJECT_LOG_INTERVAL = 60.0


class GateMiddleware:
    """Refuse en silence les clients hors liste ou bloqués, avant toute autre logique."""

    def __init__(self, app: ASGIApp, gate: AccessGate):
        self.app = app
        self.gate = gate
        self._last_logged: dict[str, float] = {}

    def _note(self, ip: str | None) -> None:
        now = time.monotonic()
        key = str(ip)
        if now - self._last_logged.get(key, -1e9) > _REJECT_LOG_INTERVAL:
            self._last_logged[key] = now
            log.warning("Connexion refusée depuis %s (adresse non autorisée ou bloquée)", key)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] in ("http", "websocket"):
            client = scope.get("client")
            ip = client[0] if client else None
            if not self.gate.client_allowed(ip) or self.gate.is_blocked(ip):
                self._note(ip)
                if scope["type"] == "websocket":
                    await receive()  # websocket.connect
                    await send({"type": "websocket.close", "code": 1008})
                else:
                    await send(
                        {
                            "type": "http.response.start",
                            "status": 403,
                            "headers": [(b"content-length", b"0")],
                        }
                    )
                    await send({"type": "http.response.body", "body": b""})
                return
        await self.app(scope, receive, send)


def create_app(
    *, hub: Hub, registry: ActionRegistry, gate: AccessGate, demo: bool = False
) -> FastAPI:
    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        await hub.start()
        try:
            yield
        finally:
            await hub.stop()

    app = FastAPI(
        title="MyDock agent",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        lifespan=lifespan,
    )
    app.add_middleware(GateMiddleware, gate=gate)

    @app.websocket("/ws")
    async def ws_endpoint(ws: WebSocket) -> None:
        peer = ws.client.host if ws.client else "?"
        if not gate.authenticate(peer, parse_bearer(ws.headers.get("authorization"))):
            await ws.close(code=1008)
            return
        await ws.accept()
        await _serve_client(ws, peer, hub=hub, registry=registry, demo=demo)

    return app


async def _serve_client(
    ws: WebSocket, peer: str, *, hub: Hub, registry: ActionRegistry, demo: bool
) -> None:
    conn = ClientConnection(peer)
    ctx = ActionContext(conn.id, peer)
    inflight: set[asyncio.Task[None]] = set()
    log.info("Client connecté : %s", peer)

    conn.outbox.push_control(
        proto.dumps(
            proto.hello(
                {
                    "version": __version__,
                    "demo": demo,
                    "topics": hub.topics,
                    "actions": registry.names,
                }
            )
        )
    )

    async def run_act(act: proto.Act) -> None:
        conn.outbox.push_control(proto.dumps(await registry.invoke(act, ctx)))

    def handle(msg: dict[str, Any]) -> None:
        kind = msg["t"]
        if kind == "sub":
            hub.subscribe(conn, proto.parse_topics(msg))
        elif kind == "unsub":
            hub.unsubscribe(conn, proto.parse_topics(msg))
        elif kind == "act":
            act = proto.parse_act(msg)
            if len(inflight) >= MAX_INFLIGHT_ACTIONS:
                reply = proto.ack_error(act.id, proto.ErrorCode.FAILED, "Trop d'actions en cours")
                conn.outbox.push_control(proto.dumps(reply))
                return
            task = asyncio.create_task(run_act(act))
            inflight.add(task)
            task.add_done_callback(inflight.discard)
        elif kind == "ping":
            n = msg.get("n")
            n = n if isinstance(n, int | str) and len(str(n)) <= 32 else None
            conn.outbox.push_control(proto.dumps(proto.pong(n, int(time.time() * 1000))))
        else:
            raise proto.ProtocolError(proto.ErrorCode.UNKNOWN_TYPE, "Type de message inconnu")

    async def receive_loop() -> None:
        bad = 0
        while True:
            message = await ws.receive()
            if message["type"] == "websocket.disconnect":
                return
            try:
                raw = message.get("text")
                if raw is None:
                    raise proto.ProtocolError(
                        proto.ErrorCode.BAD_MESSAGE, "Seuls les messages texte sont acceptés"
                    )
                handle(proto.loads(raw))
                bad = 0
            except proto.ProtocolError as exc:
                bad += 1
                conn.outbox.push_control(proto.dumps(proto.err(exc.code, exc.message)))
                if bad >= MAX_CONSECUTIVE_BAD_MESSAGES:
                    log.warning("Trop de messages invalides de %s : déconnexion", peer)
                    return

    writer = asyncio.create_task(conn.outbox.run(ws.send_text))
    reader = asyncio.create_task(receive_loop())
    try:
        done, _ = await asyncio.wait({writer, reader}, return_when=asyncio.FIRST_COMPLETED)
        for task in done:
            exc = task.exception()
            if exc and not isinstance(exc, WebSocketDisconnect | ConnectionError | RuntimeError):
                log.error("Erreur sur la connexion de %s", peer, exc_info=exc)
    finally:
        conn.outbox.close()
        everything = [reader, writer, *inflight]
        for task in everything:
            task.cancel()
        await asyncio.gather(*everything, return_exceptions=True)
        hub.detach(conn)
        try:
            await ws.close()
        except Exception:
            pass
        log.info("Client déconnecté : %s", peer)
