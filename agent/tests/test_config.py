import ipaddress
import stat

import pytest

from mydock_agent.config import (
    DEFAULT_PORT,
    config_from_dict,
    init_config,
    load_config,
    parse_networks,
)
from mydock_common.auth import is_valid_token_format
from mydock_common.config import ConfigError, load_toml

GOOD_TOKEN = "t" * 40


def test_parse_networks_accepts_hosts_and_cidr():
    networks = parse_networks(["192.0.2.50", " 198.51.100.0/24 "])
    assert networks == (
        ipaddress.ip_network("192.0.2.50/32"),
        ipaddress.ip_network("198.51.100.0/24"),
    )


@pytest.mark.parametrize("bad", ["pi.local", "999.1.1.1", "", "192.0.2.0/33", "2001:db8::1"])
def test_parse_networks_rejects_garbage_and_ipv6(bad):
    with pytest.raises(ConfigError, match="allowed_clients"):
        parse_networks([bad])


def test_parse_networks_normalizes_host_bits():
    assert parse_networks(["192.0.2.77/24"]) == (ipaddress.ip_network("192.0.2.0/24"),)


def test_config_defaults():
    config = config_from_dict({"token": GOOD_TOKEN})
    assert config.port == DEFAULT_PORT
    assert config.bind_host == "0.0.0.0"
    assert config.allowed_clients == ()
    assert config.log_level == "INFO"


@pytest.mark.parametrize(
    ("raw", "message"),
    [
        ({}, "token"),
        ({"token": "court"}, "token"),
        ({"token": GOOD_TOKEN, "port": 0}, "port"),
        ({"token": GOOD_TOKEN, "port": 70000}, "port"),
        ({"token": GOOD_TOKEN, "port": "8765"}, "port"),
        ({"token": GOOD_TOKEN, "port": True}, "port"),
        ({"token": GOOD_TOKEN, "bind_host": "pas-une-ip"}, "bind_host"),
        ({"token": GOOD_TOKEN, "bind_host": "::"}, "bind_host"),
        ({"token": GOOD_TOKEN, "allowed_clients": "192.0.2.1"}, "liste"),
        ({"token": GOOD_TOKEN, "log_level": "bavard"}, "log_level"),
    ],
)
def test_config_validation_errors_are_explicit(raw, message):
    with pytest.raises(ConfigError, match=message):
        config_from_dict(raw)


def test_init_creates_private_file_with_generated_token(tmp_path):
    path = tmp_path / "mydock" / "agent.toml"
    config, created = init_config(path, allow=["192.0.2.50"])
    assert created
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert is_valid_token_format(config.token)
    assert config.allowed_clients == (ipaddress.ip_network("192.0.2.50/32"),)
    assert load_config(path) == config


def test_init_is_idempotent_and_keeps_the_token(tmp_path):
    path = tmp_path / "agent.toml"
    first, created_first = init_config(path, allow=["192.0.2.50"])
    second, created_second = init_config(path, allow=["192.0.2.50"])
    assert created_first
    assert not created_second
    assert second.token == first.token
    assert load_toml(path)["allowed_clients"] == ["192.0.2.50/32"]


def test_init_without_allow_keeps_existing_clients(tmp_path):
    path = tmp_path / "agent.toml"
    init_config(path, allow=["192.0.2.50"], port=9000)
    config, _ = init_config(path)
    assert config.port == 9000
    assert config.allowed_clients == (ipaddress.ip_network("192.0.2.50/32"),)


def test_init_replaces_allowed_clients_when_given(tmp_path):
    path = tmp_path / "agent.toml"
    init_config(path, allow=["192.0.2.50"])
    config, _ = init_config(path, allow=["198.51.100.0/24"])
    assert config.allowed_clients == (ipaddress.ip_network("198.51.100.0/24"),)


def test_init_rotates_token_only_on_request(tmp_path):
    path = tmp_path / "agent.toml"
    first, _ = init_config(path)
    rotated, created = init_config(path, rotate_token=True)
    assert created
    assert rotated.token != first.token


def test_init_refuses_invalid_input_without_touching_the_file(tmp_path):
    path = tmp_path / "agent.toml"
    good, _ = init_config(path, allow=["192.0.2.50"])
    before = path.read_text()
    with pytest.raises(ConfigError):
        init_config(path, allow=["pas une ip"])
    with pytest.raises(ConfigError):
        init_config(path, port=99999)
    assert path.read_text() == before
    assert load_config(path) == good


def test_init_refuses_to_rewrite_a_world_readable_file(tmp_path):
    path = tmp_path / "agent.toml"
    init_config(path)
    path.chmod(0o644)
    with pytest.raises(ConfigError, match="chmod 600"):
        init_config(path)
