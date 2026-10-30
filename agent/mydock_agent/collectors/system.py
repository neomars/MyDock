"""Thème « system » : processeur, mémoire, réseau, disques, températures, batterie, cartes graphiques.

Une mesure par seconde au plus, publiée avec l'historique des 60 dernières secondes : la page
Système affiche donc son graphique tout de suite, même ouverte à l'instant. Le fournisseur est
« paresseux » : il ne mesure que tant que le dock (ou un client) est abonné. Les lectures qui
coûtent (disques, cartes graphiques) se font moins souvent et hors de la boucle d'événements.

Toutes les lectures matérielles passent par `Sensors` : les tests la remplacent par une fausse,
et la logique (débits, choix de la température du CPU, tri des disques…) reste testable seule.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from collections import deque
from collections.abc import Callable, Mapping, Sequence
from typing import Any

import psutil

from mydock_agent.access import is_virtual_interface
from mydock_agent.collectors import gpu as gpu_module
from mydock_agent.hub import Provider, Publish

log = logging.getLogger(__name__)

HISTORY_SECONDS = 60
INTERVAL = 1.0
# Une lecture sur N secondes : ce qui change lentement ou coûte cher.
EVERY = {"freq": 2, "temps": 2, "gpu": 2, "battery": 5, "disks": 15}

LOCAL_FILESYSTEMS = frozenset(
    {
        "ext2",
        "ext3",
        "ext4",
        "btrfs",
        "xfs",
        "f2fs",
        "jfs",
        "reiserfs",
        "ntfs",
        "ntfs3",
        "fuseblk",
        "vfat",
        "exfat",
        "zfs",
    }
)
IGNORED_MOUNT_PREFIXES = ("/boot", "/snap", "/var/lib/snapd", "/var/snap", "/sys", "/proc", "/dev")
MAX_DISKS = 4


# --- logique pure (testée sans matériel) ------------------------------------------------------


class Ring:
    """Les N dernières valeurs d'une mesure (None : mesure absente à cet instant)."""

    def __init__(self, size: int = HISTORY_SECONDS):
        self._values: deque[float | int | None] = deque(maxlen=size)

    def add(self, value: float | int | None) -> None:
        self._values.append(value)

    def values(self) -> list[float | int | None]:
        return list(self._values)

    def __len__(self) -> int:
        return len(self._values)


def rate(previous: int | None, current: int, elapsed: float) -> int:
    """Octets par seconde entre deux relevés d'un compteur ; 0 si le compteur a été remis à zéro."""
    if previous is None or elapsed <= 0 or current < previous:
        return 0
    return round((current - previous) / elapsed)


def cpu_temperature(raw: Mapping[str, Sequence[tuple[str, float]]]) -> float | None:
    """Température du processeur parmi les capteurs de la carte mère (coretemp, k10temp…)."""
    for chip in ("coretemp", "k10temp", "zenpower", "cpu_thermal", "cpu-thermal"):
        readings = list(raw.get(chip, ()))
        if not readings:
            continue
        for wanted in ("package id 0", "tctl", "tdie"):
            for label, value in readings:
                if label.lower() == wanted:
                    return round(value, 1)
        return round(max(value for _label, value in readings), 1)
    readings = list(raw.get("acpitz", ()))
    return round(max(v for _l, v in readings), 1) if readings else None


def disk_temperature(raw: Mapping[str, Sequence[tuple[str, float]]]) -> float | None:
    values = [value for label, value in raw.get("nvme", ()) if label.lower() == "composite"]
    return round(max(values), 1) if values else None


def gpu_temperature(raw: Mapping[str, Sequence[tuple[str, float]]]) -> float | None:
    values = [value for _label, value in raw.get("amdgpu", ())]
    return round(max(values), 1) if values else None


