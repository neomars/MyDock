"""Serveur du dock : sert l'interface, relaie l'agent vers le navigateur, exécute les actions locales.

Le navigateur (Chromium en kiosque) ne parle qu'à ce serveur, sur 127.0.0.1 :
  - il n'a jamais le jeton de l'agent ni son adresse réseau ;
  - quand le PC est éteint, l'interface reste vivante (horloge, bourse, Wake-on-LAN) ;
  - la reconnexion à l'agent est gérée à un seul endroit (agent_link.py).

Les actions « dock.* » sont exécutées ici ; toutes les autres sont transmises telles quelles à
l'agent, qui reste seul juge (liste blanche, paramètres validés, confirmations).
"""

from __future__ import annotations

import asyncio
import itertools
import logging
import socket
import time
from collections.abc import AsyncIterator, Callable, Mapping
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, ConfigDict, Field
from starlette.staticfiles import StaticFiles
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from mydock_common import protocol as proto
from mydock_common.actions import ActionContext, ActionError, ActionRegistry, NoParams
from mydock_common.config import ConfigError
from mydock_common.outbox import Outbox
from mydock_dock import __version__
from mydock_dock.agent_link import AgentLink, LinkSettings, LinkStatus, backoff_delay, probe
from mydock_dock.config import DockConfig
from mydock_dock.store import StateStore
from mydock_dock.validation import ValidationFailure, normalize_mac, validate_host, validate_port
from mydock_dock.wol import send_magic_packet

log = logging.getLogger(__name__)

MAX_INFLIGHT_ACTIONS = 32
MAX_CONSECUTIVE_BAD_MESSAGES = 10
LOOPBACK_NAMES = {"127.0.0.1", "localhost"}
CSP = (
    "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
    "img-src 'self' data: blob:; font-src 'self'; connect-src 'self' ws://127.0.0.1:* ws://localhost:*; "
    "object-src 'none'; base-uri 'none'; frame-ancestors 'none'"
)


# --- paramètres des actions locales ---------------------------------------------------------


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class PcSetParams(_Strict):
    host: str
    port: int = Field(strict=True)


class TokenParams(_Strict):
    token: str


class PcTestParams(_Strict):
    host: str | None = None
    port: int | None = Field(default=None, strict=True)
    token: str | None = None


class WolMacParams(_Strict):
    mac: str | None = None


class UiSetParams(_Strict):
    last_page: str | None = None
    order: list[str] | None = None
    hidden: list[str] | None = None


# --- connexion navigateur -------------------------------------------------------------------


class BrowserConn:
    _ids = itertools.count(1)

    def __init__(self, peer: str):
        self.id = next(self._ids)
        self.peer = peer
        self.topics: set[str] = set()  # thèmes de l'agent demandés par ce navigateur
        self.outbox = Outbox()


# --- Wake-on-LAN : quelle interface du PC viser ----------------------------------------------


def pick_wol_interface(interfaces: list[Mapping[str, Any]]) -> dict[str, str] | None:
    """Interface Ethernet de préférence (seule capable de réveiller un PC éteint), sinon Wi-Fi."""
    candidates = []
    for itf in interfaces:
        try:
            mac = normalize_mac(itf.get("mac"))
        except ValidationFailure:
            continue
        if itf.get("kind") in ("ethernet", "wifi") and isinstance(itf.get("name"), str):
            candidates.append({**itf, "mac": mac})
    candidates.sort(
        key=lambda i: (i["kind"] != "ethernet", not i.get("default"), not i.get("up"), i["name"])
    )
    if not candidates:
        return None
    best = candidates[0]
    return {"mac": best["mac"], "iface": best["name"], "kind": best["kind"]}


# --- le serveur -----------------------------------------------------------------------------


