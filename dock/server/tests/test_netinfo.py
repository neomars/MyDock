"""Renseignements réseau du Pi : adresses utilisables, adresse source vers le PC."""

import socket
from types import SimpleNamespace

import pytest

from mydock_dock import netinfo


@pytest.mark.parametrize(
    ("address", "expected"),
    [
        ("192.168.1.3", True),
        ("10.0.0.7", True),
        ("172.17.0.1", True),
        ("127.0.0.1", False),
        ("127.0.1.1", False),  # le nom de la machine dans /etc/hosts
        ("169.254.10.2", False),
        ("0.0.0.0", False),
        ("224.0.0.251", False),
        ("fe80::1", False),
        ("pas-une-adresse", False),
    ],
)
def test_which_addresses_can_belong_to_a_device_of_the_network(address, expected):
    assert netinfo.is_lan_candidate(address) is expected


def fake_psutil(monkeypatch, addrs: dict, up: dict) -> None:
    def addr(family, address):
        return SimpleNamespace(family=family, address=address)

    monkeypatch.setattr(
        netinfo.psutil,
        "net_if_addrs",
        lambda: {name: [addr(f, a) for f, a in infos] for name, infos in addrs.items()},
    )
    monkeypatch.setattr(
        netinfo.psutil,
        "net_if_stats",
        lambda: {name: SimpleNamespace(isup=is_up) for name, is_up in up.items()},
    )


def test_the_pi_lists_its_own_network_addresses(monkeypatch):
    fake_psutil(
        monkeypatch,
        {
            "lo": [(socket.AF_INET, "127.0.0.1")],
            "eth0": [(socket.AF_INET, "192.168.1.60"), (socket.AF_INET6, "fe80::1")],
            "wlan0": [(socket.AF_INET, "192.168.1.9")],
            "usb0": [(socket.AF_INET, "169.254.4.4")],
            "docker0": [(socket.AF_INET, "172.17.0.1")],
        },
        {"lo": True, "eth0": True, "wlan0": True, "usb0": True, "docker0": False},
    )
    assert netinfo.local_ipv4_addresses() == ["192.168.1.9", "192.168.1.60"]


def test_addresses_are_sorted_numerically_not_alphabetically(monkeypatch):
    fake_psutil(
        monkeypatch,
        {"eth0": [(socket.AF_INET, "192.168.1.100")], "wlan0": [(socket.AF_INET, "192.168.1.60")]},
        {"eth0": True, "wlan0": True},
    )
    assert netinfo.local_ipv4_addresses() == ["192.168.1.60", "192.168.1.100"]


def test_a_failing_system_call_gives_an_empty_list(monkeypatch):
    def boom():
        raise OSError("pas de réseau")

    monkeypatch.setattr(netinfo.psutil, "net_if_stats", boom)
    assert netinfo.local_ipv4_addresses() == []


def test_the_source_address_towards_the_pc_is_the_one_the_kernel_picks():
    assert netinfo.local_address_towards("127.0.0.1") == "127.0.0.1"


def test_no_route_means_no_source_address(monkeypatch):
    class Unroutable:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def connect(self, _address):
            raise OSError("Network is unreachable")

    monkeypatch.setattr(netinfo.socket, "socket", lambda *a, **k: Unroutable())
    assert netinfo.local_address_towards("203.0.113.5") is None
