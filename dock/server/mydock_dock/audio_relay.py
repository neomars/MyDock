"""Le son du Pi vers le PC : le dock relaie à l'agent le son que l'assistant de l'écran capture.

Le Raspberry Pi n'a pas d'enceintes. Pendant qu'une vidéo, une chaîne ou un jeu joue, l'assistant de l'écran (player_session.py)
lit le son de VLC ou de MAME dans un puits virtuel et le passe au dock par `/ws/player` (en local, jamais sur le réseau). Ce
module le relaie au PC, par une seconde connexion à l'agent (`/audio`, avec le jeton du dock : l'assistant, lui, ne le
connaît pas, voir mydock_common/audio.py pour le format).

Ce que le relais garantit :

  * **le son ne prend pas de retard** : si le PC ou le réseau ne suit pas, les trames les plus anciennes sont jetées (un petit
    craquement) plutôt qu'accumulées (un son décalé de plus en plus). Sans PC, rien n'est gardé ;
  * **il dit pourquoi on n'entend rien** : PC hors ligne, agent trop ancien, pas de serveur de son sur le PC… l'état est
    publié avec le lecteur et s'affiche sur l'écran ;
  * **il se reconnecte seul** (le PC se réveille, le réseau revient) tant que quelque chose joue, et se ferme dès que ça s'arrête.
"""

from __future__ import annotations

import asyncio
import collections
import contextlib
import json
import logging
import time
from collections.abc import Callable
from enum import StrEnum
from typing import Any

from websockets.asyncio.client import ClientConnection, connect
from websockets.exceptions import ConnectionClosed, InvalidHandshake, InvalidStatus

from mydock_common.audio import HELLO, MAX_CHUNK_BYTES, RATE, SAMPLE_BYTES
from mydock_common.auth import bearer_headers
from mydock_dock.agent_link import AgentLink

media_log = logging.getLogger("mydock_dock.media")  # dock.log

OPEN_TIMEOUT = 4.0
READY_TIMEOUT = 5.0
MAX_QUEUE_MS = (
    300  # ce qu'on garde d'avance au plus : au-delà, les plus anciennes trames sont jetées
)
MAX_QUEUE_BYTES = RATE * SAMPLE_BYTES * MAX_QUEUE_MS // 1000
RETRY_SECONDS = (1.0, 2.0, 3.0, 5.0)
POLL_SECONDS = 1.0
FLUSH_SECONDS = 0.5  # à l'arrêt, le temps de finir d'envoyer ce qui reste
KEEPALIVE_SECONDS = 3.0  # sans trame (une vidéo en pause ne fait aucun son) : un message de maintien, pour que l'agent garde le flux ouvert
KEEPALIVE = '{"t":"keepalive"}'


class RelayState(StrEnum):
    IDLE = "idle"  # rien ne joue : rien à envoyer
    CONNECTING = "connecting"
    STREAMING = "streaming"  # le PC joue le son
    OFFLINE = "offline"  # le PC est hors ligne : on n'entend rien
    OLD = "old"  # l'agent du PC ne sait pas recevoir le son (trop ancien)
    ERROR = "error"  # le PC refuse ou ne peut pas jouer (pas de serveur de son…)