class DockServer:
    def __init__(self, config: DockConfig, *, link_backoff: Callable[[int], float] = backoff_delay):
        self.config = config
        self.store = StateStore(config.state_dir)
        self.browsers: set[BrowserConn] = set()
        self.link = AgentLink(
            self._link_settings,
            on_status=self._on_link_status,
            on_snapshot=self._on_agent_snapshot,
            backoff=link_backoff,
        )
        self.actions = ActionRegistry()
        self._revs = {topic: 0 for topic in proto.DOCK_TOPICS}
        self._link_task: asyncio.Task[None] | None = None
        self._register_actions()

    # --- cycle de vie --------------------------------------------------------------------

    def start(self) -> None:
        self.link.set_subscriptions({"host"})
        self._link_task = asyncio.create_task(self._run_link(), name="agent-link")

    async def _run_link(self) -> None:
        while True:  # la liaison ne doit jamais rester morte à cause d'une erreur inattendue
            try:
                await self.link.run()
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("La liaison avec l'agent a planté ; redémarrage dans 5 s")
                await asyncio.sleep(5)

    async def stop(self) -> None:
        if self._link_task is not None:
            self._link_task.cancel()
            await asyncio.gather(self._link_task, return_exceptions=True)
        await self.link.close()
        for conn in tuple(self.browsers):
            conn.outbox.close()

    def _link_settings(self) -> LinkSettings:
        cfg = self.config
        return LinkSettings(cfg.agent_host, cfg.agent_port, cfg.token)

    # --- thèmes locaux -------------------------------------------------------------------

    def wol_info(self) -> dict[str, Any]:
        pc = self.store.pc
        interfaces = pc.get("interfaces") or []
        if self.config.wol_mac:
            known = next((i for i in interfaces if i["mac"] == self.config.wol_mac), None)
            return {
                "mac": self.config.wol_mac,
                "source": "manual",
                "iface": known["name"] if known else None,
                "kind": known["kind"] if known else None,
                "interfaces": interfaces,
            }
        learned = pc.get("wol")
        if learned:
            return {**learned, "source": "auto", "interfaces": interfaces}
        return {"mac": None, "source": None, "iface": None, "kind": None, "interfaces": interfaces}

    def payload(self, topic: str) -> dict[str, Any]:
        if topic == "link":
            return self.link.status.to_dict()
        if topic == "dock":
            return {
                "version": __version__,
                "proto": proto.PROTOCOL_VERSION,
                "hostname": socket.gethostname(),
                "pc": {
                    "host": self.config.agent_host,
                    "port": self.config.agent_port,
                    "token_set": bool(self.config.token),
                    "name": self.store.pc.get("hostname"),
                },
                "wol": self.wol_info(),
            }
        if topic == "ui":
            return self.store.ui
        raise KeyError(topic)

    def local_text(self, topic: str) -> str:
        return proto.dumps(proto.snap(topic, self._revs[topic], self.payload(topic)))

    def push_local(self, topic: str) -> None:
        self._revs[topic] += 1
        text = self.local_text(topic)
        for conn in self.browsers:
            conn.outbox.push_snapshot(topic, text)

    def _on_link_status(self, _status: LinkStatus) -> None:
        self.push_local("link")

    def _on_agent_snapshot(self, topic: str, rev: int, data: dict[str, Any]) -> None:
        if topic == "host":
            self._learn_host(data)
        text = proto.dumps(proto.snap(topic, rev, data))
        for conn in self.browsers:
            if topic in conn.topics:
                conn.outbox.push_snapshot(topic, text)

    def _learn_host(self, data: dict[str, Any]) -> None:
        """Mémorise l'identité et les adresses MAC du PC : indispensables quand il sera éteint."""
        raw = data.get("interfaces")
        interfaces = []
        for itf in raw if isinstance(raw, list) else []:
            if isinstance(itf, dict):
                try:
                    interfaces.append(
                        {
                            "name": str(itf["name"]),
                            "mac": normalize_mac(itf["mac"]),
                            "kind": str(itf["kind"]),
                        }
                    )
                except (KeyError, ValidationFailure):
                    continue
        hostname = data.get("hostname")
        changed = self.store.update_pc(
            hostname=hostname if isinstance(hostname, str) else None,
            interfaces=interfaces,
            wol=pick_wol_interface(raw if isinstance(raw, list) else []),
            seen_at=int(time.time() * 1000),
        )
        if changed:
            self.push_local("dock")

    # --- abonnements des navigateurs -----------------------------------------------------

    def subscribe(self, conn: BrowserConn, topics: list[str]) -> None:
        for topic in topics:
            if topic in proto.DOCK_TOPICS:
                conn.outbox.push_snapshot(topic, self.local_text(topic))
            elif topic in proto.AGENT_TOPICS:
                conn.topics.add(topic)
                cached = self.link.snapshots.get(topic)
                if cached:
                    conn.outbox.push_snapshot(
                        topic, proto.dumps(proto.snap(topic, cached["rev"], cached["data"]))
                    )
            else:
                raise proto.ProtocolError(proto.ErrorCode.UNKNOWN_TOPIC, f"Thème inconnu : {topic}")
        self._sync_upstream()

    def unsubscribe(self, conn: BrowserConn, topics: list[str]) -> None:
        conn.topics.difference_update(topics)
        self._sync_upstream()

    def _sync_upstream(self) -> None:
        wanted = {"host"}.union(*(c.topics for c in self.browsers))
        self.link.set_subscriptions(wanted)

    def detach(self, conn: BrowserConn) -> None:
        self.browsers.discard(conn)
        self._sync_upstream()

    # --- actions -------------------------------------------------------------------------

    async def run_act(self, conn: BrowserConn, act: proto.Act) -> None:
        if act.name.startswith("dock."):
            reply = await self.actions.invoke(act, ActionContext(conn.id, conn.peer))
        else:
            reply = await self.link.request(act.name, act.params, confirm=act.confirm)
            reply["id"] = act.id
        conn.outbox.push_control(proto.dumps(reply))

    def _register_actions(self) -> None:
        reg = self.actions
        reg.register("dock.ping", self._act_ping)
        reg.register("dock.pc.set", self._act_pc_set, params=PcSetParams)
        reg.register("dock.token.set", self._act_token_set, params=TokenParams)
        reg.register("dock.pc.test", self._act_pc_test, params=PcTestParams, timeout=15.0)
        reg.register("dock.wol.send", self._act_wol_send, timeout=8.0)
        reg.register("dock.wol.set_mac", self._act_wol_set_mac, params=WolMacParams)
        reg.register("dock.ui.set", self._act_ui_set, params=UiSetParams)

    def _persist(self, change: Any) -> None:
        """Exécute une modification de configuration en traduisant les erreurs pour l'écran."""
        try:
            change()
        except ValidationFailure as exc:
            raise ActionError(proto.ErrorCode.BAD_PARAMS, str(exc)) from None
        except (OSError, ConfigError):
            log.exception("Impossible d'enregistrer la configuration")
            raise ActionError(
                proto.ErrorCode.FAILED,
                f"Impossible d'enregistrer {self.config.path} (droits d'écriture ?).",
            ) from None

    async def _act_ping(self, _ctx: ActionContext, _params: NoParams) -> dict[str, int]:
        return {"dock_ms": int(time.time() * 1000)}

    async def _act_pc_set(self, _ctx: ActionContext, params: PcSetParams) -> dict[str, Any]:
        self._persist(lambda: self.config.set_pc(params.host, params.port))
        self.link.reload()
        self.push_local("dock")
        return {"host": self.config.agent_host, "port": self.config.agent_port}

    async def _act_token_set(self, _ctx: ActionContext, params: TokenParams) -> dict[str, bool]:
        self._persist(lambda: self.config.set_token(params.token))
        self.link.reload()
        self.push_local("dock")
        return {"token_set": True}

    async def _act_pc_test(self, _ctx: ActionContext, params: PcTestParams) -> dict[str, Any]:
        try:
            host = validate_host(params.host) if params.host else self.config.agent_host
            port = (
                validate_port(params.port, "Le port de l'agent")
                if params.port is not None
                else self.config.agent_port
            )
        except ValidationFailure as exc:
            raise ActionError(proto.ErrorCode.BAD_PARAMS, str(exc)) from None
        settings = LinkSettings(host, port, params.token or self.config.token)
        if not settings.configured:
            raise ActionError("not_configured", "Renseigne d'abord l'adresse du PC et le jeton.")
        state, detail, agent = await probe(settings)
        return {"state": str(state), "detail": detail, "agent": agent}

    async def _act_wol_send(self, _ctx: ActionContext, _params: NoParams) -> dict[str, Any]:
        info = self.wol_info()
        if not info["mac"]:
            raise ActionError(
                "no_mac",
                "Adresse MAC du PC inconnue : le dock la mémorise dès qu'il s'est connecté une "
                "fois au PC allumé, ou saisis-la dans Réglages.",
            )
        results = await send_magic_packet(info["mac"])
        sent = sum(1 for r in results if r["ok"])
        if sent == 0:
            raise ActionError(
                "wol_failed", "Aucun paquet n'a pu partir : le réseau du Pi est-il actif ?"
            )
        return {
            "mac": info["mac"],
            "kind": info["kind"],
            "sent": sent,
            "failed": len(results) - sent,
        }

    async def _act_wol_set_mac(self, _ctx: ActionContext, params: WolMacParams) -> dict[str, Any]:
        self._persist(lambda: self.config.set_wol_mac(params.mac))
        self.push_local("dock")
        return {"mac": self.config.wol_mac or None}

    async def _act_ui_set(self, _ctx: ActionContext, params: UiSetParams) -> dict[str, Any]:
        patch = params.model_dump(exclude_none=True)
        ui: dict[str, Any] = {}

        def apply() -> None:
            ui.update(self.store.update_ui(patch))

        self._persist(apply)
        self.push_local("ui")
        return ui


