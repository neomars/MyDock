"""Lien du dock vers l'agent du PC : connexion, reconnexion automatique, requêtes d'actions.

Quand la connexion échoue, la *façon* dont elle échoue dit où est le problème, et l'écran
« PC hors ligne » l'explique :

    refus immédiat (RST)        -> le PC est allumé mais l'agent n'écoute pas
    silence / pas de route      -> le PC est éteint, en veille, hors réseau ou derrière un pare-feu
    HTTP 403 à la poignée de main -> jeton incorrect ou adresse du Pi non autorisée par l'agent
"""

from __future__ import annotations

import asyncio
import errno
import ipaddress
import itertools
import logging
import random
import socket
import time
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from websockets.asyncio.client import ClientConnection, connect
from websockets.exceptions import ConnectionClosed, InvalidHandshake, InvalidStatus

from mydock_common import protocol as proto
from mydock_common.auth import bearer_headers

log = logging.getLogger(__name__)

BACKOFF_STEPS = (1.0, 2.0, 3.0, 5.0, 8.0, 10.0)
SLOW_RETRY = 30.0
OPEN_TIMEOUT = 4.0
HELLO_TIMEOUT = 5.0
UNREACHABLE_ERRNOS = {
    errno.EHOSTUNREACH,
    errno.ENETUNREACH,
    errno.EHOSTDOWN,
    errno.ETIMEDOUT,
    errno.ENETDOWN,
}


class LinkState(StrEnum):
    NOT_CONFIGURED = "not_configured"
    CONNECTING = "connecting"
    CONNECTED = "connected"
    AUTH_FAILED = "auth_failed"
    HOST_UP_AGENT_DOWN = "host_up_agent_down"
    UNREACHABLE = "unreachable"
    PROTOCOL_MISMATCH = "protocol_mismatch"


@dataclass(frozen=True)
class LinkSettings:
    host: str = ""
    port: int = 8765
    token: str = ""

    @property
    def configured(self) -> bool:
        return bool(self.host and self.token)


@dataclass(frozen=True)
class LinkStatus:
    state: LinkState
    detail: str
    since: float  # epoch (s) du dernier changement d'état
    attempt: int = 0
    next_retry_at: float | None = None
    resolved: str | None = None
    agent: dict[str, Any] | None = None
    host: str = ""
    port: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "state": str(self.state),
            "detail": self.detail,
            "since": int(self.since * 1000),
            "attempt": self.attempt,
            "next_retry_at": int(self.next_retry_at * 1000) if self.next_retry_at else None,
            "resolved": self.resolved,
            "agent": self.agent,
            "host": self.host,
            "port": self.port,
        }


class LinkFailure(Exception):
    def __init__(self, state: LinkState, detail: str):
        super().__init__(detail)
        self.state = state
        self.detail = detail


def backoff_delay(
    attempt: int, *, jitter: float = 0.15, rand: Callable[[], float] = random.random
) -> float:
    """Délai avant la prochaine tentative : 1, 2, 3, 5, 8 puis 10 s, avec un peu de hasard."""
    base = BACKOFF_STEPS[min(max(attempt, 1), len(BACKOFF_STEPS)) - 1]
    return base * (1 + jitter * (2 * rand() - 1))


async def resolve_ipv4(host: str, port: int, timeout: float = 5.0) -> str:
    """Adresse IPv4 du PC (le nom .local passe par mDNS). L'IPv6 est volontairement ignorée."""
    try:
        ipaddress.IPv4Address(host)
        return host
    except ValueError:
        pass
    loop = asyncio.get_running_loop()
    try:
        infos = await asyncio.wait_for(
            loop.getaddrinfo(host, port, family=socket.AF_INET, type=socket.SOCK_STREAM), timeout
        )
    except (TimeoutError, OSError):
        raise LinkFailure(
            LinkState.UNREACHABLE,
            f"Le nom « {host} » est introuvable sur le réseau (PC éteint, ou mDNS indisponible). "
            "Tu peux saisir son adresse IP dans Réglages.",
        ) from None
    return str(infos[0][4][0])


def classify_os_error(exc: BaseException, port: int) -> LinkFailure:
    if isinstance(exc, ConnectionRefusedError):
        return LinkFailure(
            LinkState.HOST_UP_AGENT_DOWN,
            "Le PC répond mais l'agent MyDock n'écoute pas "
            "(session fermée ? agent arrêté ? mauvais port ?).",
        )
    if isinstance(exc, TimeoutError) or (
        isinstance(exc, OSError) and exc.errno in UNREACHABLE_ERRNOS
    ):
        return LinkFailure(
            LinkState.UNREACHABLE,
            "Le PC ne répond pas : éteint, en veille, hors réseau, ou bloqué par un pare-feu.",
        )
    return LinkFailure(LinkState.UNREACHABLE, f"Connexion impossible ({exc.__class__.__name__}).")