def select_disks(partitions: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Disques à afficher : systèmes de fichiers locaux, sans doublon, la racine en premier."""
    best: dict[str, Mapping[str, Any]] = {}
    for part in partitions:
        mount = str(part["mount"])
        if part["fstype"] not in LOCAL_FILESYSTEMS or mount.startswith(IGNORED_MOUNT_PREFIXES):
            continue
        device = str(part["device"])
        if device.startswith("/dev/loop"):
            continue
        # Sous-volumes et montages « bind » : un même disque n'apparaît qu'une fois.
        if device not in best or len(mount) < len(str(best[device]["mount"])):
            best[device] = part
    chosen = sorted(best.values(), key=lambda p: (p["mount"] != "/", -int(p["total"])))
    return [
        {
            "mount": p["mount"],
            "device": os.path.basename(str(p["device"])),
            "fs": p["fstype"],
            "total": int(p["total"]),
            "used": int(p["used"]),
            "percent": round(float(p["percent"]), 1),
        }
        for p in chosen[:MAX_DISKS]
        if int(p["total"]) > 0
    ]


# --- lectures matérielles ---------------------------------------------------------------------


class Sensors:
    """Lecture brute de psutil et de /sys. Remplacée par une fausse dans les tests."""

    def prime(self) -> None:
        """Premier relevé : psutil mesure l'écart depuis l'appel précédent."""
        psutil.cpu_percent(percpu=True)

    def cpu_percent(self) -> list[float]:
        return psutil.cpu_percent(percpu=True)

    def cpu_freq_mhz(self) -> float | None:
        freq = psutil.cpu_freq()
        return freq.current if freq else None

    def load_average(self) -> tuple[float, float, float]:
        return os.getloadavg()

    def memory(self) -> dict[str, int]:
        virtual, swap = psutil.virtual_memory(), psutil.swap_memory()
        return {
            "total": virtual.total,
            "available": virtual.available,
            "swap_total": swap.total,
            "swap_used": swap.used,
        }

    def net_bytes(self) -> dict[str, tuple[int, int]]:
        """Octets reçus et envoyés par interface physique (ni boucle locale ni conteneurs)."""
        return {
            name: (counters.bytes_recv, counters.bytes_sent)
            for name, counters in psutil.net_io_counters(pernic=True).items()
            if not is_virtual_interface(name)
        }

    def disk_bytes(self) -> tuple[int, int] | None:
        counters = psutil.disk_io_counters()
        return (counters.read_bytes, counters.write_bytes) if counters else None

    def partitions(self) -> list[dict[str, Any]]:
        found = []
        for part in psutil.disk_partitions(all=False):
            try:
                usage = psutil.disk_usage(part.mountpoint)
            except OSError:
                continue
            found.append(
                {
                    "mount": part.mountpoint,
                    "device": part.device,
                    "fstype": part.fstype,
                    "total": usage.total,
                    "used": usage.used,
                    "percent": usage.percent,
                }
            )
        return found

    def temperatures(self) -> dict[str, list[tuple[str, float]]]:
        try:
            raw = psutil.sensors_temperatures()
        except (AttributeError, OSError):
            return {}
        return {
            chip: [(r.label or "", r.current) for r in readings] for chip, readings in raw.items()
        }

    def battery(self) -> dict[str, Any] | None:
        try:
            battery = psutil.sensors_battery()
        except (AttributeError, OSError):
            return None
        if battery is None:
            return None
        left = battery.secsleft
        return {
            "percent": round(battery.percent),
            "plugged": battery.power_plugged,
            "secs_left": left if isinstance(left, int) and left >= 0 else None,
        }

    def gpus(self) -> list[dict[str, Any]]:
        return gpu_module.read_gpus()


# --- le fournisseur ---------------------------------------------------------------------------


class SystemProvider(Provider):
    topic = "system"
    lazy = True

    def __init__(
        self,
        sensors: Sensors | None = None,
        *,
        interval: float = INTERVAL,
        clock: Callable[[], float] = time.monotonic,
    ):
        self._sensors = sensors or Sensors()
        self._interval = interval
        self._clock = clock
        self._reset()

    def _reset(self) -> None:
        self._history = {key: Ring() for key in ("cpu", "mem", "gpu", "down", "up")}
        self._net_previous: dict[str, tuple[int, int]] | None = None
        self._disk_previous: tuple[int, int] | None = None
        self._tick = 0
        self._cache: dict[str, Any] = {}

    def _due(self, key: str) -> bool:
        return self._tick % EVERY[key] == 0 or key not in self._cache

    async def sample(self, elapsed: float) -> dict[str, Any]:
        """Une mesure complète (`elapsed` : secondes depuis la précédente, pour les débits)."""
        s = self._sensors
        cores = s.cpu_percent()
        cpu_percent = round(sum(cores) / len(cores), 1) if cores else 0.0
        memory = s.memory()
        used = memory["total"] - memory["available"]
        mem_percent = round(100 * used / memory["total"], 1) if memory["total"] else 0.0

        net = s.net_bytes()
        previous = self._net_previous
        down = sum(
            rate(previous[n][0] if previous and n in previous else None, c[0], elapsed)
            for n, c in net.items()
        )
        up = sum(
            rate(previous[n][1] if previous and n in previous else None, c[1], elapsed)
            for n, c in net.items()
        )
        self._net_previous = net

        disk_now = s.disk_bytes()
        disk_read = disk_write = 0
        if disk_now is not None:
            if self._disk_previous is not None:
                disk_read = rate(self._disk_previous[0], disk_now[0], elapsed)
                disk_write = rate(self._disk_previous[1], disk_now[1], elapsed)
            self._disk_previous = disk_now

        if self._due("freq"):
            self._cache["freq"] = s.cpu_freq_mhz()
        if self._due("temps"):
            self._cache["temps"] = s.temperatures()
        if self._due("battery"):
            self._cache["battery"] = s.battery()
        if self._due("disks"):
            self._cache["disks"] = select_disks(await asyncio.to_thread(s.partitions))
        if self._due("gpu"):
            self._cache["gpu"] = await asyncio.to_thread(s.gpus)

        raw_temps = self._cache["temps"]
        temps = {
            key: value
            for key, value in (
                ("cpu", cpu_temperature(raw_temps)),
                ("disk", disk_temperature(raw_temps)),
                ("gpu", gpu_temperature(raw_temps)),
            )
            if value is not None
        }
        gpus = self._cache["gpu"]
        gpu_util = next((g["util"] for g in gpus if g.get("util") is not None), None)

        for key, value in (
            ("cpu", cpu_percent),
            ("mem", mem_percent),
            ("gpu", gpu_util),
            ("down", down),
            ("up", up),
        ):
            self._history[key].add(value)
        self._tick += 1

        freq = self._cache["freq"]
        return {
            "cpu": {
                "percent": cpu_percent,
                "cores": [round(c) for c in cores],
                "freq_mhz": round(freq) if freq else None,
                "temp_c": temps.get("cpu"),
                "load": [round(v, 2) for v in s.load_average()],
            },
            "mem": {
                "total": memory["total"],
                "used": used,
                "percent": mem_percent,
                "swap_total": memory["swap_total"],
                "swap_used": memory["swap_used"],
            },
            "net": {"down": down, "up": up},
            "disk_io": {"read": disk_read, "write": disk_write},
            "disks": self._cache["disks"],
            "temps": temps,
            "battery": self._cache["battery"],
            "gpus": gpus,
            "history": {key: ring.values() for key, ring in self._history.items()},
        }

    async def run(self, publish: Publish) -> None:
        self._reset()
        sensors = self._sensors
        await asyncio.to_thread(sensors.prime)
        previous = self._clock()
        await asyncio.sleep(min(0.25, self._interval))  # première mesure sans attendre une seconde
        while True:
            now = self._clock()
            try:
                data = await self.sample(now - previous)
            except Exception:
                log.exception("Mesure du système impossible")
                data = {"unavailable": "Lecture des capteurs impossible."}
            previous = now
            await publish(data)
            await asyncio.sleep(self._interval)