# --- HTTP / WebSocket -----------------------------------------------------------------------


def host_allowed(host_header: str | None, config: DockConfig) -> bool:
    """Contre le « DNS rebinding » : en local seul 127.0.0.1 / localhost sont des hôtes valides."""
    if not config.loopback_only:
        return True
    if not host_header:
        return False
    name, sep, port = host_header.rpartition(":")
    hostname = name if sep and port.isdigit() else host_header
    return hostname.lower() in LOOPBACK_NAMES


def origin_allowed(origin: str | None, host_header: str | None) -> bool:
    """L'origine doit être l'interface elle-même : aucun autre site ne pilote le dock."""
    return origin is None or (host_header is not None and origin == f"http://{host_header}")


class SecurityMiddleware:
    def __init__(self, app: ASGIApp, config: DockConfig):
        self.app = app
        self.config = config

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return
        headers = {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope["headers"]}
        host = headers.get("host")
        if not host_allowed(host, self.config) or not origin_allowed(headers.get("origin"), host):
            if scope["type"] == "websocket":
                await receive()
                await send({"type": "websocket.close", "code": 1008})
            else:
                await send(
                    {
                        "type": "http.response.start",
                        "status": 421,
                        "headers": [(b"content-length", b"0")],
                    }
                )
                await send({"type": "http.response.body", "body": b""})
            return

        async def send_with_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                extra = [
                    (b"content-security-policy", CSP.encode()),
                    (b"x-content-type-options", b"nosniff"),
                    (b"referrer-policy", b"no-referrer"),
                ]
                message = {**message, "headers": [*message.get("headers", []), *extra]}
            await send(message)

        await self.app(scope, receive, send_with_headers)


