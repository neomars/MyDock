"""Cartes graphiques : lecture sans réveiller la carte NVIDIA endormie (faux sysfs, fausse NVML)."""

import json
from pathlib import Path

import pytest

from mydock_agent.collectors import gpu

PCI_IDS = """\
# base de test
8086  Intel Corporation
\ta788  Raptor Lake-S GT1 [UHD Graphics 770]
\t\t17aa 22cd  Sous-système qui ne doit pas être pris pour un appareil
10de  NVIDIA Corporation
\t28b8  AD104GLM [RTX 4000 Ada Generation Laptop GPU]
\t2684  AD102 [GeForce RTX 4090]
1002  Advanced Micro Devices, Inc. [AMD/ATI]
\t744c  Navi 31 [Radeon RX 7900 XT/7900 XTX/7900M]
"""


@pytest.fixture(autouse=True)
def fresh_caches():
    gpu.reset_caches()
    yield
    gpu.reset_caches()


@pytest.fixture
def sysfs(tmp_path):
    return Sysfs(tmp_path)


class Sysfs:
    """Un faux /sys avec les cartes qu'on lui ajoute."""

    def __init__(self, root: Path):
        self.pci = root / "bus" / "pci" / "devices"
        self.drm = root / "class" / "drm"
        self.pci.mkdir(parents=True)
        self.drm.mkdir(parents=True)
        self.pci_ids = root / "pci.ids"
        self.pci_ids.write_text(PCI_IDS)

    def add(
        self,
        bus_id: str,
        vendor: str,
        device: str,
        *,
        state: str = "active",
        cls: str = "0x030000",
        boot_vga: bool = False,
        card: str | None = None,
    ) -> Path:
        path = self.pci / bus_id
        (path / "power").mkdir(parents=True)
        (path / "class").write_text(cls + "\n")
        (path / "vendor").write_text(vendor + "\n")
        (path / "device").write_text(device + "\n")
        (path / "power" / "runtime_status").write_text(state + "\n")
        if boot_vga:
            (path / "boot_vga").write_text("1\n")
        if card:
            (self.drm / card).mkdir()
            (self.drm / card / "device").symlink_to(path)
        return path

    def read(self, **kw):
        return gpu.read_gpus(pci_root=self.pci, drm_root=self.drm, pci_ids=[self.pci_ids], **kw)


class FakeNvml:
    """Fausse libnvidia-ml : écrit dans les paramètres de sortie comme le ferait la vraie."""

    def __init__(self, *, fail_on: str | None = None):
        self.calls: list[object] = []
        self.fail_on = fail_on

    def _code(self, name: str) -> int:
        self.calls.append(name)
        return 7 if self.fail_on == name else 0

    def nvmlInit_v2(self):
        return self._code("init")

    def nvmlShutdown(self):
        return self._code("shutdown")

    def nvmlDeviceGetHandleByPciBusId_v2(self, bus_id, handle_ref):
        self.calls.append(("bus", bus_id))
        return self._code("handle")

    def nvmlDeviceGetName(self, handle, buffer, length):
        buffer.value = b"NVIDIA RTX 4000 Ada Generation Laptop GPU"
        return self._code("name")

    def nvmlDeviceGetUtilizationRates(self, handle, ref):
        ref._obj.gpu, ref._obj.memory = 37, 12
        return self._code("util")

    def nvmlDeviceGetMemoryInfo(self, handle, ref):
        ref._obj.total, ref._obj.free, ref._obj.used = 12 << 30, 10 << 30, 2 << 30
        return self._code("memory")

    def nvmlDeviceGetTemperature(self, handle, sensor, ref):
        ref._obj.value = 51
        return self._code("temp")

    def nvmlDeviceGetPowerUsage(self, handle, ref):
        ref._obj.value = 23_500
        return self._code("power")

    def nvmlDeviceGetClockInfo(self, handle, kind, ref):
        ref._obj.value = 1980
        return self._code("clock")


# --- noms ----------------------------------------------------------------------------------------


def test_names_come_from_pci_ids_without_touching_the_hardware(tmp_path):
    ids = tmp_path / "pci.ids"
    ids.write_text(PCI_IDS)
    assert gpu.lookup_pci_name("0x10de", "0x28b8", [ids]) == (
        "AD104GLM [RTX 4000 Ada Generation Laptop GPU]"
    )
    assert gpu.lookup_pci_name("0x8086", "0xa788", [ids]) == "Raptor Lake-S GT1 [UHD Graphics 770]"


def test_an_unknown_device_does_not_borrow_the_next_vendors_names(tmp_path):
    ids = tmp_path / "pci.ids"
    ids.write_text(PCI_IDS)
    assert gpu.lookup_pci_name("0x8086", "0x2684", [ids]) is None  # 2684 est chez NVIDIA
    assert gpu.lookup_pci_name("0x1234", "0x0001", [ids]) is None
    assert gpu.lookup_pci_name("0x8086", "0x17aa", [ids]) is None  # une ligne de sous-système


