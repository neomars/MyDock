"""Wake-on-LAN : envoie le « magic packet » pour allumer le PC depuis le Pi.

Le paquet (6 × 0xFF puis 16 fois l'adresse MAC) est émis en broadcast UDP sur chaque réseau
IPv4 du Pi, depuis l'adresse de ce réseau, pour qu'il sorte par la bonne interface.
Le réveil d'un PC *éteint* n'est possible qu'en Ethernet (BIOS + carte réseau configurés) ;
en Wi-Fi, au mieux depuis la veille et selon la machine.
"""

from __future__ import annotations

import asyncio
import os
import socket
from dataclasses import dataclass
from typing import Any

import psutil

from mydock_dock.validation import normalize_mac

WOL_PORTS = (9, 7)
LIMITED_BROADCAST = "255.255.255.255"
TEST_TARGETS_ENV = "MYDOCK_WOL_TARGETS"  # « hôte:port,hôte:port » : réservé aux tests


def magic_packet(mac: str) -> bytes:
    raw = bytes.fromhex(normalize_mac(mac).replace(":", ""))
    return b"\xff" * 6 + raw * 16


@dataclass(frozen=True)
class Target:
    local_ip: str | None  # adresse source (choisit l'interface de sortie)
    destination: str
    port: int


def broadcast_targets(
    addresses: dict[str, list[Any]] | None = None, ports: tuple[int, ...] = WOL_PORTS
) -> list[Target]:
    override = os.environ.get(TEST_TARGETS_ENV)
    if override:
        targets = []
        for item in override.split(","):
            host, _, port = item.strip().rpartition(":")
            targets.append(Target(None, host, int(port)))
        return targets

    addresses = psutil.net_if_addrs() if addresses is None else addresses
    targets: list[Target] = []
    seen: set[Target] = set()
    for name, entries in addresses.items():
        if name == "lo":
            continue
        for entry in entries:
            if entry.family != socket.AF_INET or not entry.address:
                continue
            if entry.address.startswith(("127.", "169.254.")):
                continue
            for destination in {entry.broadcast, LIMITED_BROADCAST} - {None}:
                for port in ports:
                    target = Target(entry.address, destination, port)
                    if target not in seen:
                        seen.add(target)
                        targets.append(target)
    if not targets:  # aucune interface connue : au moins essayer l'envoi par défaut
        targets = [Target(None, LIMITED_BROADCAST, port) for port in ports]
    return targets


def _send(packet: bytes, target: Target) -> dict[str, Any]:
    result: dict[str, Any] = {"via": target.local_ip, "to": target.destination, "port": target.port}
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
            if target.local_ip:
                sock.bind((target.local_ip, 0))
            sock.sendto(packet, (target.destination, target.port))
        result["ok"] = True
    except OSError as exc:
        result.update(ok=False, error=exc.strerror or str(exc))
    return result


async def send_magic_packet(mac: str, targets: list[Target] | None = None) -> list[dict[str, Any]]:
    """Envoie le paquet sur toutes les cibles ; retourne le détail (ok / erreur) de chacune."""
    packet = magic_packet(mac)
    targets = broadcast_targets() if targets is None else targets
    return list(await asyncio.gather(*(asyncio.to_thread(_send, packet, t) for t in targets)))
