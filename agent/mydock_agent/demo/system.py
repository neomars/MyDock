"""Démonstration de « system » et « procs » : de fausses mesures vraisemblables, sans le PC.

Les capteurs de démonstration remplacent seulement la lecture du matériel : le vrai
`SystemProvider` tourne par-dessus (débits, historique, choix des disques…), donc la démo
exerce exactement le code de production.
"""

from __future__ import annotations

import asyncio
import math
import os
import time
from typing import Any

from mydock_agent.collectors.procs import (
    EXIT_WAIT,
    KillParams,
    ProcessSource,
    ProcSample,
    ProcsProvider,
)
from mydock_agent.collectors.system import Sensors
from mydock_common.actions import ActionContext, ActionError, ActionRegistry

GIB = 1 << 30


class DemoSensors(Sensors):
    """Portable hybride fictif : Intel éveillée, NVIDIA endormie (ou éveillée, pour les captures)."""

    def __init__(self, *, nvidia_awake: bool | None = None, clock=time.monotonic):
        if nvidia_awake is None:
            nvidia_awake = os.environ.get("MYDOCK_DEMO_NVIDIA", "asleep") == "awake"
        self._nvidia_awake = nvidia_awake
        self._clock = clock
        self._t0 = clock()
        self._rx = 8_000_000_000
        self._tx = 1_200_000_000
        self._read = 90_000_000_000
        self._written = 60_000_000_000
        self._last = self._t0

    def _t(self) -> float:
        return self._clock() - self._t0

    def prime(self) -> None:
        pass

    def cpu_percent(self) -> list[float]:
        t = self._t()
        return [max(1.0, min(99.0, 22 + 18 * math.sin(t / 5 + i * 0.9) + 6 * math.sin(t * 1.7 + i)))
                for i in range(8)]  # fmt: skip

    def cpu_freq_mhz(self) -> float:
        return 2400 + 1200 * abs(math.sin(self._t() / 9))

    def load_average(self) -> tuple[float, float, float]:
        return (1.84, 1.52, 1.1)

    def memory(self) -> dict[str, int]:
        t = self._t()
        return {
            "total": 32 * GIB,
            "available": int((19 + 2 * math.sin(t / 23)) * GIB),
            "swap_total": 8 * GIB,
            "swap_used": 300 * (1 << 20),
        }

    def net_bytes(self) -> dict[str, tuple[int, int]]:
        now = self._clock()
        dt, self._last = now - self._last, now
        t = self._t()
        self._rx += int(dt * (1_800_000 + 1_500_000 * math.sin(t / 6)))
        self._tx += int(dt * (160_000 + 90_000 * math.sin(t / 4)))
        return {"wlp0s20f3": (self._rx, self._tx), "enp0s31f6": (0, 0)}

    def disk_bytes(self) -> tuple[int, int]:
        t = self._t()
        self._read += int(40_000_000 * max(0.0, math.sin(t / 8)))
        self._written += int(12_000_000 * max(0.0, math.sin(t / 5)))
        return (self._read, self._written)

    def partitions(self) -> list[dict[str, Any]]:
        return [
            {
                "mount": "/",
                "device": "/dev/nvme0n1p2",
                "fstype": "ext4",
                "total": 1000 * GIB,
                "used": 412 * GIB,
                "percent": 41.2,
            },
            {
                "mount": "/home/mars/Jeux",
                "device": "/dev/nvme1n1p1",
                "fstype": "ext4",
                "total": 2000 * GIB,
                "used": 1260 * GIB,
                "percent": 63.0,
            },
        ]

    def temperatures(self) -> dict[str, list[tuple[str, float]]]:
        t = self._t()
        return {
            "coretemp": [("Package id 0", 54 + 9 * math.sin(t / 7)), ("Core 0", 52.0)],
            "nvme": [("Composite", 39.0)],
        }

    def battery(self) -> dict[str, Any]:
        return {"percent": 82, "plugged": True, "secs_left": None}

    def gpus(self) -> list[dict[str, Any]]:
        t = self._t()
        intel = {
            "id": "0000:00:02.0",
            "vendor": "intel",
            "name": "Intel UHD Graphics 770",
            "state": "active",
            "util": None,
            "mem_used": None,
            "mem_total": None,
            "temp_c": None,
            "power_w": None,
            "freq_mhz": int(350 + 250 * abs(math.sin(t / 11))),
        }
        nvidia = {
            "id": "0000:01:00.0",
            "vendor": "nvidia",
            "name": "NVIDIA RTX 4000 Ada Generation Laptop GPU",
            "state": "active" if self._nvidia_awake else "suspended",
            "util": None,
            "mem_used": None,
            "mem_total": None,
            "temp_c": None,
            "power_w": None,
            "freq_mhz": None,
        }
        if self._nvidia_awake:
            nvidia.update(
                util=int(35 + 25 * math.sin(t / 6)),
                mem_used=int(2.4 * GIB),
                mem_total=12 * GIB,
                temp_c=58,
                power_w=34.5,
                freq_mhz=1650,
            )
        return [intel, nvidia]