def test_a_missing_pci_ids_file_is_not_an_error(tmp_path):
    assert gpu.lookup_pci_name("0x10de", "0x28b8", [tmp_path / "absent"]) is None


@pytest.mark.parametrize(
    ("vendor", "raw", "expected"),
    [
        (
            "nvidia",
            "AD104GLM [RTX 4000 Ada Generation Laptop GPU]",
            "NVIDIA RTX 4000 Ada Generation Laptop GPU",
        ),
        ("intel", "Raptor Lake-S GT1 [UHD Graphics 770]", "Intel UHD Graphics 770"),
        (
            "amd",
            "Navi 31 [Radeon RX 7900 XT/7900 XTX/7900M]",
            "AMD Radeon RX 7900 XT/7900 XTX/7900M",
        ),
        ("nvidia", "NVIDIA Something", "NVIDIA Something"),  # pas de préfixe en double
        ("other", "Machin [Truc]", "Truc"),
    ],
)
def test_names_are_made_readable(vendor, raw, expected):
    assert gpu.clean_gpu_name(vendor, raw) == expected


# --- découverte ----------------------------------------------------------------------------------


def test_only_display_controllers_are_listed(sysfs):
    sysfs.add("0000:00:02.0", "0x8086", "0xa788", boot_vga=True)
    sysfs.add("0000:01:00.0", "0x10de", "0x28b8", cls="0x030200", state="suspended")
    sysfs.add("0000:00:1f.3", "0x8086", "0x7a50", cls="0x040300")  # audio
    sysfs.add("0000:00:00.0", "0x8086", "0xa700", cls="0x060000")  # pont hôte
    found = gpu.discover(sysfs.pci)
    assert [(g.bus_id, g.vendor, g.state, g.boot_vga) for g in found] == [
        ("0000:00:02.0", "intel", "active", True),
        ("0000:01:00.0", "nvidia", "suspended", False),
    ]


def test_a_machine_without_pci_sysfs_has_no_gpu(tmp_path):
    assert gpu.discover(tmp_path / "absent") == []


# --- NVIDIA ----------------------------------------------------------------------------------------


def test_a_sleeping_nvidia_card_is_never_queried(sysfs):
    sysfs.add("0000:01:00.0", "0x10de", "0x28b8", cls="0x030200", state="suspended")
    nvml = FakeNvml()
    [card] = sysfs.read(nvml=gpu.NvmlReader(library=nvml))
    assert nvml.calls == []  # ni nvmlInit ni rien : la carte ne doit pas être réveillée
    assert card["state"] == "suspended"
    assert card["name"] == "NVIDIA RTX 4000 Ada Generation Laptop GPU"  # lu dans pci.ids
    assert card["util"] is None


@pytest.mark.parametrize("state", ["suspending", "resuming"])
def test_a_card_changing_power_state_is_left_alone_too(sysfs, state):
    sysfs.add("0000:01:00.0", "0x10de", "0x28b8", state=state)
    nvml = FakeNvml()
    [card] = sysfs.read(nvml=gpu.NvmlReader(library=nvml))
    assert nvml.calls == []
    assert card["state"] == "suspended"


def test_an_awake_nvidia_card_is_measured_and_released_right_away(sysfs):
    sysfs.add("0000:01:00.0", "0x10de", "0x28b8", state="active")
    nvml = FakeNvml()
    [card] = sysfs.read(nvml=gpu.NvmlReader(library=nvml))
    assert card["state"] == "active"
    assert (card["util"], card["temp_c"], card["power_w"], card["freq_mhz"]) == (37, 51, 23.5, 1980)
    assert (card["mem_used"], card["mem_total"]) == (2 << 30, 12 << 30)
    # NVML est ouverte puis refermée : aucune référence ne retient la carte éveillée.
    assert nvml.calls[0] == "init"
    assert nvml.calls[-1] == "shutdown"
    assert ("bus", b"00000000:01:00.0") in nvml.calls
    json.dumps(card)  # sérialisable tel quel


def test_the_name_given_by_nvml_is_kept_for_when_the_card_sleeps(sysfs):
    path = sysfs.add("0000:01:00.0", "0x10de", "0x28b8", state="active")
    sysfs.pci_ids.write_text("")  # plus de base de noms
    reader = gpu.NvmlReader(library=FakeNvml())
    assert sysfs.read(nvml=reader)[0]["name"] == "NVIDIA RTX 4000 Ada Generation Laptop GPU"
    (path / "power" / "runtime_status").write_text("suspended\n")
    [card] = sysfs.read(nvml=reader)
    assert card["name"] == "NVIDIA RTX 4000 Ada Generation Laptop GPU"
    assert card["state"] == "suspended"


