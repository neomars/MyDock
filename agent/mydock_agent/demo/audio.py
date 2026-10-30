"""Démonstration de « audio » : un serveur de son fictif qui parle comme `pactl`.

Même JSON que `pactl -f json`, mêmes commandes (`set-sink-volume`...), mêmes événements : le vrai
fournisseur (`AudioProvider`) tourne tel quel par-dessus, sans carte son ni PipeWire. L'interface
ne sait pas qu'elle parle à une démonstration, et les tests peuvent y brancher des pannes.

Le PC fictif est un portable : haut-parleurs, un casque Bluetooth, un écran HDMI (un second, non
branché, que l'agent doit cacher), un micro intégré et un micro de webcam USB.
"""

from __future__ import annotations

import asyncio
import contextlib
import copy
import math
from collections.abc import AsyncIterator
from contextlib import AbstractAsyncContextManager
from typing import Any

from mydock_agent.collectors.audio import NORM, PactlError

CHANNELS = {1: ["mono"], 2: ["front-left", "front-right"]}
#: Les commandes que la démonstration comprend, et le type d'élément qu'elles visent.
FACILITY_OF = {
    "set-sink-volume": "sink",
    "set-sink-mute": "sink",
    "set-default-sink": "sink",
    "set-source-volume": "source",
    "set-source-mute": "source",
    "set-default-source": "source",
    "set-sink-input-volume": "sink-input",
    "set-sink-input-mute": "sink-input",
}


def volume(percents: list[int]) -> dict[str, Any]:
    """Un volume au format de pactl : une entrée par canal, valeur brute, pourcentage, décibels."""
    names = CHANNELS[len(percents)]
    out: dict[str, Any] = {}
    for name, percent in zip(names, percents, strict=True):
        raw = round(percent * NORM / 100)
        db = -math.inf if raw == 0 else 60 * math.log10(raw / NORM)  # la loi cubique de PulseAudio
        out[name] = {
            "value": raw,
            "value_percent": f"{percent}%",
            "db": "-inf dB" if raw == 0 else f"{db:.2f} dB",
        }
    return out


def port(
    name: str, description: str, kind: str, availability: str = "availability unknown"
) -> dict:
    return {
        "name": name,
        "description": description,
        "type": kind,
        "priority": 1000,
        "availability_group": "",
        "availability": availability,
    }


def device(
    index: int,
    name: str,
    description: str,
    percents: list[int],
    *,
    props: dict[str, str],
    ports: list[dict[str, Any]] | None = None,
    active: str | None = None,
    monitor: bool = False,
) -> dict[str, Any]:
    return {
        "index": index,
        "state": "SUSPENDED",
        "name": name,
        "description": description,
        "driver": "PipeWire",
        "mute": False,
        "volume": volume(percents),
        "balance": 0.0,
        "ports": ports or [],
        "active_port": active,
        "properties": {
            "device.description": description,
            "device.class": "monitor" if monitor else "sound",
            **props,
        },
    }


def stream(
    index: int,
    app: str,
    percents: list[int],
    *,
    to: int,
    media: str = "Playback",
    role: str = "music",
    corked: bool = False,
    capture: bool = False,
) -> dict[str, Any]:
    return {
        "index": index,
        "driver": "PipeWire",
        "client": str(index + 1000),
        "source" if capture else "sink": to,
        "corked": corked,
        "mute": False,
        "volume": volume(percents),
        "balance": 0.0,
        "properties": {
            "application.name": app,
            "application.process.binary": app.lower(),
            "media.name": media,
            "media.role": role,
        },
    }