async def open_session(
    settings: LinkSettings,
    *,
    open_timeout: float = OPEN_TIMEOUT,
    ping_interval: float | None = 8.0,
) -> tuple[ClientConnection, dict[str, Any], str]:
    """Ouvre la connexion et attend le `hello` ; sinon lève LinkFailure classée."""
    ip = await resolve_ipv4(settings.host, settings.port)
    try:
        ws = await connect(
            f"ws://{ip}:{settings.port}/ws",
            additional_headers=bearer_headers(settings.token),
            open_timeout=open_timeout,
            ping_interval=ping_interval,
            ping_timeout=8.0,
            compression=None,
            proxy=None,
            user_agent_header=None,
        )
    except InvalidStatus as exc:
        if exc.response.status_code == 403:
            raise LinkFailure(
                LinkState.AUTH_FAILED,
                "Connexion refusée : jeton incorrect, ou l'adresse de ce Pi n'est pas "
                "autorisée par l'agent (allowed_clients).",
            ) from None
        raise LinkFailure(
            LinkState.HOST_UP_AGENT_DOWN,
            f"Un service répond sur le port {settings.port} mais ce n'est pas l'agent MyDock "
            f"(HTTP {exc.response.status_code}).",
        ) from None
    except InvalidHandshake:
        raise LinkFailure(
            LinkState.HOST_UP_AGENT_DOWN,
            f"Un service répond sur le port {settings.port} mais ce n'est pas l'agent MyDock.",
        ) from None
    except (OSError, TimeoutError) as exc:
        raise classify_os_error(exc, settings.port) from None

    try:
        hello = proto.loads(await asyncio.wait_for(ws.recv(), HELLO_TIMEOUT))
    except (TimeoutError, ConnectionClosed, proto.ProtocolError):
        await ws.close()
        raise LinkFailure(LinkState.HOST_UP_AGENT_DOWN, "L'agent n'a pas répondu.") from None
    if hello.get("t") != "hello":
        await ws.close()
        raise LinkFailure(LinkState.HOST_UP_AGENT_DOWN, "Réponse inattendue de l'agent.")
    if hello.get("proto") != proto.PROTOCOL_VERSION:
        await ws.close()
        raise LinkFailure(
            LinkState.PROTOCOL_MISMATCH,
            f"Versions incompatibles (protocole {hello.get('proto')} côté agent, "
            f"{proto.PROTOCOL_VERSION} côté dock) : mets l'agent et le dock à jour.",
        )
    return ws, hello, ip


async def probe(settings: LinkSettings) -> tuple[LinkState, str, dict[str, Any] | None]:
    """Essai de connexion ponctuel (bouton « Tester la connexion »)."""
    try:
        ws, hello, _ip = await open_session(settings, ping_interval=None)
    except LinkFailure as failure:
        return failure.state, failure.detail, None
    await ws.close()
    return LinkState.CONNECTED, "Connexion réussie.", hello.get("agent")