class AudioRelay:
    def __init__(
        self,
        link: AgentLink,
        *,
        on_change: Callable[[], None] = lambda: None,
        open_connection: Callable[..., Any] = connect,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._link = link
        self._on_change = on_change
        self._connect = open_connection
        self._clock = clock
        self.state = RelayState.IDLE
        self.detail = ""
        self.capturing = False
        self._queue: collections.deque[bytes] = collections.deque()
        self._queued = 0
        self._frames = asyncio.Event()
        self._wake = asyncio.Event()
        self._task: asyncio.Task[None] | None = None
        self.sent = 0  # octets envoyés au PC pendant ce son
        self.dropped = 0  # trames jetées pendant ce son

    # --- ce que voit l'écran ------------------------------------------------------------------------------

    def snapshot(self) -> dict[str, str]:
        return {"state": str(self.state), "detail": self.detail}

    def _set(self, state: RelayState, detail: str = "") -> None:
        if (state, detail) != (self.state, self.detail):
            self.state, self.detail = state, detail
            self._on_change()

    # --- l'assistant dit : je capture / j'ai fini ----------------------------------------------------------

    def begin(self) -> None:
        """Un son va arriver (l'assistant capture le puits virtuel)."""
        self.capturing = True
        self.sent = self.dropped = 0
        self._wake.set()
        if self._task is None or self._task.done():
            self._set(RelayState.CONNECTING)
            self._task = asyncio.get_running_loop().create_task(self._run(), name="audio-relay")

    def end(self) -> None:
        """Plus de son : le flux se ferme après avoir fini d'envoyer ce qui reste."""
        if not self.capturing:
            return
        self.capturing = False
        self._frames.set()
        self._wake.set()
        if self._task is None or self._task.done():
            self._set(RelayState.IDLE)

    def feed(self, frame: bytes) -> None:
        """Une trame de son brut de l'assistant. Jamais bloquant : trop de retard, on jette le plus ancien."""
        if (
            not self.capturing
            or self.state != RelayState.STREAMING
            or not frame
            or len(frame) > MAX_CHUNK_BYTES
            or len(frame) % SAMPLE_BYTES
        ):
            return
        self._queue.append(frame)
        self._queued += len(frame)
        while self._queued > MAX_QUEUE_BYTES and len(self._queue) > 1:
            self._queued -= len(self._queue.popleft())
            self.dropped += 1
        self._frames.set()

    async def aclose(self) -> None:
        self.capturing = False
        self._frames.set()
        self._wake.set()
        task, self._task = self._task, None
        if task is not None:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        self._set(RelayState.IDLE)

    # --- la connexion au PC ---------------------------------------------------------------------------------

    async def _sleep(self, seconds: float) -> None:
        """Dort jusqu'à ce que `seconds` passent, ou qu'on soit réveillé (fin du son)."""
        self._wake.clear()
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(self._wake.wait(), seconds)

    def _clear(self) -> None:
        self._queue.clear()
        self._queued = 0
        self._frames.clear()

    async def _run(self) -> None:
        attempt = 0
        try:
            while self.capturing:
                if not self._link.connected:
                    self._clear()
                    self._set(RelayState.OFFLINE, "Le PC est hors ligne : pas de son.")
                    await self._sleep(POLL_SECONDS)
                    continue
                agent = self._link.status.agent or {}
                if not agent.get("audio_in"):
                    self._set(
                        RelayState.OLD,
                        "L'agent du PC est trop ancien pour recevoir le son : mets-le à jour (paquet mydock-agent).",
                    )
                    await self._sleep(2.0)
                    continue
                healthy = await self._stream()
                attempt = 0 if healthy else attempt + 1
                if self.capturing:
                    await self._sleep(RETRY_SECONDS[min(attempt, len(RETRY_SECONDS) - 1)])
        finally:
            self._clear()
            if self.state != RelayState.ERROR or not self.capturing:
                self._set(RelayState.IDLE)
            if self.sent or self.dropped:
                media_log.info(
                    "Son vers le PC : %d Ko envoyés, %d trames jetées (le PC ne suivait pas)",
                    self.sent // 1024, self.dropped,
                )  # fmt: skip

    async def _stream(self) -> bool:
        """Un flux, de l'ouverture à la fermeture. Vrai s'il a joué avant de se terminer."""
        settings = self._link.settings
        address = self._link.status.resolved
        if not address:
            return False
        self._set(RelayState.CONNECTING)
        try:
            ws: ClientConnection = await asyncio.wait_for(
                self._connect(
                    f"ws://{address}:{settings.port}/audio",
                    additional_headers=bearer_headers(settings.token),
                    open_timeout=OPEN_TIMEOUT,
                    ping_interval=None,
                    compression=None,
                    proxy=None,
                    user_agent_header=None,
                    max_size=4096,
                ),
                OPEN_TIMEOUT + 1,
            )
        except InvalidStatus as exc:
            self._set(
                RelayState.ERROR,
                f"Le PC a refusé le son (HTTP {exc.response.status_code}) : agent trop ancien, ou jeton refusé.",
            )
            return False
        except (OSError, TimeoutError, InvalidHandshake):
            self._set(RelayState.OFFLINE, "Le PC ne répond pas : pas de son.")
            return False
        played = False
        try:
            await ws.send(json.dumps(HELLO, separators=(",", ":")))
            answer = json.loads(await asyncio.wait_for(ws.recv(), READY_TIMEOUT))
            if not isinstance(answer, dict) or answer.get("t") != "ready":
                message = answer.get("msg") if isinstance(answer, dict) else None
                self._set(
                    RelayState.ERROR,
                    str(message or "Le PC ne peut pas jouer le son.")[:300],
                )
                media_log.warning("Son vers le PC refusé : %s", self.detail)
                return False
            self._clear()
            self._set(RelayState.STREAMING)
            played = await self._send_loop(ws)
        except (ConnectionClosed, TimeoutError, ValueError, OSError):
            if self.capturing:
                self._set(RelayState.OFFLINE, "La connexion au PC s'est coupée : pas de son.")
        finally:
            with contextlib.suppress(Exception):
                await ws.close()
        return played

    async def _send_loop(self, ws: ClientConnection) -> bool:
        closed = asyncio.create_task(ws.wait_closed())
        try:
            deadline: float | None = None
            while True:
                if not self._queue:
                    if not self.capturing:
                        return True
                    self._frames.clear()
                    waiter = asyncio.create_task(self._frames.wait())
                    done, _ = await asyncio.wait(
                        {waiter, closed},
                        return_when=asyncio.FIRST_COMPLETED,
                        timeout=KEEPALIVE_SECONDS,
                    )
                    waiter.cancel()
                    await asyncio.gather(waiter, return_exceptions=True)
                    if closed in done:
                        raise ConnectionClosed(None, None)
                    if not done:  # une pause : le flux reste ouvert sans son (l'agent ferme un flux muet au bout de dix secondes)
                        await ws.send(KEEPALIVE)
                    continue
                if not self.capturing:
                    deadline = deadline or self._clock() + FLUSH_SECONDS
                    if self._clock() > deadline:
                        return True
                frame = self._queue.popleft()
                self._queued -= len(frame)
                await ws.send(frame)
                self.sent += len(frame)
        finally:
            closed.cancel()
            await asyncio.gather(closed, return_exceptions=True)
