"""Cartes graphiques, lues sans réveiller une carte NVIDIA endormie.

Sur un portable hybride (Intel + NVIDIA), la carte dédiée dort presque tout le temps pour économiser
la batterie, et la moindre ouverture de /dev/nvidia* (nvidia-smi, NVML…) la réveille et l'empêche
de se rendormir. On procède donc dans cet ordre :

  1. les cartes se découvrent dans /sys/bus/pci (identifiants déjà en mémoire du noyau : aucun
     accès au matériel) et leur nom vient de pci.ids ;
  2. l'état d'alimentation (power/runtime_status) se lit sans réveiller la carte ;
  3. NVML n'est interrogée que si la carte est DÉJÀ active (un jeu, un calcul… s'en sert) ; sinon
     on affiche « en veille » ;
  4. Intel et AMD se lisent directement dans sysfs (fréquence, taux d'occupation, mémoire).
"""

from __future__ import annotations

import ctypes
import logging
import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

PCI_ROOT = Path("/sys/bus/pci/devices")
DRM_ROOT = Path("/sys/class/drm")
PCI_IDS = (Path("/usr/share/misc/pci.ids"), Path("/usr/share/hwdata/pci.ids"))
VENDORS = {"0x10de": "nvidia", "0x8086": "intel", "0x1002": "amd"}
VENDOR_PREFIX = {"nvidia": "NVIDIA", "intel": "Intel", "amd": "AMD"}
DISPLAY_CLASS = "0x03"  # contrôleurs d'affichage : VGA (0300), 3D (0302), autres (0380)
#: États d'alimentation d'une carte endormie (ou en train de s'endormir ou de se réveiller) : y
#: toucher, même par un simple fichier sysfs (AMD) ou NVML (NVIDIA), la réveillerait.
SLEEPING_STATES = frozenset({"suspended", "suspending", "resuming"})


def _read(path: Path) -> str:
    try:
        return path.read_text().strip()
    except (OSError, UnicodeDecodeError):
        return ""


def _number(path: Path) -> int | None:
    text = _read(path)
    try:
        return int(text)
    except ValueError:
        return None


# --- noms (pci.ids) ---------------------------------------------------------------------------


def clean_gpu_name(vendor: str, raw: str) -> str:
    """« AD104GLM [RTX 4000 Ada Generation Laptop GPU] » -> « NVIDIA RTX 4000 Ada … »."""
    bracket = re.search(r"\[([^\]]+)\]", raw)
    name = (bracket.group(1) if bracket else raw).strip()
    prefix = VENDOR_PREFIX.get(vendor, "")
    if prefix and not name.lower().startswith(prefix.lower()):
        name = f"{prefix} {name}"
    return name


def lookup_pci_name(
    vendor_id: str, device_id: str, sources: Iterable[Path] = PCI_IDS
) -> str | None:
    """Nom lisible d'un périphérique PCI dans la base pci.ids (sans accéder au matériel)."""
    vendor = vendor_id.lower().removeprefix("0x")
    device = device_id.lower().removeprefix("0x")
    for source in sources:
        try:
            handle = source.open(encoding="utf-8", errors="replace")
        except OSError:
            continue
        with handle:
            in_vendor = False
            for line in handle:
                if line.startswith("#") or not line.strip():
                    continue
                if not line.startswith("\t"):
                    if in_vendor:
                        return None  # fabricant suivant : l'appareil n'existe pas
                    in_vendor = line[:4].lower() == vendor
                elif in_vendor and not line.startswith("\t\t") and line[1:5].lower() == device:
                    return line[5:].strip()
    return None


# --- NVML (carte NVIDIA, seulement si elle est déjà éveillée) --------------------------------


class NvmlError(Exception):
    pass


class _Utilization(ctypes.Structure):
    _fields_ = [("gpu", ctypes.c_uint), ("memory", ctypes.c_uint)]


class _Memory(ctypes.Structure):
    _fields_ = [
        ("total", ctypes.c_ulonglong),
        ("free", ctypes.c_ulonglong),
        ("used", ctypes.c_ulonglong),
    ]


NVML_TEMPERATURE_GPU = 0
NVML_CLOCK_GRAPHICS = 0