class AgentLink:
    def __init__(
        self,
        get_settings: Callable[[], LinkSettings],
        *,
        on_status: Callable[[LinkStatus], None] = lambda _status: None,
        on_snapshot: Callable[[str, int, dict[str, Any]], None] = lambda _t, _r, _d: None,
        clock: Callable[[], float] = time.time,
        backoff: Callable[[int], float] = backoff_delay,
    ):
        self._get_settings = get_settings
        self._on_status = on_status
        self._on_snapshot = on_snapshot
        self._clock = clock
        self._backoff = backoff
        self.status = LinkStatus(LinkState.NOT_CONFIGURED, "", clock())
        self.snapshots: dict[str, dict[str, Any]] = {}
        self._desired: set[str] = set()
        self._subscribed: set[str] = set()
        self._ws: ClientConnection | None = None
        self._pending: dict[str, asyncio.Future[dict[str, Any]]] = {}
        self._reload = asyncio.Event()
        self._ids = itertools.count(1)
        self._background: set[asyncio.Task[Any]] = set()

    # --- état ----------------------------------------------------------------------------

    @property
    def connected(self) -> bool:
        return self.status.state == LinkState.CONNECTED and self._ws is not None

    def _set_status(self, state: LinkState, detail: str = "", **fields: Any) -> None:
        settings = self._get_settings()
        since = self.status.since if state == self.status.state else self._clock()
        status = LinkStatus(state, detail, since, host=settings.host, port=settings.port, **fields)
        if status != self.status:
            self.status = status
            self._on_status(status)

    # --- boucle de connexion -------------------------------------------------------------

    async def run(self) -> None:
        attempt = 0
        while True:
            settings = self._get_settings()
            if not settings.configured:
                self._set_status(
                    LinkState.NOT_CONFIGURED,
                    "Aucun PC n'est configuré : ouvre Réglages pour saisir son adresse et le jeton.",
                )
                await self._wait(None)
                attempt = 0
                continue

            self._set_status(LinkState.CONNECTING, f"Connexion à {settings.host}…", attempt=attempt)
            was_connected = False
            try:
                ws, hello, ip = await open_session(settings)
            except LinkFailure as failure:
                state, detail = failure.state, failure.detail
            else:
                was_connected = True
                self._ws = ws
                self._set_status(
                    LinkState.CONNECTED, "Connecté.", resolved=ip, agent=hello.get("agent")
                )
                log.info("Connecté à l'agent %s (%s)", settings.host, ip)
                state, detail = await self._session(ws)
                self._ws = None
                self._fail_pending()
                log.warning("Connexion à l'agent perdue : %s", detail)

            attempt = 1 if was_connected else attempt + 1
            # Jeton refusé ou versions incompatibles : insister ne sert à rien (et ferait bloquer
            # l'adresse du Pi par l'agent). Corriger les Réglages relance aussitôt la connexion.
            slow = state in (LinkState.AUTH_FAILED, LinkState.PROTOCOL_MISMATCH)
            delay = SLOW_RETRY if slow else self._backoff(attempt)
            self._set_status(state, detail, attempt=attempt, next_retry_at=self._clock() + delay)
            await self._wait(delay)

    async def _wait(self, delay: float | None) -> None:
        try:
            await asyncio.wait_for(self._reload.wait(), delay)
        except TimeoutError:
            pass
        self._reload.clear()

    def reload(self) -> None:
        """Réglages modifiés : reconnecte tout de suite avec les nouveaux paramètres."""
        self._reload.set()
        ws = self._ws
        if ws is not None:
            self._spawn(ws.close())

    def _spawn(self, coro: Any) -> None:
        task = asyncio.get_running_loop().create_task(coro)
        self._background.add(task)
        task.add_done_callback(self._background.discard)

    async def _session(self, ws: ClientConnection) -> tuple[LinkState, str]:
        self._subscribed = set()
        await self._sync_subscriptions()
        try:
            async for raw in ws:
                self._dispatch(raw)
        except ConnectionClosed:
            pass
        return LinkState.UNREACHABLE, "Connexion perdue avec le PC."

    def _dispatch(self, raw: str | bytes) -> None:
        try:
            message = proto.loads(raw)
        except proto.ProtocolError:
            return
        kind = message["t"]
        if kind == "snap":
            topic, rev, data = message.get("topic"), message.get("rev"), message.get("data")
            if isinstance(topic, str) and isinstance(rev, int) and isinstance(data, dict):
                self.snapshots[topic] = {"rev": rev, "data": data}
                self._on_snapshot(topic, rev, data)
        elif kind == "ack":
            future = self._pending.get(str(message.get("id")))
            if future is not None and not future.done():
                future.set_result(message)
        elif kind == "err":
            log.warning("Erreur signalée par l'agent : %s", message.get("msg"))

    def _fail_pending(self) -> None:
        for act_id, future in list(self._pending.items()):
            if not future.done():
                future.set_result(
                    proto.ack_error(act_id, proto.ErrorCode.PC_OFFLINE, "Connexion au PC perdue.")
                )

    # --- abonnements ---------------------------------------------------------------------

    def set_subscriptions(self, topics: set[str]) -> None:
        self._desired = set(topics)
        if self._ws is not None:
            self._spawn(self._sync_subscriptions())

    async def _sync_subscriptions(self) -> None:
        ws = self._ws
        if ws is None:
            return
        add = sorted(self._desired - self._subscribed)
        remove = sorted(self._subscribed - self._desired)
        try:
            if add:
                await ws.send(proto.dumps({"t": "sub", "topics": add}))
                self._subscribed.update(add)
            if remove:
                await ws.send(proto.dumps({"t": "unsub", "topics": remove}))
                self._subscribed.difference_update(remove)
        except ConnectionClosed:
            pass

    # --- actions -------------------------------------------------------------------------

    async def request(
        self,
        name: str,
        params: dict[str, Any] | None = None,
        *,
        confirm: str | None = None,
        timeout: float = 6.0,
    ) -> dict[str, Any]:
        """Envoie une action à l'agent et retourne son « ack » (jamais d'exception)."""
        act_id = f"d{next(self._ids)}"
        ws = self._ws
        if ws is None:
            return proto.ack_error(act_id, proto.ErrorCode.PC_OFFLINE, "Le PC n'est pas connecté.")
        future: asyncio.Future[dict[str, Any]] = asyncio.get_running_loop().create_future()
        self._pending[act_id] = future
        message: dict[str, Any] = {"t": "act", "id": act_id, "a": name, "p": params or {}}
        if confirm:
            message["confirm"] = confirm
        started = time.perf_counter()
        try:
            await ws.send(proto.dumps(message))
            reply = await asyncio.wait_for(future, timeout)
        except TimeoutError:
            return proto.ack_error(
                act_id, proto.ErrorCode.TIMEOUT, "Le PC n'a pas répondu à temps."
            )
        except ConnectionClosed:
            return proto.ack_error(act_id, proto.ErrorCode.PC_OFFLINE, "Connexion au PC perdue.")
        finally:
            self._pending.pop(act_id, None)
        reply["hop"] = {"link_ms": round((time.perf_counter() - started) * 1000, 2)}
        return reply

    async def close(self) -> None:
        ws = self._ws
        if ws is not None:
            await ws.close()
        for task in list(self._background):
            task.cancel()
