"""Thème « host » : identité du PC, session graphique et interfaces réseau.

Les adresses MAC des interfaces *physiques* servent au Wake-on-LAN : le dock les mémorise
tant que le PC est allumé pour pouvoir le réveiller quand il est éteint. Les interfaces
virtuelles (docker0, veth, ponts, VPN) sont ignorées.
"""

from __future__ import annotations

import asyncio
import os
import platform
import socket
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

import psutil

from mydock_agent import __version__
from mydock_agent.hub import Provider, Publish
from mydock_agent.session import detect_session
from mydock_common.protocol import PROTOCOL_VERSION

ZERO_MAC = "00:00:00:00:00:00"
RTF_GATEWAY = 0x2


def _read(path: Path) -> str:
    try:
        return path.read_text().strip()
    except (OSError, UnicodeDecodeError):
        return ""


def read_pretty_os_name(path: Path = Path("/etc/os-release")) -> str:
    for line in _read(path).splitlines():
        key, _, value = line.partition("=")
        if key == "PRETTY_NAME":
            return value.strip().strip('"').strip("'")
    return platform.system() or "Linux"


def default_route_interface(route_file: Path) -> str | None:
    """Interface de la route par défaut (plus petite métrique), lue dans /proc/net/route."""
    best: tuple[int, str] | None = None
    for line in _read(route_file).splitlines()[1:]:
        fields = line.split()
        if len(fields) < 7 or fields[1] != "00000000":
            continue
        try:
            flags, metric = int(fields[3], 16), int(fields[6])
        except ValueError:
            continue
        if flags & RTF_GATEWAY and (best is None or metric < best[0]):
            best = (metric, fields[0])
    return best[1] if best else None


def ipv4_addresses() -> dict[str, list[str]]:
    result: dict[str, list[str]] = {}
    for name, entries in psutil.net_if_addrs().items():
        addresses = [e.address for e in entries if e.family == socket.AF_INET]
        if addresses:
            result[name] = addresses
    return result


def list_interfaces(
    *,
    sys_root: Path = Path("/sys"),
    proc_root: Path = Path("/proc"),
    addresses: Mapping[str, list[str]] | None = None,
) -> list[dict[str, Any]]:
    """Interfaces réseau physiques, Ethernet ou Wi-Fi, triées par nom."""
    net = sys_root / "class" / "net"
    addresses = ipv4_addresses() if addresses is None else addresses
    default_if = default_route_interface(proc_root / "net" / "route")
    interfaces: list[dict[str, Any]] = []
    try:
        entries = sorted(net.iterdir(), key=lambda p: p.name)
    except OSError:
        return interfaces
    for entry in entries:
        name = entry.name
        if name == "lo" or "/devices/virtual/" in os.path.realpath(entry):
            continue
        mac = _read(entry / "address").lower()
        if not mac or mac == ZERO_MAC or _read(entry / "type") not in {"", "1"}:
            continue
        wifi = (entry / "wireless").exists() or (entry / "phy80211").exists()
        state = _read(entry / "operstate")
        carrier = _read(entry / "carrier") == "1"
        interfaces.append(
            {
                "name": name,
                "mac": mac,
                "kind": "wifi" if wifi else "ethernet",
                "up": state == "up" or (state == "unknown" and carrier),
                "ipv4": list(addresses.get(name, [])),
                "default": name == default_if,
            }
        )
    return interfaces


class HostProvider(Provider):
    topic = "host"
    lazy = False

    def __init__(
        self,
        *,
        refresh: float = 60.0,
        collect_interfaces: Callable[[], list[dict[str, Any]]] = list_interfaces,
        env: Mapping[str, str] | None = None,
        demo: bool = False,
    ):
        self._refresh = refresh
        self._collect_interfaces = collect_interfaces
        self._env = env
        self._demo = demo
        self._boot_time = int(psutil.boot_time())

    def collect(self) -> dict[str, Any]:
        session = detect_session(self._env)
        return {
            "hostname": socket.gethostname(),
            "os": read_pretty_os_name(),
            "kernel": platform.release(),
            "arch": platform.machine(),
            "python": platform.python_version(),
            "agent": {"version": __version__, "proto": PROTOCOL_VERSION, "demo": self._demo},
            "boot_time": self._boot_time,
            "session": session.to_dict(),
            "interfaces": self._collect_interfaces(),
        }

    async def run(self, publish: Publish) -> None:
        while True:
            await publish(self.collect())
            await asyncio.sleep(self._refresh)
