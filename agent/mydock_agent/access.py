"""Contrôle d'accès de l'agent : qui a le droit de se connecter, et avec quel jeton.

Ordre des vérifications (voir server.py) :
  1. l'adresse du client doit être dans la liste autorisée, sinon la connexion est refusée
     sans un mot, avant toute authentification ;
  2. le jeton doit être le bon ; les échecs répétés d'une même adresse la bloquent un moment.
"""

from __future__ import annotations

import ipaddress
import logging
import socket
import time
from collections import deque
from collections.abc import Callable, Sequence

import psutil

from mydock_common.auth import tokens_equal

log = logging.getLogger(__name__)

LOOPBACK = ipaddress.ip_network("127.0.0.0/8")
_VIRTUAL_PREFIXES = (
    "docker",
    "br-",
    "veth",
    "virbr",
    "vmnet",
    "vboxnet",
    "lxcbr",
    "lxdbr",
    "tun",
    "tap",
)


def normalize_ip(raw: str | None) -> ipaddress.IPv4Address | None:
    """Adresse IPv4 du client (gère la forme ::ffff:a.b.c.d), ou None si ce n'en est pas une."""
    if not raw:
        return None
    try:
        address = ipaddress.ip_address(raw)
    except ValueError:
        return None
    if isinstance(address, ipaddress.IPv6Address):
        return address.ipv4_mapped
    return address


def local_lan_networks() -> list[ipaddress.IPv4Network]:
    """Réseaux IPv4 des interfaces physiques de ce PC (repli quand rien n'est configuré)."""
    networks: list[ipaddress.IPv4Network] = []
    for name, entries in psutil.net_if_addrs().items():
        if name == "lo" or name.startswith(_VIRTUAL_PREFIXES):
            continue
        for entry in entries:
            if entry.family != socket.AF_INET or not entry.netmask:
                continue
            try:
                network = ipaddress.ip_interface(f"{entry.address}/{entry.netmask}").network
            except ValueError:
                continue
            if not network.is_link_local and network not in networks:
                networks.append(network)
    return networks


class AccessGate:
    def __init__(
        self,
        token: str,
        allowed: Sequence[ipaddress.IPv4Network] = (),
        *,
        lan_networks: Callable[[], list[ipaddress.IPv4Network]] = local_lan_networks,
        max_failures: int = 8,
        window: float = 60.0,
        block: float = 60.0,
        clock: Callable[[], float] = time.monotonic,
    ):
        self._token = token
        self._allowed = tuple(allowed)
        self._lan_networks = lan_networks
        self._max_failures = max_failures
        self._window = window
        self._block = block
        self._clock = clock
        self._failures: dict[str, deque[float]] = {}
        self._blocked_until: dict[str, float] = {}
        self._lan_cache: tuple[float, list[ipaddress.IPv4Network]] = (-1e9, [])

    def _networks(self) -> Sequence[ipaddress.IPv4Network]:
        if self._allowed:
            return self._allowed
        now = self._clock()
        if now - self._lan_cache[0] > 60:
            self._lan_cache = (now, self._lan_networks())
        return self._lan_cache[1]

    def client_allowed(self, raw_ip: str | None) -> bool:
        address = normalize_ip(raw_ip)
        if address is None:
            return False
        if address in LOOPBACK:
            return True
        return any(address in network for network in self._networks())

    def is_blocked(self, raw_ip: str | None) -> bool:
        key = str(raw_ip)
        until = self._blocked_until.get(key)
        if until is None:
            return False
        if self._clock() >= until:
            del self._blocked_until[key]
            self._failures.pop(key, None)
            return False
        return True

    def authenticate(self, raw_ip: str | None, presented: str | None) -> bool:
        """Vérifie le jeton ; compte les échecs et bloque l'adresse si elle insiste."""
        key = str(raw_ip)
        if self.is_blocked(key):
            return False
        if tokens_equal(self._token, presented):
            self._failures.pop(key, None)
            return True
        now = self._clock()
        recent = self._failures.setdefault(key, deque())
        recent.append(now)
        while recent and now - recent[0] > self._window:
            recent.popleft()
        if len(recent) >= self._max_failures:
            self._blocked_until[key] = now + self._block
            log.warning(
                "Trop d'échecs d'authentification depuis %s : adresse bloquée %d s",
                key,
                self._block,
            )
        else:
            log.warning("Jeton refusé pour %s (%d/%d)", key, len(recent), self._max_failures)
        return False