class WebFiles(StaticFiles):
    """Fichiers de l'interface : les ressources hachées se mettent en cache, la page jamais."""

    async def get_response(self, path: str, scope: Scope):  # type: ignore[override]
        response = await super().get_response(path, scope)
        if path.startswith("assets/"):
            response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
        else:
            response.headers["Cache-Control"] = "no-cache"
        return response


MISSING_WEB_PAGE = (
    "<!doctype html><meta charset=utf-8><title>MyDock</title>"
    "<body style='font:20px sans-serif;background:#0b0e14;color:#eef2f8;padding:2em'>"
    "<h1>Interface non installée</h1><p>Le dossier de l'interface est introuvable : "
    "{path}</p><p>Compile-la avec <code>make web</code> puis relance le dock.</p>"
)


def create_app(server: DockServer) -> FastAPI:
    config = server.config

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        server.start()
        try:
            yield
        finally:
            await server.stop()

    app = FastAPI(
        title="MyDock dock", docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan
    )
    app.add_middleware(SecurityMiddleware, config=config)

    @app.get("/api/health")
    async def health() -> JSONResponse:
        return JSONResponse(
            {"ok": True, "version": __version__, "link": str(server.link.status.state)},
            headers={"Cache-Control": "no-store"},
        )

    @app.websocket("/ws")
    async def ws_endpoint(ws: WebSocket) -> None:
        await ws.accept()
        await _serve_browser(server, ws)

    web_dir = config.web_dir
    if web_dir is not None and (Path(web_dir) / "index.html").is_file():
        app.mount("/", WebFiles(directory=str(web_dir), html=True), name="web")
    else:

        @app.get("/", response_class=HTMLResponse)
        async def missing() -> HTMLResponse:
            return HTMLResponse(MISSING_WEB_PAGE.format(path=web_dir), status_code=503)

    return app


