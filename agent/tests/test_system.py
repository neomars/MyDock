"""Thème « system » : débits, températures, disques, historique de 60 s (faux capteurs)."""

import asyncio
import json

import pytest

from mydock_agent.collectors import system
from mydock_agent.collectors.system import (
    Ring,
    Sensors,
    SystemProvider,
    cpu_temperature,
    disk_temperature,
    gpu_temperature,
    rate,
    select_disks,
)
from mydock_agent.testing import eventually


class FakeSensors(Sensors):
    """Capteurs pilotés par le test : on règle les valeurs, on compte les lectures coûteuses."""

    def __init__(self):
        self.cores = [10.0, 30.0]
        self.freq = 3200.4
        self.load = (0.5, 0.4, 0.3)
        self.mem = {
            "total": 16 << 30,
            "available": 12 << 30,
            "swap_total": 4 << 30,
            "swap_used": 1 << 20,
        }
        self.net = {"wlp0s20f3": (1_000, 500), "enp0s31f6": (0, 0)}
        self.disk_io = (1_000_000, 2_000_000)
        self.parts = [
            {
                "mount": "/",
                "device": "/dev/nvme0n1p2",
                "fstype": "ext4",
                "total": 500 << 30,
                "used": 200 << 30,
                "percent": 40.04,
            }
        ]
        self.temps = {"coretemp": [("Package id 0", 54.0), ("Core 0", 52.0)]}
        self.battery_info = {"percent": 85, "plugged": True, "secs_left": None}
        self.gpu_list = [{"id": "0000:01:00.0", "util": 12, "state": "active"}]
        self.reads = {"partitions": 0, "gpus": 0, "temps": 0, "battery": 0, "freq": 0, "primed": 0}

    def prime(self):
        self.reads["primed"] += 1

    def cpu_percent(self):
        return list(self.cores)

    def cpu_freq_mhz(self):
        self.reads["freq"] += 1
        return self.freq

    def load_average(self):
        return self.load

    def memory(self):
        return dict(self.mem)

    def net_bytes(self):
        return dict(self.net)

    def disk_bytes(self):
        return self.disk_io

    def partitions(self):
        self.reads["partitions"] += 1
        return list(self.parts)

    def temperatures(self):
        self.reads["temps"] += 1
        return dict(self.temps)

    def battery(self):
        self.reads["battery"] += 1
        return self.battery_info

    def gpus(self):
        self.reads["gpus"] += 1
        return list(self.gpu_list)


# --- logique pure --------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("previous", "current", "elapsed", "expected"),
    [
        (1_000, 3_000, 1.0, 2_000),
        (1_000, 3_000, 2.0, 1_000),
        (None, 3_000, 1.0, 0),  # premier relevé : pas de débit
        (5_000, 1_000, 1.0, 0),  # compteur remis à zéro (interface relancée)
        (1_000, 3_000, 0, 0),
        (0, 1_500, 1.0, 1_500),
    ],
)
def test_rates_are_bytes_per_second_and_never_negative(previous, current, elapsed, expected):
    assert rate(previous, current, elapsed) == expected


def test_the_ring_keeps_the_last_values_in_order():
    ring = Ring(3)
    for value in (1, 2, 3, 4, None):
        ring.add(value)
    assert ring.values() == [3, 4, None]
    assert len(ring) == 3


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ({"coretemp": [("Core 0", 50.0), ("Package id 0", 61.0), ("Core 1", 55.0)]}, 61.0),
        ({"coretemp": [("Core 0", 50.0), ("Core 1", 57.3)]}, 57.3),  # sans paquet : le plus chaud
        ({"k10temp": [("Tctl", 66.2), ("Tccd1", 60.0)]}, 66.2),
        ({"k10temp": [("Tdie", 64.0)]}, 64.0),
        ({"acpitz": [("", 27.8), ("", 30.1)]}, 30.1),  # repli
        ({"coretemp": [("Package id 0", 61.0)], "acpitz": [("", 99.0)]}, 61.0),  # coretemp prime
        ({"nvme": [("Composite", 40.0)]}, None),  # un SSD n'est pas le processeur
        ({}, None),
    ],
)
def test_the_cpu_temperature_comes_from_the_right_sensor(raw, expected):
    assert cpu_temperature(raw) == expected


def test_disk_and_gpu_temperatures():
    raw = {
        "nvme": [("Composite", 38.0), ("Sensor 1", 55.0)],
        "amdgpu": [("edge", 45.0)],
    }
    assert disk_temperature(raw) == 38.0
    assert gpu_temperature(raw) == 45.0
    assert disk_temperature({}) is None
    assert gpu_temperature({}) is None


