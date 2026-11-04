import stat
from pathlib import Path

import pytest

from mydock_common.config import ConfigError, load_toml, write_toml_private
from mydock_dock.config import (
    DEFAULT_AGENT_PORT,
    DEFAULT_CLIENTS_DIR,
    DEFAULT_PORT,
    DEFAULT_PORTAL_PORT,
    DockConfig,
    init_config,
)
from mydock_dock.validation import ValidationFailure

TOKEN = "t" * 40


def test_defaults_keep_the_dock_local_and_unconfigured():
    config = DockConfig.from_dict({}, Path("/x/dock.toml"))
    assert config.listen_host == "127.0.0.1"
    assert config.port == DEFAULT_PORT
    assert config.agent_port == DEFAULT_AGENT_PORT
    assert config.agent_host == ""
    assert config.token == ""
    assert config.loopback_only


def test_exposing_the_dock_on_the_network_is_refused_by_default():
    raw = {"server": {"listen_host": "0.0.0.0"}}
    with pytest.raises(ConfigError, match="sans authentification"):
        DockConfig.from_dict(raw, Path("/x"))
    raw["server"]["allow_remote"] = True
    assert not DockConfig.from_dict(raw, Path("/x")).loopback_only


@pytest.mark.parametrize(
    ("raw", "message"),
    [
        ({"server": {"port": 0}}, "server.port"),
        ({"server": {"listen_host": "pas-une-ip"}}, "IPv4"),
        ({"server": {"log_level": "bavard"}}, "log_level"),
        ({"pc": {"host": "http://pc"}}, "Configuration invalide"),
        ({"pc": {"port": "8765"}}, "pc.port"),
        ({"pc": {"token": "court"}}, "pc.token"),
        ({"pc": {"wol_mac": "n'importe quoi"}}, "MAC"),
        ({"pc": "texte"}, "section"),
    ],
)
def test_invalid_values_are_explained(raw, message):
    with pytest.raises(ConfigError, match=message):
        DockConfig.from_dict(raw, Path("/x"))


def test_init_creates_a_private_file(tmp_path):
    path = tmp_path / "etc" / "dock.toml"
    config = init_config(path, pc_host="Ubuntu.local", token=TOKEN, state_dir=tmp_path / "state")
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert config.agent_host == "ubuntu.local"
    reloaded = DockConfig.load(path)
    assert reloaded.token == TOKEN
    assert reloaded.state_dir == tmp_path / "state"


def test_init_is_idempotent_and_partial(tmp_path):
    path = tmp_path / "dock.toml"
    init_config(path, pc_host="ubuntu.local", pc_port=9000, token=TOKEN)
    init_config(path, port=8081)  # ne touche à rien d'autre
    config = DockConfig.load(path)
    assert (config.agent_host, config.agent_port, config.token, config.port) == (
        "ubuntu.local",
        9000,
        TOKEN,
        8081,
    )


def test_unknown_sections_survive_rewrites(tmp_path):
    """Les clés d'API de l'étape e vivront dans [markets] : une sauvegarde ne doit pas les perdre."""
    path = tmp_path / "dock.toml"
    write_toml_private(path, {"pc": {"host": "pc.local"}, "markets": {"finnhub_key": "k" * 20}})
    config = DockConfig.load(path)
    config.set_wol_mac("02:00:5e:10:00:01")
    saved = load_toml(path)
    assert saved["markets"] == {"finnhub_key": "k" * 20}
    assert saved["pc"]["wol_mac"] == "02:00:5e:10:00:01"
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_setters_validate_before_writing(tmp_path):
    path = tmp_path / "dock.toml"
    config = init_config(path, pc_host="ubuntu.local", token=TOKEN)
    before = path.read_text()
    with pytest.raises(ValidationFailure):
        config.set_pc("http://x", 8765)
    with pytest.raises(ValidationFailure):
        config.set_pc("pc.local", 70000)
    with pytest.raises(ValidationFailure):
        config.set_token("court")
    with pytest.raises(ValidationFailure):
        config.set_wol_mac("zz")
    assert path.read_text() == before
    assert config.agent_host == "ubuntu.local"


def test_setters_persist(tmp_path):
    path = tmp_path / "dock.toml"
    config = init_config(path)
    config.set_pc("192.168.1.20", 9001)
    config.set_token("n" * 40)
    config.set_wol_mac("02-00-5E-10-00-01")
    again = DockConfig.load(path)
    assert (again.agent_host, again.agent_port, again.token, again.wol_mac) == (
        "192.168.1.20",
        9001,
        "n" * 40,
        "02:00:5e:10:00:01",
    )
    config.set_wol_mac(None)
    assert DockConfig.load(path).wol_mac == ""


