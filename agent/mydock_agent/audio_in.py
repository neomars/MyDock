"""Le son du dock sur le PC : le Raspberry Pi n'a pas d'enceintes, son lecteur (VLC, MAME) envoie son son ici.

Le dock ouvre `/audio` (un WebSocket, sur le même port que `/ws`, avec le même jeton et la même liste d'adresses autorisées :
rien de plus n'est ouvert sur le réseau), annonce le format, puis envoie des trames de son brut. L'agent les joue sur la
sortie par défaut du PC avec `pacat`, le lecteur brut de PulseAudio et de PipeWire.

Ce que le dock peut faire par là est volontairement minuscule :

  * **jamais une commande** : `pacat` est lancé avec une ligne de commande figée, dont rien ne vient du dock ;
  * **un seul format**, annoncé puis vérifié à l'octet près (PCM 16 bits, 48 kHz, stéréo) : tout autre est refusé ;
  * **un flux à la fois** : un nouveau flux remplace l'ancien (le dock qui se reconnecte ne reste pas bloqué dehors) ;
  * **un retard borné** : si le PC lit moins vite que le Pi n'envoie (horloges qui dérivent, PC occupé), les trames en trop sont
    jetées plutôt qu'accumulées, au prix d'un petit craquement ; le son ne prend jamais de retard sur l'image ;
  * **rien qui dure** : un flux sans trame pendant dix secondes est fermé, et `pacat` avec lui.

Le volume est celui du PC : le flux apparaît dans le thème « audio » comme une application (« MyDock »), avec son propre
curseur.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import shutil
import time
from dataclasses import dataclass
from typing import Any, Protocol

from fastapi import WebSocket

from mydock_common.audio import CHANNELS, FORMAT, HELLO, MAX_CHUNK_BYTES, RATE, SAMPLE_BYTES

log = logging.getLogger(__name__)

LATENCY_MS = 80  # ce que `pacat` garde d'avance avant de jouer
MAX_BACKLOG_MS = 250  # au-delà, on jette les trames qui arrivent
MAX_BACKLOG_BYTES = RATE * SAMPLE_BYTES * MAX_BACKLOG_MS // 1000
IDLE_SECONDS = 10.0
HELLO_TIMEOUT = 5.0
TAKEOVER_TIMEOUT = 3.0
STDERR_LINES = 6
STREAM_NAME = "MyDock - son du Raspberry Pi"

#: La ligne de commande du lecteur, figée : rien ne vient du dock.
PACAT_ARGS = (
    "--playback",
    "--raw",
    f"--format={FORMAT}",
    f"--rate={RATE}",
    f"--channels={CHANNELS}",
    f"--latency-msec={LATENCY_MS}",
    "--client-name=MyDock",
    f"--stream-name={STREAM_NAME}",
)


class AudioUnavailable(Exception):
    """Le PC ne peut pas jouer le son (pas de lecteur, pas de serveur de son) ; le message est affichable sur le dock."""


class Sink(Protocol):
    """Là où le son est joué : `pacat` en vrai, un compteur en démonstration, un faux dans les tests."""

    async def start(self) -> None: ...
    def backlog(self) -> int: ...
    def write(self, data: bytes) -> None: ...
    @property
    def alive(self) -> bool: ...
    async def stop(self) -> None: ...
    def failure(self) -> str: ...


class PacatSink:
    """Le son joué par `pacat` : PulseAudio, ou PipeWire par sa couche de compatibilité (Ubuntu)."""

    def __init__(self, *, env: dict[str, str] | None = None) -> None:
        self._env = env
        self._process: asyncio.subprocess.Process | None = None
        self._stderr: asyncio.Task[None] | None = None
        self._lines: list[str] = []

    async def start(self) -> None:
        program = shutil.which("pacat")
        if program is None:
            raise AudioUnavailable(
                "pacat est introuvable sur le PC (sudo apt install pulseaudio-utils)."
            )
        env = {**(self._env if self._env is not None else os.environ), "LC_ALL": "C"}
        try:
            self._process = await asyncio.create_subprocess_exec(
                program,
                *PACAT_ARGS,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.PIPE,
                env=env,
            )
        except OSError as exc:
            raise AudioUnavailable(f"pacat ne démarre pas : {exc.strerror or exc}.") from None
        self._stderr = asyncio.create_task(self._read_stderr())
        # Un serveur de son absent se voit tout de suite : pacat s'arrête en disant pourquoi.
        await asyncio.sleep(0.25)
        if not self.alive:
            raise AudioUnavailable(self.failure() or "Le serveur de son du PC ne répond pas.")

    async def _read_stderr(self) -> None:
        assert self._process is not None
        stream = self._process.stderr
        assert stream is not None
        while line := await stream.readline():
            self._lines = [*self._lines, line.decode("utf-8", "replace").strip()][-STDERR_LINES:]

    def failure(self) -> str:
        """Ce que `pacat` a dit en s'arrêtant (une phrase), ou rien."""
        return " ".join(part for part in self._lines if part)[:200]

    def backlog(self) -> int:
        process = self._process
        if process is None or process.stdin is None:
            return 0
        transport = process.stdin.transport
        return int(transport.get_write_buffer_size()) if transport is not None else 0

    def write(self, data: bytes) -> None:
        assert self._process is not None
        assert self._process.stdin is not None
        self._process.stdin.write(data)

    @property
    def alive(self) -> bool:
        return self._process is not None and self._process.returncode is None

    async def stop(self) -> None:
        process, self._process = self._process, None
        if process is None:
            return
        with contextlib.suppress(Exception):
            if process.stdin is not None:
                process.stdin.close()
        try:
            await asyncio.wait_for(process.wait(), 1.0)
        except TimeoutError:
            with contextlib.suppress(ProcessLookupError):
                process.kill()
            await process.wait()
        if self._stderr is not None:
            self._stderr.cancel()
            await asyncio.gather(self._stderr, return_exceptions=True)