def test_a_failing_nvml_call_is_reported_and_still_releases_the_library(sysfs):
    sysfs.add("0000:01:00.0", "0x10de", "0x28b8", state="active")
    nvml = FakeNvml(fail_on="util")
    [card] = sysfs.read(nvml=gpu.NvmlReader(library=nvml))
    assert card["util"] is None
    assert "utilisation" in card["note"]
    assert nvml.calls[-1] == "shutdown"


def test_optional_measures_may_be_unsupported(sysfs):
    sysfs.add("0000:01:00.0", "0x10de", "0x28b8", state="active")
    [card] = sysfs.read(nvml=gpu.NvmlReader(library=FakeNvml(fail_on="power")))
    assert card["util"] == 37
    assert card["power_w"] is None


def test_without_the_nvidia_driver_the_card_is_still_listed_with_an_explanation(sysfs):
    sysfs.add("0000:01:00.0", "0x10de", "0x28b8", state="active")
    reader = gpu.NvmlReader(names=("libnvidia-ml-inexistante.so.9",))
    [card] = sysfs.read(nvml=reader)
    assert card["util"] is None
    assert "NVML absente" in card["note"]
    assert reader.missing


def test_the_nvml_struct_layouts_match_the_c_headers():
    import ctypes

    assert ctypes.sizeof(gpu._Utilization) == 8  # deux unsigned int
    assert ctypes.sizeof(gpu._Memory) == 24  # trois unsigned long long


# --- Intel et AMD -----------------------------------------------------------------------------------


def test_the_intel_card_reports_its_frequency(sysfs):
    path = sysfs.add("0000:00:02.0", "0x8086", "0xa788", card="card0", boot_vga=True)
    sysfs.drm.joinpath("card0-HDMI-A-1").mkdir()  # une sortie vidéo n'est pas une carte
    (sysfs.drm / "card0" / "gt_cur_freq_mhz").write_text("300\n")
    (sysfs.drm / "card0" / "gt_act_freq_mhz").write_text("450\n")
    [card] = sysfs.read()
    assert card["name"] == "Intel UHD Graphics 770"
    assert card["freq_mhz"] == 450  # la fréquence réelle prime sur la demandée
    assert card["util"] is None  # pas de taux d'occupation sans privilèges
    assert path.exists()


def test_a_recent_intel_driver_layout_is_understood(sysfs):
    sysfs.add("0000:00:02.0", "0x8086", "0xa788", card="card0")
    gt = sysfs.drm / "card0" / "gt" / "gt0"
    gt.mkdir(parents=True)
    (gt / "rps_cur_freq_mhz").write_text("1100\n")
    assert sysfs.read()[0]["freq_mhz"] == 1100


def test_the_amd_card_reports_load_memory_temperature_and_power(sysfs):
    sysfs.add("0000:03:00.0", "0x1002", "0x744c", card="card1")
    device = sysfs.drm / "card1" / "device"
    (device / "hwmon" / "hwmon4").mkdir(parents=True)
    (device / "gpu_busy_percent").write_text("63\n")
    (device / "mem_info_vram_used").write_text(str(3 << 30) + "\n")
    (device / "mem_info_vram_total").write_text(str(20 << 30) + "\n")
    (device / "hwmon" / "hwmon4" / "temp1_input").write_text("58000\n")
    (device / "hwmon" / "hwmon4" / "power1_average").write_text("112500000\n")
    [card] = sysfs.read()
    assert (card["util"], card["temp_c"], card["power_w"]) == (63, 58, 112.5)
    assert (card["mem_used"], card["mem_total"]) == (3 << 30, 20 << 30)


def test_a_sleeping_amd_card_is_not_read_either(sysfs):
    sysfs.add("0000:03:00.0", "0x1002", "0x744c", card="card1", state="suspended")
    (sysfs.drm / "card1" / "device" / "gpu_busy_percent").write_text("63\n")
    [card] = sysfs.read()
    assert card["state"] == "suspended"
    assert card["util"] is None


# --- le cas de l'utilisateur : portable hybride ------------------------------------------------------


def test_hybrid_laptop_with_a_sleeping_nvidia_card(sysfs):
    sysfs.add("0000:00:02.0", "0x8086", "0xa788", card="card0", boot_vga=True)
    (sysfs.drm / "card0" / "gt_cur_freq_mhz").write_text("350\n")
    sysfs.add("0000:01:00.0", "0x10de", "0x28b8", cls="0x030200", state="suspended")
    nvml = FakeNvml()
    cards = sysfs.read(nvml=gpu.NvmlReader(library=nvml))
    assert [(c["vendor"], c["state"]) for c in cards] == [
        ("intel", "active"),
        ("nvidia", "suspended"),
    ]
    assert nvml.calls == []
    json.dumps(cards)
