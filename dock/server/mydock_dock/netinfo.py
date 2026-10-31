"""Renseignements réseau de ce Pi, pour les messages d'erreur, les Réglages et le diagnostic."""

from __future__ import annotations

import ipaddress
import socket

import psutil


def is_lan_candidate(address: str) -> bool:
    """Vrai pour une adresse IPv4 qui peut être celle d'un appareil du réseau local."""
    try:
        ip = ipaddress.IPv4Address(address)
    except ValueError:
        return False
    return not (ip.is_loopback or ip.is_link_local or ip.is_unspecified or ip.is_multicast)


def local_ipv4_addresses() -> list[str]:
    """Adresses IPv4 de ce Pi sur ses interfaces actives (sans boucle locale ni lien-local)."""
    try:
        stats = psutil.net_if_stats()
        found = {
            info.address
            for name, infos in psutil.net_if_addrs().items()
            if (stat := stats.get(name)) is not None and stat.isup
            for info in infos
            if info.family == socket.AF_INET and is_lan_candidate(info.address)
        }
    except OSError:
        return []
    return sorted(found, key=ipaddress.IPv4Address)


def local_address_towards(ip: str) -> str | None:
    """Adresse source que ce Pi utilise pour joindre `ip` : celle que le PC voit arriver.

    Un « connect » UDP n'envoie aucun paquet : le noyau choisit seulement l'itinéraire.
    """
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.connect((ip, 9))
            return str(sock.getsockname()[0])
    except OSError:
        return None
