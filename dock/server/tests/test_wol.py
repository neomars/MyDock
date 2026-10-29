import asyncio
import socket
from types import SimpleNamespace

import pytest

from mydock_dock.testing import start_udp_sink
from mydock_dock.validation import ValidationFailure
from mydock_dock.wol import (
    LIMITED_BROADCAST,
    TEST_TARGETS_ENV,
    Target,
    broadcast_targets,
    magic_packet,
    send_magic_packet,
)

MAC = "02:00:5e:10:00:01"


def addr(address, broadcast=None):
    return SimpleNamespace(family=socket.AF_INET, address=address, broadcast=broadcast)


def test_magic_packet_layout():
    packet = magic_packet(MAC)
    assert len(packet) == 102
    assert packet[:6] == b"\xff" * 6
    assert packet[6:] == bytes.fromhex("02005e100001") * 16


def test_magic_packet_accepts_any_mac_notation_and_rejects_garbage():
    assert magic_packet("02-00-5E-10-00-01") == magic_packet(MAC)
    with pytest.raises(ValidationFailure):
        magic_packet("pas une mac")


def test_targets_cover_every_lan_with_its_own_source_address():
    targets = broadcast_targets(
        {
            "lo": [addr("127.0.0.1")],
            "eth0": [addr("192.0.2.50", "192.0.2.255")],
            "wlan0": [addr("198.51.100.7", "198.51.100.255"), addr("169.254.1.1")],
            "ipv6": [SimpleNamespace(family=socket.AF_INET6, address="fe80::1", broadcast=None)],
        },
        ports=(9,),
    )
    assert set(targets) == {
        Target("192.0.2.50", "192.0.2.255", 9),
        Target("192.0.2.50", LIMITED_BROADCAST, 9),
        Target("198.51.100.7", "198.51.100.255", 9),
        Target("198.51.100.7", LIMITED_BROADCAST, 9),
    }


def test_targets_fall_back_to_the_limited_broadcast():
    assert broadcast_targets({"lo": [addr("127.0.0.1")]}, ports=(9, 7)) == [
        Target(None, LIMITED_BROADCAST, 9),
        Target(None, LIMITED_BROADCAST, 7),
    ]


def test_test_override_replaces_real_targets(monkeypatch):
    monkeypatch.setenv(TEST_TARGETS_ENV, "127.0.0.1:4000, 127.0.0.2:4001")
    assert broadcast_targets() == [Target(None, "127.0.0.1", 4000), Target(None, "127.0.0.2", 4001)]


async def test_packet_really_leaves_over_udp():
    sink = await start_udp_sink()
    results = await send_magic_packet(MAC, [Target(None, "127.0.0.1", sink.port)])
    for _ in range(50):
        if sink.packets:
            break
        await asyncio.sleep(0.01)
    assert results == [{"via": None, "to": "127.0.0.1", "port": sink.port, "ok": True}]
    assert sink.packets == [magic_packet(MAC)]


async def test_a_failing_target_is_reported_without_hiding_the_others():
    sink = await start_udp_sink()
    results = await send_magic_packet(
        MAC,
        [Target("203.0.113.99", "127.0.0.1", 9), Target(None, "127.0.0.1", sink.port)],
    )
    assert [r["ok"] for r in results] == [False, True]  # adresse source inexistante
    assert "error" in results[0]
