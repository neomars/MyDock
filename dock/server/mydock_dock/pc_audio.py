"""Le son du lecteur, capturé sur le Pi pour sortir sur le PC : la part de l'assistant de l'écran (player_session.py).

Le Raspberry Pi n'a pas d'enceintes. VLC (vidéos, chaînes) et MAME (jeux) jouent donc dans un **puits virtuel** (`mydock_pc`, un
« null sink » de PipeWire ou de PulseAudio, qui n'a pas de matériel derrière), et ce module en lit le son avec `parec`, brut
(PCM 16 bits, 48 kHz, stéréo), pour le passer au dock par `/ws/player` (en local) : le dock le relaie à l'agent du PC, qui le joue
(audio_relay.py, puis agent/mydock_agent/audio_in.py). Le jeton de l'agent n'est jamais ici : l'assistant ne lit aucun secret.

Pour faire jouer VLC et MAME dans ce puits sans toucher à leur configuration :

  * leur environnement porte `PULSE_SINK` et `PIPEWIRE_NODE` (la cible des flux sans cible explicite, pour PulseAudio et pour
    PipeWire : SDL et VLC les respectent) ;
  * le puits est aussi la sortie par défaut le temps de la lecture, pour les programmes qui passent par ALSA ; la sortie d'avant
    est rétablie à la fin.

La vidéo avance son son (`--audio-desync` négatif, réglable) : le trajet jusqu'au PC (capture, réseau, lecteur du PC) le retarde
d'un dixième de seconde environ, et l'image ne doit pas arriver avant lui.

Rien n'est capturé hors d'une lecture, et rien ne sort si le réglage « son sur le PC » est coupé. Quand le Pi n'a pas de serveur
de son, la lecture continue (muette) et l'écran dit pourquoi, avec la commande qui répare.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import shutil
from collections.abc import Awaitable, Callable
from typing import Any

from mydock_common.audio import CHANNELS, FORMAT, RATE, SAMPLE_BYTES

log = logging.getLogger("mydock.player")

SINK = "mydock_pc"
COMMAND_TIMEOUT = 6.0
CAPTURE_LATENCY_MS = 20
READ_BYTES = SAMPLE_BYTES * 1920  # 40 ms au plus par lecture
DEFAULT_ADVANCE_MS = 150
FIX = "sudo apt install pipewire-pulse wireplumber pulseaudio-utils, puis redémarre le Pi"


class SoundProblem(Exception):
    """Le son du Pi ne peut pas être capturé ; le message est affichable à l'écran."""