class NvmlReader:
    """Lecture d'une carte NVIDIA par NVML (libnvidia-ml.so.1, livrée avec le pilote).

    Chaque lecture ouvre et referme NVML : entre deux lectures l'agent ne tient aucune référence
    sur la carte, qui peut donc s'endormir dès que les autres programmes la lâchent.
    """

    def __init__(self, library: Any | None = None, names: tuple[str, ...] = ("libnvidia-ml.so.1",)):
        self._library = library
        self._names = names
        self.missing = False

    def _lib(self) -> Any:
        if self._library is None:
            for name in self._names:
                try:
                    self._library = ctypes.CDLL(name)
                    break
                except OSError:
                    continue
            else:
                self.missing = True
                raise NvmlError("Bibliothèque NVML absente (pilote NVIDIA propriétaire installé ?)")
        return self._library

    @staticmethod
    def _check(code: int, what: str) -> None:
        if code != 0:
            raise NvmlError(f"NVML : {what} a échoué (code {code})")

    def read(self, bus_id: str) -> dict[str, Any]:
        lib = self._lib()
        self._check(lib.nvmlInit_v2(), "nvmlInit")
        try:
            handle = ctypes.c_void_p()
            domain, rest = bus_id.split(":", 1)
            self._check(
                lib.nvmlDeviceGetHandleByPciBusId_v2(
                    f"{int(domain, 16):08x}:{rest}".encode(), ctypes.byref(handle)
                ),
                "nvmlDeviceGetHandleByPciBusId",
            )
            result: dict[str, Any] = {}
            name = ctypes.create_string_buffer(96)
            if lib.nvmlDeviceGetName(handle, name, 96) == 0:
                result["name"] = name.value.decode(errors="replace")
            utilization = _Utilization()
            self._check(
                lib.nvmlDeviceGetUtilizationRates(handle, ctypes.byref(utilization)), "utilisation"
            )
            result["util"] = int(utilization.gpu)
            memory = _Memory()
            if lib.nvmlDeviceGetMemoryInfo(handle, ctypes.byref(memory)) == 0:
                result["mem_used"], result["mem_total"] = int(memory.used), int(memory.total)
            temp = ctypes.c_uint()
            if lib.nvmlDeviceGetTemperature(handle, NVML_TEMPERATURE_GPU, ctypes.byref(temp)) == 0:
                result["temp_c"] = int(temp.value)
            power = ctypes.c_uint()
            if lib.nvmlDeviceGetPowerUsage(handle, ctypes.byref(power)) == 0:
                result["power_w"] = round(power.value / 1000, 1)
            clock = ctypes.c_uint()
            if lib.nvmlDeviceGetClockInfo(handle, NVML_CLOCK_GRAPHICS, ctypes.byref(clock)) == 0:
                result["freq_mhz"] = int(clock.value)
            return result
        finally:
            lib.nvmlShutdown()


# --- découverte et lecture --------------------------------------------------------------------


@dataclass(frozen=True)
class PciGpu:
    bus_id: str  # « 0000:01:00.0 »
    vendor: str  # nvidia | intel | amd | other
    vendor_id: str
    device_id: str
    state: str  # active | suspended | suspending | resuming | unsupported | unknown
    boot_vga: bool
    path: Path


def discover(pci_root: Path = PCI_ROOT) -> list[PciGpu]:
    """Cartes graphiques du PC, sans toucher au matériel."""
    try:
        entries = sorted(pci_root.iterdir())
    except OSError:
        return []
    found = []
    for entry in entries:
        if not _read(entry / "class").startswith(DISPLAY_CLASS):
            continue
        vendor_id = _read(entry / "vendor").lower()
        found.append(
            PciGpu(
                bus_id=entry.name,
                vendor=VENDORS.get(vendor_id, "other"),
                vendor_id=vendor_id,
                device_id=_read(entry / "device").lower(),
                state=_read(entry / "power" / "runtime_status") or "unknown",
                boot_vga=_read(entry / "boot_vga") == "1",
                path=entry,
            )
        )
    return found


def card_directory(gpu: PciGpu, drm_root: Path = DRM_ROOT) -> Path | None:
    """Dossier /sys/class/drm/cardN de cette carte."""
    try:
        cards = sorted(drm_root.glob("card[0-9]*"))
    except OSError:
        return None
    for card in cards:
        if card.name.count("-"):  # cardN-HDMI-A-1 : une sortie, pas la carte
            continue
        try:
            if (card / "device").resolve().name == gpu.bus_id:
                return card
        except OSError:
            continue
    return None