def test_a_world_readable_config_is_refused(tmp_path):
    path = tmp_path / "dock.toml"
    init_config(path, token=TOKEN)
    path.chmod(0o644)
    with pytest.raises(ConfigError, match="chmod 600"):
        DockConfig.load(path)


# --- le portail -------------------------------------------------------------------------------------


def test_the_portal_is_on_by_default_on_the_network_while_the_interface_stays_local():
    config = DockConfig.from_dict({}, Path("/x/dock.toml"))
    assert config.portal_enabled is True
    assert config.portal_host == "0.0.0.0"
    assert config.portal_port == DEFAULT_PORTAL_PORT == 80
    assert config.clients_dir == DEFAULT_CLIENTS_DIR
    assert config.loopback_only  # l'interface de pilotage, elle, n'écoute toujours que en local


def test_the_portal_section_is_read():
    raw = {
        "portal": {
            "enabled": False,
            "listen_host": "192.168.1.9",
            "port": 8081,
            "clients_dir": "/srv/pkg",
        }
    }
    config = DockConfig.from_dict(raw, Path("/x"))
    assert (config.portal_enabled, config.portal_host, config.portal_port) == (
        False,
        "192.168.1.9",
        8081,
    )
    assert config.clients_dir == Path("/srv/pkg")


@pytest.mark.parametrize(
    ("raw", "message"),
    [
        ({"portal": {"port": 0}}, "portal.port"),
        ({"portal": {"port": 70000}}, "portal.port"),
        ({"portal": {"port": "80"}}, "portal.port"),
        ({"portal": {"listen_host": "pas-une-ip"}}, "IPv4"),
        ({"portal": "texte"}, "section"),
        ({"portal": {"port": 8080}}, "deux ports différents"),
        ({"server": {"port": 9000}, "portal": {"port": 9000}}, "deux ports différents"),
    ],
)
def test_an_invalid_portal_is_explained(raw, message):
    with pytest.raises(ConfigError, match=message):
        DockConfig.from_dict(raw, Path("/x"))


def test_a_disabled_portal_may_share_the_port_number_since_it_never_opens():
    raw = {"portal": {"enabled": False, "port": 8080}}
    assert DockConfig.from_dict(raw, Path("/x")).portal_enabled is False


def test_the_portal_settings_are_written_and_read_back(tmp_path):
    path = tmp_path / "dock.toml"
    init_config(path, portal_port=8081, clients_dir=tmp_path / "paquets")
    config = DockConfig.load(path)
    assert config.portal_port == 8081
    assert config.clients_dir == tmp_path / "paquets"
    assert "[portal]" in path.read_text()
    init_config(path, portal_enabled=False)
    assert DockConfig.load(path).portal_enabled is False
    assert DockConfig.load(path).portal_port == 8081  # le reste n'a pas bougé


def test_init_refuses_a_portal_on_the_port_of_the_interface(tmp_path):
    with pytest.raises(ConfigError, match="doit différer"):
        init_config(tmp_path / "dock.toml", portal_port=DEFAULT_PORT)


def test_pairing_writes_address_port_and_token_in_one_go(tmp_path):
    path = tmp_path / "dock.toml"
    config = init_config(path)
    config.set_pairing("192.168.1.3", 8765, TOKEN)
    saved = DockConfig.load(path)
    assert (saved.agent_host, saved.agent_port, saved.token) == ("192.168.1.3", 8765, TOKEN)
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


@pytest.mark.parametrize(
    ("host", "port", "token"),
    [
        ("http://pc", 8765, TOKEN),
        ("192.168.1.3", 0, TOKEN),
        ("192.168.1.3", "8765", TOKEN),
        ("192.168.1.3", 8765, "court"),
    ],
)
def test_pairing_validates_everything_before_changing_anything(tmp_path, host, port, token):
    path = tmp_path / "dock.toml"
    config = init_config(path, pc_host="192.168.1.50", token=TOKEN)
    with pytest.raises(ValidationFailure):
        config.set_pairing(host, port, token)
    assert (config.agent_host, config.agent_port, config.token) == ("192.168.1.50", 8765, TOKEN)
    assert DockConfig.load(path).agent_host == "192.168.1.50"
