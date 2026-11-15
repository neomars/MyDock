"""Aides pour les tests : un vrai agent (uvicorn) sur un port éphémère, comme en production.

Utilisé par les tests de l'agent, du serveur dock et par la pile des tests de bout en bout.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import shutil
import signal
import struct
import subprocess
import tempfile
import time
import zlib
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass
from typing import Any

import uvicorn
from websockets.asyncio.client import ClientConnection, connect
from websockets.exceptions import ConnectionClosed

from mydock_agent.access import AccessGate
from mydock_agent.audio_in import AudioIn
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

    audio_in: AudioIn | None = None

    @property
    def url(self) -> str:
        return f"ws://127.0.0.1:{self.port}/ws"

    @property
    def audio_url(self) -> str:
        return f"ws://127.0.0.1:{self.port}/audio"

    def connect_audio(self, token: str | None = None, *, headers: dict[str, str] | None = None):
        """`async with agent.connect_audio() as ws` : le flux de son du dock ; `headers={}` pour ne présenter aucun jeton."""
        if headers is None:
            headers = bearer_headers(token or self.token)
        return connect(
            self.audio_url,
            additional_headers=headers,
            open_timeout=3,
            ping_interval=None,
            proxy=None,
            max_size=None,
        )

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
    registry: ActionRegistry | None = None,
    token: str = TOKEN,
    audio_in: AudioIn | None = None,
) -> AsyncIterator[RunningAgent]:
    hub = Hub(providers if providers is not None else [ScriptedProvider("host", lazy=False)])
    if registry is None:
        registry = ActionRegistry()
        register_builtin(registry, demo=demo)
    gate_kwargs: dict[str, Any] = {"max_failures": max_failures, "block": block}
    if clock is not None:
        gate_kwargs["clock"] = clock
    gate = AccessGate(token, (), lan_networks=list, **gate_kwargs)
    app = create_app(hub=hub, registry=registry, gate=gate, demo=demo, audio_in=audio_in)
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
        yield RunningAgent(actual_port, hub, registry, gate, token, audio_in)
    finally:
        server.should_exit = True
        await asyncio.wait_for(task, 5)


def png_bytes(width: int = 2, height: int = 2, *, alpha: bool = False) -> bytes:
    """Un PNG minimal valide, fabriqué à la main (aucune bibliothèque d'image requise)."""
    channels, color_type = (4, 6) if alpha else (3, 2)
    raw = b"".join(b"\x00" + bytes([200, 40, 40, 255][:channels]) * width for _ in range(height))

    def chunk(kind: bytes, data: bytes) -> bytes:
        body = kind + data
        return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body))

    header = struct.pack(">IIBBBBB", width, height, 8, color_type, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", header)
        + chunk(b"IDAT", zlib.compress(raw))
        + chunk(b"IEND", b"")
    )


async def eventually(
    predicate: Callable[[], Any], timeout: float = 3.0, interval: float = 0.01
) -> None:
    """Attend qu'une condition devienne vraie (TimeoutError sinon)."""
    async with asyncio.timeout(timeout):
        while not predicate():
            await asyncio.sleep(interval)


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


# --- un vrai serveur de son, sans carte son --------------------------------------------------------------------------------


@dataclass
class AudioStack:
    """Un PipeWire (avec sa couche PulseAudio) jetable : de vrais puits virtuels, sans matériel.

    Sert à vérifier le son de bout en bout (VLC -> puits -> capture -> agent -> `pacat` -> puits) avec les vrais outils.
    """

    env: dict[str, str]

    def pactl(self, *args: str) -> str:
        result = subprocess.run(
            ["pactl", *args], env=self.env, capture_output=True, text=True, timeout=10, check=True
        )
        return result.stdout

    def new_sink(self, name: str) -> None:
        """Un puits virtuel (`name`) et son moniteur (`name.monitor`), au format du dock."""
        self.pactl(
            "load-module", "module-null-sink", f"sink_name={name}",
            "rate=48000", "channels=2", "format=s16le",
        )  # fmt: skip

    def capture(self, source: str, seconds: float) -> subprocess.Popen[bytes]:
        """Enregistre `source` (brut, s16le, 48 kHz, stéréo) pendant `seconds` ; lire la sortie avec `.communicate()`."""
        return subprocess.Popen(
            ["timeout", str(seconds), "parec", f"--device={source}", "--format=s16le",
             "--rate=48000", "--channels=2", "--raw"],
            env=self.env, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
        )  # fmt: skip


def have_audio_tools() -> bool:
    return all(
        shutil.which(tool)
        for tool in ("pipewire", "wireplumber", "pipewire-pulse", "pactl", "parec", "pacat", "dbus-run-session", "timeout")
    )  # fmt: skip


@contextlib.contextmanager
def audio_stack(*, user_env: dict[str, str] | None = None) -> Any:
    """Démarre PipeWire + WirePlumber + pipewire-pulse dans un dossier d'exécution à soi ; les arrête à la sortie."""
    runtime = tempfile.mkdtemp(
        prefix="mdk-"
    )  # court : le chemin d'une prise Unix est limité à 108 caractères
    os.chmod(runtime, 0o700)
    env = {
        **os.environ,
        **(user_env or {}),
        "XDG_RUNTIME_DIR": runtime,
        "HOME": runtime,
        "LC_ALL": "C",
    }
    env.pop("PULSE_SINK", None)
    env.pop("PULSE_SERVER", None)
    script = "pipewire & sleep 1; wireplumber & sleep 1; pipewire-pulse & wait"
    process = subprocess.Popen(
        ["dbus-run-session", "--", "sh", "-c", script],
        env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True,
    )  # fmt: skip
    stack = AudioStack(env)
    try:
        deadline = time.monotonic() + 15
        while True:
            probe = subprocess.run(
                ["pactl", "info"], env=env, capture_output=True, timeout=5, check=False
            )
            if probe.returncode == 0:
                break
            if time.monotonic() > deadline:
                raise RuntimeError("le serveur de son de test ne démarre pas")
            time.sleep(0.3)
        time.sleep(0.5)  # WirePlumber a fini d'installer ses règles de liaison
        yield stack
    finally:
        with contextlib.suppress(ProcessLookupError):
            os.killpg(process.pid, signal.SIGKILL)
        process.wait()
        shutil.rmtree(runtime, ignore_errors=True)