def part(mount, device, fstype="ext4", total=100 << 30, used=10 << 30, percent=10.0):
    return {
        "mount": mount,
        "device": device,
        "fstype": fstype,
        "total": total,
        "used": used,
        "percent": percent,
    }


def test_only_real_local_disks_are_shown_root_first():
    shown = select_disks(
        [
            part("/home/mars/Jeux", "/dev/sda1", total=2000 << 30),
            part("/", "/dev/nvme0n1p2", total=500 << 30),
            part("/boot/efi", "/dev/nvme0n1p1", "vfat", total=1 << 30),
            part("/snap/core22/1", "/dev/loop3", "squashfs"),
            part("/snap/firefox/9", "/dev/loop4", "ext4"),
            part("/run/user/1000/doc", "portal", "fuse.portal"),
            part("/mnt/nas", "//nas/partage", "cifs", total=9000 << 30),
            part("/media/mars/CLE", "/dev/sdb1", "exfat", total=64 << 30),
        ]
    )
    assert [d["mount"] for d in shown] == ["/", "/home/mars/Jeux", "/media/mars/CLE"]
    assert shown[0]["device"] == "nvme0n1p2"  # nom court, sans /dev/
    assert shown[0]["fs"] == "ext4"


def test_subvolumes_of_the_same_disk_appear_once():
    shown = select_disks(
        [
            part("/home", "/dev/nvme0n1p2", "btrfs"),
            part("/", "/dev/nvme0n1p2", "btrfs"),
            part("/var/log", "/dev/nvme0n1p2", "btrfs"),
        ]
    )
    assert [d["mount"] for d in shown] == ["/"]


def test_at_most_four_disks_and_no_empty_ones():
    parts = [part(f"/mnt/d{n}", f"/dev/sd{n}1", total=(n + 1) << 30) for n in range(7)]
    parts.append(part("/mnt/vide", "/dev/sdz1", total=0))
    shown = select_disks(parts)
    assert len(shown) == system.MAX_DISKS
    assert all(d["total"] > 0 for d in shown)


# --- le fournisseur ------------------------------------------------------------------------------


async def test_the_first_sample_reports_the_cpu_memory_and_has_no_rates_yet():
    sensors = FakeSensors()
    data = await SystemProvider(sensors).sample(1.0)
    assert data["cpu"]["percent"] == 20.0  # moyenne des cœurs
    assert data["cpu"]["cores"] == [10, 30]
    assert data["cpu"]["freq_mhz"] == 3200
    assert data["cpu"]["temp_c"] == 54.0
    assert data["cpu"]["load"] == [0.5, 0.4, 0.3]
    assert data["mem"]["used"] == 4 << 30
    assert data["mem"]["percent"] == 25.0
    assert data["net"] == {"down": 0, "up": 0}  # un seul relevé : pas encore de débit
    assert data["disk_io"] == {"read": 0, "write": 0}
    assert data["disks"][0]["percent"] == 40.0
    assert data["battery"] == {"percent": 85, "plugged": True, "secs_left": None}
    assert data["temps"] == {"cpu": 54.0}
    json.dumps(data)  # uniquement des types JSON


async def test_network_and_disk_rates_follow_the_counters():
    sensors = FakeSensors()
    provider = SystemProvider(sensors)
    await provider.sample(1.0)
    sensors.net = {"wlp0s20f3": (1_000 + 4_000, 500 + 1_000), "enp0s31f6": (100, 0)}
    sensors.disk_io = (1_000_000 + 8_000_000, 2_000_000 + 500_000)
    data = await provider.sample(2.0)
    assert data["net"] == {"down": 2_050, "up": 500}  # (4000 + 100) / 2 s ; 1000 / 2 s
    assert data["disk_io"] == {"read": 4_000_000, "write": 250_000}


async def test_an_interface_appearing_later_does_not_count_its_whole_history_as_a_burst():
    sensors = FakeSensors()
    provider = SystemProvider(sensors)
    await provider.sample(1.0)
    sensors.net["usb0"] = (900_000_000, 900_000_000)  # compteurs déjà élevés à l'apparition
    assert (await provider.sample(1.0))["net"] == {"down": 0, "up": 0}


async def test_history_keeps_the_last_sixty_seconds():
    sensors = FakeSensors()
    provider = SystemProvider(sensors)
    for second in range(75):
        sensors.cores = [float(second % 100)]
        data = await provider.sample(1.0)
    history = data["history"]
    assert set(history) == {"cpu", "mem", "gpu", "down", "up"}
    assert all(len(values) == system.HISTORY_SECONDS for values in history.values())
    assert history["cpu"][-1] == 74.0
    assert history["cpu"][0] == 15.0  # les 15 plus anciennes ont disparu