async def _serve_browser(server: DockServer, ws: WebSocket) -> None:
    peer = ws.client.host if ws.client else "?"
    conn = BrowserConn(peer)
    inflight: set[asyncio.Task[None]] = set()
    server.browsers.add(conn)
    log.debug("Navigateur connecté (%s)", conn.id)

    conn.outbox.push_control(proto.dumps(proto.hello({"role": "dock", "version": __version__})))
    for topic in proto.DOCK_TOPICS:
        conn.outbox.push_snapshot(topic, server.local_text(topic))

    def handle(msg: dict[str, Any]) -> None:
        kind = msg["t"]
        if kind == "sub":
            server.subscribe(conn, proto.parse_topics(msg))
        elif kind == "unsub":
            server.unsubscribe(conn, proto.parse_topics(msg))
        elif kind == "act":
            act = proto.parse_act(msg)
            if len(inflight) >= MAX_INFLIGHT_ACTIONS:
                reply = proto.ack_error(act.id, proto.ErrorCode.FAILED, "Trop d'actions en cours")
                conn.outbox.push_control(proto.dumps(reply))
                return
            task = asyncio.create_task(server.run_act(conn, act))
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
                    raise proto.ProtocolError(proto.ErrorCode.BAD_MESSAGE, "Texte uniquement")
                handle(proto.loads(raw))
                bad = 0
            except proto.ProtocolError as exc:
                bad += 1
                conn.outbox.push_control(proto.dumps(proto.err(exc.code, exc.message)))
                if bad >= MAX_CONSECUTIVE_BAD_MESSAGES:
                    return

    writer = asyncio.create_task(conn.outbox.run(ws.send_text))
    reader = asyncio.create_task(receive_loop())
    try:
        done, _ = await asyncio.wait({writer, reader}, return_when=asyncio.FIRST_COMPLETED)
        for task in done:
            exc = task.exception()
            if exc and not isinstance(exc, WebSocketDisconnect | ConnectionError | RuntimeError):
                log.error("Erreur sur la connexion navigateur", exc_info=exc)
    finally:
        conn.outbox.close()
        everything = [reader, writer, *inflight]
        for task in everything:
            task.cancel()
        await asyncio.gather(*everything, return_exceptions=True)
        server.detach(conn)
        try:
            await ws.close()
        except Exception:
            pass