class DemoSink:
    """En démonstration, le son est compté et jeté : le PC de la démo ne joue rien."""

    def __init__(self) -> None:
        self.bytes = 0
        self._alive = False

    async def start(self) -> None:
        self._alive = True

    def backlog(self) -> int:
        return 0

    def write(self, data: bytes) -> None:
        self.bytes += len(data)

    @property
    def alive(self) -> bool:
        return self._alive

    async def stop(self) -> None:
        self._alive = False

    def failure(self) -> str:
        return ""


@dataclass
class _Stream:
    stop: asyncio.Event
    done: asyncio.Event


@dataclass
class _Stats:
    began: float
    chunks: int = 0
    bytes: int = 0
    dropped: int = 0


def _error(code: str, message: str) -> str:
    return json.dumps({"t": "err", "code": code, "msg": message}, separators=(",", ":"))


class AudioIn:
    """Reçoit le flux du dock et le joue. Un seul flux à la fois."""

    def __init__(
        self,
        sink_factory: Any = PacatSink,
        *,
        idle: float = IDLE_SECONDS,
        hello_timeout: float = HELLO_TIMEOUT,
        max_backlog: int = MAX_BACKLOG_BYTES,
    ) -> None:
        self._factory = sink_factory
        self._idle, self._hello_timeout, self._max_backlog = idle, hello_timeout, max_backlog
        self._stream: _Stream | None = None
        self.streams = 0  # combien de flux ont été joués (pour les tests et le journal)

    @staticmethod
    def default_sink() -> Sink:
        return PacatSink()

    @property
    def active(self) -> bool:
        return self._stream is not None

    async def serve(self, ws: WebSocket, peer: str) -> None:
        """Un flux, de sa première trame à sa fin. L'appelant a déjà accepté la connexion (jeton vérifié)."""
        stream = _Stream(asyncio.Event(), asyncio.Event())
        old, self._stream = self._stream, stream
        if (
            old is not None
        ):  # le dock qui revient remplace son ancienne connexion, qui n'a peut-être pas encore vu qu'elle est morte
            old.stop.set()
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(old.done.wait(), TAKEOVER_TIMEOUT)
        try:
            await self._run(ws, peer, stream)
        finally:
            stream.done.set()
            if self._stream is stream:
                self._stream = None

    async def _close(self, ws: WebSocket, code: int) -> None:
        with contextlib.suppress(Exception):
            await ws.close(code=code)

    async def _run(self, ws: WebSocket, peer: str, stream: _Stream) -> None:
        # 1. l'annonce : exactement le seul format qu'on sait jouer
        try:
            first = await asyncio.wait_for(ws.receive(), self._hello_timeout)
            raw = first.get("text") if first.get("type") == "websocket.receive" else None
            announced = json.loads(raw) if isinstance(raw, str) and len(raw) <= 512 else None
        except (TimeoutError, ValueError):
            announced = None
        if announced != HELLO:
            with contextlib.suppress(Exception):
                await ws.send_text(_error("bad_format", "Format de son non pris en charge."))
            await self._close(ws, 1008)
            log.warning("Flux audio de %s refusé : annonce absente ou non prise en charge", peer)
            return

        # 2. le lecteur
        sink: Sink = self._factory()
        try:
            await sink.start()
        except AudioUnavailable as exc:
            with contextlib.suppress(Exception):
                await ws.send_text(_error("unavailable", str(exc)))
            await self._close(ws, 1011)
            log.warning("Flux audio de %s impossible : %s", peer, exc)
            await sink.stop()
            return
        self.streams += 1
        stats = _Stats(time.monotonic())
        log.info("Flux audio de %s : démarré", peer)
        reason = "fin du flux"
        try:
            await ws.send_text(
                json.dumps({"t": "ready", "latency_ms": LATENCY_MS}, separators=(",", ":"))
            )
            reason = await self._pump(ws, stream, sink, stats)
        except Exception as exc:
            reason = f"erreur : {type(exc).__name__}"
        finally:
            await sink.stop()
            await self._close(ws, 1000)
            log.info(
                "Flux audio de %s : %s (%.0f s, %d Ko, %d trames jetées)",
                peer, reason, time.monotonic() - stats.began, stats.bytes // 1024, stats.dropped,
            )  # fmt: skip

    async def _pump(self, ws: WebSocket, stream: _Stream, sink: Sink, stats: _Stats) -> str:
        stopped = asyncio.create_task(stream.stop.wait())
        try:
            while True:
                receiving = asyncio.create_task(ws.receive())
                done, _ = await asyncio.wait(
                    {receiving, stopped}, timeout=self._idle, return_when=asyncio.FIRST_COMPLETED
                )
                if not done:
                    receiving.cancel()
                    await asyncio.gather(receiving, return_exceptions=True)
                    return f"aucune trame depuis {self._idle:.0f} s"
                if stopped in done:
                    receiving.cancel()
                    await asyncio.gather(receiving, return_exceptions=True)
                    return "remplacé par un nouveau flux"
                message = receiving.result()
                if message["type"] == "websocket.disconnect":
                    return "le dock a coupé"
                data = message.get("bytes")
                if data is None:
                    continue  # un message texte après l'annonce n'a aucun effet
                if len(data) > MAX_CHUNK_BYTES or len(data) % SAMPLE_BYTES:
                    return "trame invalide"
                if not sink.alive:
                    return sink.failure() or "le lecteur de son s'est arrêté"
                if sink.backlog() > self._max_backlog:
                    stats.dropped += (
                        1  # le PC est en retard : mieux vaut un craquement qu'un son décalé
                    )
                    continue
                sink.write(data)
                stats.chunks += 1
                stats.bytes += len(data)
        finally:
            stopped.cancel()
            await asyncio.gather(stopped, return_exceptions=True)