async def test_gpu_history_has_gaps_while_the_card_sleeps():
    sensors = FakeSensors()
    provider = SystemProvider(sensors)
    await provider.sample(1.0)
    sensors.gpu_list = [{"id": "0000:01:00.0", "util": None, "state": "suspended"}]
    sensors.gpu_list_changed = True
    for _ in range(2):
        data = await provider.sample(1.0)
    assert data["history"]["gpu"][0] == 12
    assert data["history"]["gpu"][-1] is None  # carte endormie : pas de mesure, pas un zéro faux


async def test_expensive_reads_happen_less_often_than_cheap_ones():
    sensors = FakeSensors()
    provider = SystemProvider(sensors)
    for _ in range(30):
        await provider.sample(1.0)
    assert sensors.reads["partitions"] == 2  # secondes 0 et 15
    assert sensors.reads["gpus"] == 15  # une seconde sur deux
    assert sensors.reads["temps"] == 15
    assert sensors.reads["battery"] == 6  # une seconde sur cinq


async def test_a_machine_without_battery_or_temperature_sensors_still_works():
    sensors = FakeSensors()
    sensors.battery_info = None
    sensors.temps = {}
    sensors.gpu_list = []
    data = await SystemProvider(sensors).sample(1.0)
    assert data["battery"] is None
    assert data["temps"] == {}
    assert data["cpu"]["temp_c"] is None
    assert data["gpus"] == []
    assert data["history"]["gpu"] == [None]


async def test_an_empty_core_list_does_not_divide_by_zero():
    sensors = FakeSensors()
    sensors.cores = []
    sensors.mem["total"] = 0
    data = await SystemProvider(sensors).sample(1.0)
    assert data["cpu"]["percent"] == 0.0
    assert data["mem"]["percent"] == 0.0


# --- la boucle -----------------------------------------------------------------------------------


async def test_the_provider_publishes_repeatedly_and_primes_the_counters_once():
    sensors = FakeSensors()
    provider = SystemProvider(sensors, interval=0.01)
    published: list[dict] = []

    async def publish(data):
        published.append(data)
        return True

    task = asyncio.create_task(provider.run(publish))
    try:
        await eventually(lambda: len(published) >= 4)
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
    assert sensors.reads["primed"] == 1
    assert len(published[-1]["history"]["cpu"]) >= 4


async def test_a_failing_sensor_is_reported_without_stopping_the_provider():
    sensors = FakeSensors()
    provider = SystemProvider(sensors, interval=0.01)
    published: list[dict] = []
    calls = {"n": 0}
    original = sensors.memory

    def flaky():
        calls["n"] += 1
        if calls["n"] == 2:
            raise OSError("capteur absent")
        return original()

    sensors.memory = flaky

    async def publish(data):
        published.append(data)
        return True

    task = asyncio.create_task(provider.run(publish))
    try:
        await eventually(
            lambda: any("unavailable" in d for d in published) and "cpu" in published[-1]
        )
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
    assert any("unavailable" in d for d in published)
    assert "cpu" in published[-1]  # reprise après l'incident


async def test_restarting_the_provider_forgets_the_old_history():
    sensors = FakeSensors()
    provider = SystemProvider(sensors, interval=0.01)
    await provider.sample(1.0)
    await provider.sample(1.0)
    assert len(provider._history["cpu"]) == 2
    published: list[dict] = []

    async def publish(data):
        published.append(data)
        return True

    task = asyncio.create_task(provider.run(publish))
    try:
        await eventually(lambda: published)
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
    assert len(published[0]["history"]["cpu"]) == 1  # pas d'historique périmé après un arrêt


# --- vrais capteurs (ce que psutil donne ici) -------------------------------------------------------


async def test_the_real_sensors_give_a_well_formed_json_state():
    provider = SystemProvider()
    provider._sensors.prime()
    data = await provider.sample(1.0)
    assert 0 <= data["cpu"]["percent"] <= 100
    assert len(data["cpu"]["cores"]) >= 1
    assert data["mem"]["total"] > 0
    assert 0 <= data["mem"]["percent"] <= 100
    assert isinstance(data["disks"], list)
    assert isinstance(data["gpus"], list)
    json.dumps(data)


def test_real_network_counters_ignore_loopback_and_containers():
    names = Sensors().net_bytes()
    assert "lo" not in names
    assert not any(n.startswith(("docker", "veth", "virbr")) for n in names)