def _first_number(card: Path, *candidates: str) -> int | None:
    for relative in candidates:
        value = _number(card / relative)
        if value is not None:
            return value
    return None


def read_intel(card: Path | None) -> dict[str, Any]:
    if card is None:
        return {}
    freq = _first_number(
        card,
        "gt_act_freq_mhz",
        "gt_cur_freq_mhz",
        "gt/gt0/rps_act_freq_mhz",
        "gt/gt0/rps_cur_freq_mhz",
        "device/tile0/gt0/freq0/cur_freq",
    )
    return {"freq_mhz": freq} if freq is not None else {}


def read_amd(card: Path | None) -> dict[str, Any]:
    if card is None:
        return {}
    device = card / "device"
    result: dict[str, Any] = {}
    busy = _number(device / "gpu_busy_percent")
    if busy is not None:
        result["util"] = busy
    used, total = _number(device / "mem_info_vram_used"), _number(device / "mem_info_vram_total")
    if used is not None and total:
        result["mem_used"], result["mem_total"] = used, total
    for hwmon in sorted(device.glob("hwmon/hwmon*")):
        temp = _number(hwmon / "temp1_input")
        if temp is not None:
            result["temp_c"] = round(temp / 1000)
        power = _number(hwmon / "power1_average") or _number(hwmon / "power1_input")
        if power is not None:
            result["power_w"] = round(power / 1_000_000, 1)
        break
    return result


class _Cache:
    """Noms déjà connus : une carte endormie garde son nom entre deux lectures."""

    names: dict[str, str] = {}  # noqa: RUF012 - cache de module volontaire


def read_gpus(
    *,
    pci_root: Path = PCI_ROOT,
    drm_root: Path = DRM_ROOT,
    pci_ids: Iterable[Path] = PCI_IDS,
    nvml: NvmlReader | None = None,
    reader: Callable[[PciGpu], dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Une entrée par carte graphique : nom, état d'alimentation et, si lisible, charge et mesures."""
    nvml = nvml or _default_nvml()
    gpus = []
    for gpu in discover(pci_root):
        name = _Cache.names.get(gpu.bus_id) or clean_gpu_name(
            gpu.vendor, lookup_pci_name(gpu.vendor_id, gpu.device_id, pci_ids) or "Carte graphique"
        )
        sleeping = gpu.state in SLEEPING_STATES
        entry: dict[str, Any] = {
            "id": gpu.bus_id,
            "vendor": gpu.vendor,
            "name": name,
            "state": "suspended" if sleeping else "active",
            "util": None,
            "mem_used": None,
            "mem_total": None,
            "temp_c": None,
            "power_w": None,
            "freq_mhz": None,
        }
        measures: dict[str, Any] = {}
        if sleeping:
            pass  # elle dort : on n'y touche surtout pas
        elif reader is not None:
            measures = reader(gpu)
        elif gpu.vendor == "nvidia":
            try:
                measures = nvml.read(gpu.bus_id)
            except NvmlError as exc:
                entry["note"] = str(exc)
            except Exception:
                log.exception("Lecture NVML impossible")
                entry["note"] = "Lecture de la carte NVIDIA impossible."
        elif gpu.vendor == "intel":
            measures = read_intel(card_directory(gpu, drm_root))
        elif gpu.vendor == "amd":
            measures = read_amd(card_directory(gpu, drm_root))
        if "name" in measures:
            _Cache.names[gpu.bus_id] = entry["name"] = measures.pop("name")
        entry.update(measures)
        gpus.append(entry)
    return gpus


_NVML: NvmlReader | None = None


def _default_nvml() -> NvmlReader:
    global _NVML
    if _NVML is None:
        _NVML = NvmlReader()
    return _NVML


def reset_caches() -> None:
    """Pour les tests : oublie les noms et la bibliothèque NVML chargées."""
    global _NVML
    _NVML = None
    _Cache.names = {}


__all__ = [
    "NvmlError",
    "NvmlReader",
    "PciGpu",
    "clean_gpu_name",
    "discover",
    "lookup_pci_name",
    "read_amd",
    "read_gpus",
    "read_intel",
    "reset_caches",
]