class DemoPactl:
    """Le serveur de son du PC fictif. `commands` garde ce qu'on lui a demandé (utile aux tests)."""

    def __init__(self) -> None:
        speakers, bluetooth, hdmi = 51, 52, 53
        self.default_sink = "alsa_output.pci-0000_00_1f.3.analog-stereo"
        self.default_source = "alsa_input.pci-0000_00_1f.3.analog-stereo"
        internal_mic, webcam_mic = 61, 62
        self.sinks = [
            device(
                speakers,
                self.default_sink,
                "Built-in Audio Analog Stereo",
                [54, 54],
                props={"device.form_factor": "internal", "device.bus": "pci"},
                ports=[
                    port("analog-output-speaker", "Speakers", "Speaker"),
                    port("analog-output-headphones", "Headphones", "Headphones", "not available"),
                ],
                active="analog-output-speaker",
            ),
            device(
                bluetooth,
                "bluez_output.80_C3_BA_12_34_56.1",
                "WH-1000XM4",
                [40, 40],
                props={"device.form_factor": "headphone", "device.bus": "bluetooth"},
            ),
            device(
                hdmi,
                "alsa_output.pci-0000_00_1f.3.hdmi-stereo-extra1",
                "Built-in Audio Digital Stereo (HDMI 1)",
                [100, 100],
                props={"device.bus": "pci"},
                ports=[port("hdmi-output-1", "HDMI / DisplayPort 1", "HDMI", "available")],
                active="hdmi-output-1",
            ),
            device(
                54,
                "alsa_output.pci-0000_00_1f.3.hdmi-stereo-extra2",
                "Built-in Audio Digital Stereo (HDMI 2)",
                [100, 100],
                props={"device.bus": "pci"},
                ports=[port("hdmi-output-2", "HDMI / DisplayPort 2", "HDMI", "not available")],
                active="hdmi-output-2",
            ),
        ]
        self.sources = [
            device(
                internal_mic,
                self.default_source,
                "Built-in Audio Analog Stereo",
                [70, 70],
                props={"device.form_factor": "internal", "device.bus": "pci"},
                ports=[port("analog-input-internal-mic", "Internal Microphone", "Mic")],
                active="analog-input-internal-mic",
            ),
            device(
                webcam_mic,
                "alsa_input.usb-046d_HD_Pro_Webcam_C920-02.analog-stereo",
                "HD Pro Webcam C920 Analog Stereo",
                [90, 90],
                props={"device.form_factor": "webcam", "device.bus": "usb"},
            ),
            *(
                device(
                    sink["index"],
                    f"{sink['name']}.monitor",
                    f"Monitor of {sink['description']}",
                    [100, 100],
                    props={},
                    monitor=True,
                )
                for sink in self.sinks
            ),
        ]
        self.inputs = [
            stream(101, "Spotify", [85, 85], to=speakers, media="Nuit blanche"),
            stream(102, "Brave", [100, 100], to=speakers, role="video", corked=True),
            stream(103, "Discord", [65, 65], to=speakers, role="communication"),
            stream(104, "GNOME Shell", [100, 100], to=speakers, media="Sound event", role="event"),
        ]
        self.outputs = [
            stream(201, "Discord", [100], to=internal_mic, role="communication", capture=True),
            stream(202, "pavucontrol", [100], to=internal_mic, media="Peak detect", capture=True),
        ]
        self.commands: list[tuple[str, ...]] = []
        self.queries: list[tuple[str, ...]] = []
        self.fail: PactlError | None = None  # tant qu'elle est définie, tout appel échoue
        self.fail_commands: PactlError | None = None  # idem, pour les seules commandes
        self.subscribers = 0
        self._listeners: list[asyncio.Queue[str | None]] = []

    # --- le côté « pactl » ---

    async def query(self, *args: str) -> Any:
        self._check()
        self.queries.append(args)
        match args:
            case ("info",):
                return {
                    "server_name": "PulseAudio (on PipeWire 1.2.7)",
                    "default_sink_name": self.default_sink,
                    "default_source_name": self.default_source,
                }
            case ("list", "sinks" | "sources" | "sink-inputs" | "source-outputs" as listing):
                pool = {
                    "sinks": self.sinks,
                    "sources": self.sources,
                    "sink-inputs": self.inputs,
                    "source-outputs": self.outputs,
                }[listing]
                return copy.deepcopy(pool)
        raise PactlError(f"Commande inconnue de la démonstration : {' '.join(args)}")

    async def command(self, *args: str) -> None:
        self._check()
        if self.fail_commands is not None:
            raise self.fail_commands
        self.commands.append(args)
        if len(args) < 2 or args[0] not in FACILITY_OF:
            raise PactlError(f"Commande inconnue de la démonstration : {' '.join(args)}")
        verb, target, *rest = args
        facility = FACILITY_OF[verb]
        item = self._find(facility, target)
        if verb.endswith("-volume"):
            self._set_volume(item, rest)
        elif verb.endswith("-mute"):
            item["mute"] = self._mute_value(item, rest)
        elif facility == "sink":
            self.default_sink = item["name"]
            for (
                playing
            ) in self.inputs:  # comme PipeWire : les applications suivent la sortie par défaut
                playing["sink"] = item["index"]
        else:
            self.default_source = item["name"]
            for recording in self.outputs:
                recording["source"] = item["index"]
        if verb.startswith("set-default"):
            self._emit("change", "server", 0)
        else:
            self._emit("change", facility, item["index"])

    def subscribe(self) -> AbstractAsyncContextManager[AsyncIterator[str]]:
        return self._subscribe()

    @contextlib.asynccontextmanager
    async def _subscribe(self) -> AsyncIterator[AsyncIterator[str]]:
        self._check()
        queue: asyncio.Queue[str | None] = asyncio.Queue()
        self._listeners.append(queue)
        self.subscribers += 1

        async def lines() -> AsyncIterator[str]:
            while (line := await queue.get()) is not None:
                yield line

        try:
            yield lines()
        finally:
            self._listeners.remove(queue)
            self.subscribers -= 1

    # --- les pannes et les événements, pour les tests ---

    def drop(self) -> None:
        """Le serveur de son s'arrête : les abonnements se terminent."""
        for queue in self._listeners:
            queue.put_nowait(None)

    def touch(self, facility: str = "sink", index: int = 0, event: str = "change") -> None:
        """Émet un événement comme le ferait le serveur (sans rien modifier)."""
        self._emit(event, facility, index)

    # --- interne ---

    def _check(self) -> None:
        if self.fail is not None:
            raise self.fail

    def _emit(self, event: str, facility: str, index: int) -> None:
        line = f"Event '{event}' on {facility} #{index}\n"
        for queue in self._listeners:
            queue.put_nowait(line)

    def _find(self, facility: str, target: str) -> dict[str, Any]:
        if facility == "sink-input":
            pool, key = self.inputs, "index"
        elif facility == "sink":
            pool, key = self.sinks, "name"
        else:
            pool = [s for s in self.sources if s["properties"]["device.class"] != "monitor"]
            key = "name"
        for item in pool:
            if str(item[key]) == target:
                return item
        raise PactlError(f"Failure: No such entity ({facility} {target})", gone=True)

    @staticmethod
    def _parse_volume(text: str) -> int:
        try:
            if text.endswith("%"):
                return round(float(text[:-1]) * NORM / 100)
            return int(text)
        except ValueError:
            raise PactlError(f"Failed to parse volume: {text}") from None

    def _set_volume(self, item: dict[str, Any], values: list[str]) -> None:
        channels = list(item["volume"])
        raw = [self._parse_volume(v) for v in values]
        if len(raw) == 1:
            raw *= len(channels)
        if len(raw) != len(channels):
            raise PactlError(
                f"Failed to set volume: You tried to set volumes for {len(raw)} channels, "
                f"whereas channel/s supported = {len(channels)}."
            )
        for name, value in zip(channels, raw, strict=True):
            percent = round(value * 100 / NORM)
            db = "-inf dB" if value == 0 else f"{60 * math.log10(value / NORM):.2f} dB"
            item["volume"][name] = {"value": value, "value_percent": f"{percent}%", "db": db}

    @staticmethod
    def _mute_value(item: dict[str, Any], rest: list[str]) -> bool:
        match rest:
            case ["1"]:
                return True
            case ["0"]:
                return False
            case ["toggle"]:
                return not item["mute"]
        raise PactlError("Failed to parse mute argument.")
