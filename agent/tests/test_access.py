import ipaddress

import pytest

from mydock_agent.access import AccessGate, local_lan_networks, normalize_ip

TOKEN = "t" * 40
PI = ipaddress.ip_network("192.0.2.50/32")


class FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def gate(allowed=(PI,), clock=None, **kwargs):
    return AccessGate(TOKEN, allowed, lan_networks=lambda: [], clock=clock or FakeClock(), **kwargs)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("192.0.2.50", "192.0.2.50"),
        ("::ffff:192.0.2.50", "192.0.2.50"),
        ("2001:db8::1", None),
        ("pas-une-ip", None),
        ("testclient", None),
        ("", None),
        (None, None),
    ],
)
def test_normalize_ip(raw, expected):
    result = normalize_ip(raw)
    assert (str(result) if result else None) == expected


def test_only_listed_clients_and_loopback_are_allowed():
    g = gate()
    assert g.client_allowed("192.0.2.50")
    assert g.client_allowed("::ffff:192.0.2.50")
    assert g.client_allowed("127.0.0.1")
    assert not g.client_allowed("192.0.2.51")
    assert not g.client_allowed("10.0.0.1")
    assert not g.client_allowed("2001:db8::1")
    assert not g.client_allowed(None)
    assert not g.client_allowed("")


def test_cidr_networks():
    g = gate(allowed=(ipaddress.ip_network("198.51.100.0/24"),))
    assert g.client_allowed("198.51.100.7")
    assert not g.client_allowed("198.51.101.7")


def test_empty_allow_list_falls_back_to_local_networks():
    lan = ipaddress.ip_network("203.0.113.0/24")
    g = AccessGate(TOKEN, (), lan_networks=lambda: [lan])
    assert g.client_allowed("203.0.113.9")
    assert not g.client_allowed("192.0.2.50")


def test_local_lan_networks_skip_virtual_and_loopback(monkeypatch):
    import socket
    from types import SimpleNamespace

    def addr(address, netmask):
        return SimpleNamespace(family=socket.AF_INET, address=address, netmask=netmask)

    monkeypatch.setattr(
        "mydock_agent.access.psutil.net_if_addrs",
        lambda: {
            "lo": [addr("127.0.0.1", "255.0.0.0")],
            "docker0": [addr("172.17.0.1", "255.255.0.0")],
            "br-1234": [addr("172.18.0.1", "255.255.0.0")],
            "wlp0s20f3": [addr("192.0.2.20", "255.255.255.0")],
            "enx0": [addr("169.254.3.4", "255.255.0.0")],
        },
    )
    assert local_lan_networks() == [ipaddress.ip_network("192.0.2.0/24")]


def test_authenticate_accepts_only_the_right_token():
    g = gate()
    assert g.authenticate("192.0.2.50", TOKEN)
    assert not g.authenticate("192.0.2.50", TOKEN + "x")
    assert not g.authenticate("192.0.2.50", None)
    assert not g.authenticate("192.0.2.50", "")


def test_repeated_failures_block_the_address_even_with_the_good_token():
    clock = FakeClock()
    g = gate(clock=clock, max_failures=3, window=60, block=300)
    for _ in range(3):
        assert not g.authenticate("192.0.2.50", "mauvais")
    assert g.is_blocked("192.0.2.50")
    assert not g.authenticate("192.0.2.50", TOKEN)  # bloqué : même le bon jeton est refusé
    assert not g.is_blocked("192.0.2.99")  # les autres adresses ne sont pas touchées
    clock.now += 301
    assert not g.is_blocked("192.0.2.50")
    assert g.authenticate("192.0.2.50", TOKEN)


def test_failures_outside_the_window_do_not_add_up():
    clock = FakeClock()
    g = gate(clock=clock, max_failures=3, window=60)
    for _ in range(5):
        assert not g.authenticate("192.0.2.50", "mauvais")
        clock.now += 61
    assert not g.is_blocked("192.0.2.50")


def test_success_resets_the_failure_count():
    clock = FakeClock()
    g = gate(clock=clock, max_failures=3)
    g.authenticate("192.0.2.50", "mauvais")
    g.authenticate("192.0.2.50", "mauvais")
    assert g.authenticate("192.0.2.50", TOKEN)
    g.authenticate("192.0.2.50", "mauvais")
    g.authenticate("192.0.2.50", "mauvais")
    assert not g.is_blocked("192.0.2.50")