class PcSound:
    """Le son de la lecture en cours, de VLC ou de MAME jusqu'au dock."""

    def __init__(
        self,
        env: dict[str, str],
        *,
        emit: Callable[[dict[str, Any]], Awaitable[Any]],
        send_frame: Callable[[bytes], Awaitable[Any]],
        event: Callable[[str], None] = lambda _text: None,
        which: Callable[[str], str | None] = shutil.which,
    ) -> None:
        self._env = env
        self._emit = emit
        self._send_frame = send_frame
        self._event = event
        self._which = which
        self.enabled = True  # « son sur le PC » (réglage du dock)
        self.advance_ms = DEFAULT_ADVANCE_MS
        self.active = False
        self.bytes = 0
        self._capture: asyncio.subprocess.Process | None = None
        self._pump_task: asyncio.Task[None] | None = None
        self._previous_default: str | None = None

    # --- ce que la lecture doit savoir -----------------------------------------------------------------

    def route_env(self) -> dict[str, str]:
        """À ajouter à l'environnement de VLC et de MAME pour qu'ils jouent dans le puits."""
        return {"PULSE_SINK": SINK, "PIPEWIRE_NODE": SINK}

    def vlc_args(self) -> list[str]:
        """Les vidéos avancent leur son de ce que coûte le trajet jusqu'au PC (VLC accepte un décalage négatif)."""
        return [f"--audio-desync=-{self.advance_ms}"] if self.advance_ms > 0 else []

    # --- le cycle d'une lecture ------------------------------------------------------------------------

    async def begin(self) -> bool:
        """Prépare le puits et lance la capture. Vrai si le son sera envoyé au PC ; faux (et le dock en est informé) sinon."""
        if not self.enabled:
            return False
        if self.active:
            await self.end()
        try:
            await self._prepare()
            await self._start_capture()
        except SoundProblem as problem:
            await self._abandon()
            self._event(f"son : impossible — {problem}")
            await self._emit({"t": "audio", "state": "unavailable", "reason": str(problem)})
            return False
        self.active = True
        self.bytes = 0
        self._event(
            f"son : capture de {SINK} démarrée (avance du son des vidéos : {self.advance_ms} ms)"
        )
        await self._emit({"t": "audio", "state": "capturing"})
        return True

    async def end(self) -> None:
        """La lecture est finie : la capture s'arrête, la sortie par défaut d'avant revient."""
        was_active = self.active
        self.active = False
        await self._abandon()
        if was_active:
            self._event(f"son : capture arrêtée ({self.bytes // 1024} Ko relayés)")
            await self._emit({"t": "audio", "state": "stopped"})

    async def probe(self) -> str:
        """Ce que le Pi a comme serveur de son, en une ligne (pour le journal de démarrage)."""
        try:
            info = await self._run("pactl", "info")
        except SoundProblem as problem:
            return f"aucun serveur de son joignable — {problem}"
        for line in info.splitlines():
            if line.startswith("Server Name:"):
                return line.split(":", 1)[1].strip()
        return "serveur de son joignable"

    # --- le puits ---------------------------------------------------------------------------------------

    async def _run(self, program: str, *args: str) -> str:
        path = self._which(program)
        if path is None:
            raise SoundProblem(f"{program} est absent du Pi : {FIX}.")
        try:
            process = await asyncio.create_subprocess_exec(
                path,
                *args,
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env={**self._env, "LC_ALL": "C"},
            )
            out, err = await asyncio.wait_for(process.communicate(), COMMAND_TIMEOUT)
        except TimeoutError:
            raise SoundProblem("Le serveur de son du Pi ne répond pas.") from None
        except OSError as exc:
            raise SoundProblem(f"{program} ne démarre pas ({exc.strerror}).") from None
        if process.returncode != 0:
            detail = (err.decode("utf-8", "replace").strip().splitlines() or [""])[0][:120]
            raise SoundProblem(
                f"Pas de serveur de son (PipeWire) dans la session de l'écran ({detail or program}) : {FIX}."
            )
        return out.decode("utf-8", "replace")

    async def _prepare(self) -> None:
        # La sortie par défaut d'avant, relevée AVANT de créer le puits : PipeWire en fait aussitôt la sortie par défaut.
        current = (await self._run("pactl", "get-default-sink")).strip()
        if current and current != SINK:
            self._previous_default = current
        sinks = await self._run("pactl", "list", "short", "sinks")
        if not any(line.split("\t")[1:2] == [SINK] for line in sinks.splitlines()):
            await self._run(
                "pactl", "load-module", "module-null-sink", f"sink_name={SINK}",
                "sink_properties=device.description=MyDock-PC",
                f"rate={RATE}", f"channels={CHANNELS}", f"format={FORMAT}",
                "channel_map=front-left,front-right",
            )  # fmt: skip
        await self._run("pactl", "set-default-sink", SINK)

    async def _start_capture(self) -> None:
        path = self._which("parec")
        if path is None:
            raise SoundProblem(f"parec est absent du Pi : {FIX}.")
        try:
            self._capture = await asyncio.create_subprocess_exec(
                path,
                f"--device={SINK}.monitor",
                f"--format={FORMAT}",
                f"--rate={RATE}",
                f"--channels={CHANNELS}",
                f"--latency-msec={CAPTURE_LATENCY_MS}",
                "--client-name=MyDock",
                "--stream-name=MyDock - son du lecteur",
                "--raw",
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
                env={**self._env, "LC_ALL": "C"},
            )
        except OSError as exc:
            raise SoundProblem(f"parec ne démarre pas ({exc.strerror}).") from None
        self._pump_task = asyncio.create_task(self._pump(self._capture))

    async def _pump(self, process: asyncio.subprocess.Process) -> None:
        """Lit le son du puits et le passe au dock, par morceaux d'échantillons entiers."""
        stream = process.stdout
        assert stream is not None
        pending = b""
        try:
            while chunk := await stream.read(READ_BYTES):
                pending += chunk
                usable = len(pending) - len(pending) % SAMPLE_BYTES
                if usable:
                    data, pending = pending[:usable], pending[usable:]
                    self.bytes += len(data)
                    await self._send_frame(data)
        except (ConnectionError, asyncio.CancelledError):
            raise
        except Exception:
            log.exception("Capture du son interrompue")
        if (
            self.active and self._capture is process
        ):  # parec s'est arrêté seul en pleine lecture (serveur de son relancé…)
            self.active = False
            self._event("son : la capture s'est arrêtée toute seule")
            with contextlib.suppress(Exception):
                await self._emit(
                    {
                        "t": "audio",
                        "state": "unavailable",
                        "reason": "La capture du son du Pi s'est arrêtée.",
                    }
                )

    async def _abandon(self) -> None:
        """Arrête la capture et rend la sortie par défaut ; sans effet si rien n'est en cours."""
        task, process = self._pump_task, self._capture
        self._pump_task = self._capture = None
        if process is not None:
            with contextlib.suppress(ProcessLookupError):
                process.terminate()
            try:
                await asyncio.wait_for(process.wait(), 1.0)
            except TimeoutError:
                with contextlib.suppress(ProcessLookupError):
                    process.kill()
                await process.wait()
        if task is not None:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        previous, self._previous_default = self._previous_default, None
        if previous is not None:
            with contextlib.suppress(SoundProblem):
                await self._run("pactl", "set-default-sink", previous)


class DemoSound(PcSound):
    """En démonstration, le cycle est le même mais sans serveur de son ni capture : le dock relaie un flux vide au PC de la démo."""

    async def _prepare(self) -> None:
        return None

    async def _start_capture(self) -> None:
        return None

    async def probe(self) -> str:
        return "démonstration (aucun son capturé)"


def usable_environment(env: dict[str, str]) -> dict[str, str]:
    """L'environnement des commandes de son : celui de la session, sans une cible de flux qui fausserait `pactl` et `parec`."""
    return {k: v for k, v in env.items() if k not in ("PULSE_SINK", "PIPEWIRE_NODE")} | (
        {"HOME": os.environ["HOME"]} if "HOME" in os.environ and "HOME" not in env else {}
    )