class DemoProcessSource(ProcessSource):
    """Quelques processus fictifs ; l'un d'eux peut être « tué » depuis le dock."""

    def __init__(self) -> None:
        self._cores = 8
        me = os.getuid()
        t0 = round(time.time() - 7200, 2)
        self.samples: list[ProcSample] = [
            ProcSample(4101, "brave", 14.0, 1_450 << 20, "mars", me, t0),
            ProcSample(2210, "gnome-shell", 6.5, 520 << 20, "mars", me, t0),
            ProcSample(5530, "spotify", 4.2, 610 << 20, "mars", me, t0),
            ProcSample(7712, "code", 3.1, 980 << 20, "mars", me, t0),
            ProcSample(9034, "zoom-vide", 2.4, 240 << 20, "mars", me, t0 + 60),
            ProcSample(1450, "Xwayland", 1.2, 130 << 20, "mars", me, t0),
            ProcSample(812, "pipewire", 0.8, 60 << 20, "mars", me, t0),
            ProcSample(1204, "cupsd", 0.1, 25 << 20, "root", 0 if me else 1, t0),
            ProcSample(1190, "tracker-miner-fs", 0.0, 90 << 20, "mars", me, t0),
        ]

    def scan(self) -> list[ProcSample]:
        wobble = math.sin(time.monotonic() / 4)
        return [
            ProcSample(s.pid, s.name, round(max(0.0, s.cpu * (1 + 0.15 * wobble)), 1), s.rss,
                       s.user, s.uid, s.started)
            for s in self.samples
        ]  # fmt: skip

    def remove(self, pid: int) -> None:
        self.samples = [s for s in self.samples if s.pid != pid]


def register_demo_system_actions(
    registry: ActionRegistry, provider: ProcsProvider, source: DemoProcessSource
) -> None:
    """« system.kill » de démonstration : mêmes paramètres et même confirmation, aucun effet réel."""

    def label(params: KillParams) -> str:
        name = provider.name_of(params.pid)
        verb = "Forcer l'arrêt de" if params.force else "Terminer"
        target = f"« {name} » (PID {params.pid})" if name else f"le processus {params.pid}"
        return f"{verb} {target} (démo, sans effet réel)"

    async def kill(_ctx: ActionContext, params: KillParams) -> dict[str, Any]:
        found = next((s for s in source.samples if s.pid == params.pid), None)
        if found is None:
            raise ActionError("gone", "Ce processus n'existe plus.")
        await asyncio.sleep(0.05)
        source.remove(params.pid)
        return {
            "pid": params.pid,
            "name": found.name,
            "signal": "SIGKILL" if params.force else "SIGTERM",
            "gone": True,
        }

    registry.register(
        "system.kill", kill, params=KillParams, sensitive=True, label=label, timeout=EXIT_WAIT + 3.0
    )


__all__ = ["DemoProcessSource", "DemoSensors", "register_demo_system_actions"]
