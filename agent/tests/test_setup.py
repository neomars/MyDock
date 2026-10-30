"""`mydock-agent setup` : première configuration, sans toucher au vrai systemd ni au vrai ufw."""

import socket
import stat
import subprocess
from pathlib import Path

import pytest

from mydock_agent import __main__ as cli
from mydock_agent import firewall, setup
from mydock_agent.config import load_config
from mydock_agent.setup import PcAddress, Systemd, run_setup


class FakeSystemd(Systemd):
    """Enregistre les commandes `systemctl --user` au lieu de les lancer."""

    def __init__(self, *, reachable=True, unit_known=True, restart_ok=True):
        self._reachable = reachable
        self.unit_known = unit_known
        self.restart_ok = restart_ok
        self.calls: list[tuple[str, ...]] = []

    def reachable(self) -> bool:
        return self._reachable

    def run(self, *args, timeout=30.0):
        self.calls.append(args)
        ok = {"cat": self.unit_known, "restart": self.restart_ok}.get(args[0], True)
        return subprocess.CompletedProcess(args, 0 if ok else 1, "", "" if ok else "Boom")


@pytest.fixture
def home(tmp_path, monkeypatch):
    """Un dossier personnel jetable avec un réseau et un pare-feu connus."""
    monkeypatch.setattr(
        setup,
        "pc_addresses",
        lambda: [
            PcAddress("wlp0s20f3", "192.168.1.3", True),
            PcAddress("enp0s31f6", "10.0.0.4", False),
        ],
    )
    monkeypatch.setattr(firewall, "ufw_state", lambda conf=firewall.UFW_CONF: False)
    return tmp_path


def run(home: Path, **kw):
    lines: list[str] = []
    kw.setdefault("systemd", FakeSystemd())
    kw.setdefault("wait", lambda _config: True)
    kw.setdefault("pi", ["192.168.1.60"])
    code = run_setup(
        config_path=home / ".config" / "mydock" / "agent.toml",
        out=lines.append,
        is_root=False,
        **kw,
    )
    return code, "\n".join(lines)


def config_of(home: Path):
    return load_config(home / ".config" / "mydock" / "agent.toml")


# --- première configuration ----------------------------------------------------------------------


def test_first_setup_creates_a_private_config_starts_the_service_and_says_what_to_type(home):
    systemd = FakeSystemd()
    code, text = run(home, systemd=systemd)
    config = config_of(home)
    path = home / ".config" / "mydock" / "agent.toml"
    assert code == 0
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert [str(n) for n in config.allowed_clients] == ["192.168.1.60/32"]
    assert "nouveau jeton généré" in text
    assert ("restart", "mydock-agent.service") in systemd.calls
    assert "Service démarré" in text
    # Le Pi reçoit l'adresse IP (route par défaut) et non un nom .local qui échoue souvent.
    assert "sudo ./deploy/pi/install-dock.sh --pc 192.168.1.3" in text
    assert "http://192.168.1.3:8765/" in text
    assert "autre adresse de ce PC : 10.0.0.4" in text
    assert "adresse IP au nom" in text


def test_the_token_is_shown_once_at_creation_and_never_afterwards(home):
    _, first = run(home)
    token = config_of(home).token
    assert first.count(token) == 1
    _, second = run(home)
    assert "jeton existant conservé" in second
    assert token not in second
    assert "mydock-agent show-token" in second
    assert config_of(home).token == token


def test_rotating_the_token_gives_a_new_one(home):
    run(home)
    old = config_of(home).token
    _, text = run(home, rotate_token=True)
    new = config_of(home).token
    assert new != old
    assert new in text
    assert old not in text


def test_without_a_pi_the_local_networks_are_accepted_and_the_user_is_warned(home):
    _, text = run(home, pi=[])
    assert "Aucun Pi donné" in text
    assert "mydock-agent setup --pi" in text


def test_a_pi_given_by_name_is_resolved_to_its_address(home, monkeypatch):
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *a, **k: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("192.168.1.61", 0))],
    )
    run(home, pi=["raspberrypi.local"])
    assert [str(n) for n in config_of(home).allowed_clients] == ["192.168.1.61/32"]


def test_an_unresolvable_pi_name_is_refused_before_anything_is_written(home, monkeypatch):
    def fail(*args, **kwargs):
        raise socket.gaierror("introuvable")

    monkeypatch.setattr(socket, "getaddrinfo", fail)
    code, text = run(home, pi=["raspberrypi.local"])
    assert code == 2
    assert "Impossible de résoudre" in text
    assert "adresse IP du Pi" in text
    assert not (home / ".config" / "mydock" / "agent.toml").exists()


def test_bad_values_are_explained(home):
    code, text = run(home, port=70000)
    assert code == 2
    assert "Erreur" in text


def test_setup_refuses_to_run_as_root(home):
    lines: list[str] = []
    code = run_setup(
        config_path=home / "agent.toml", pi=["192.168.1.60"], out=lines.append, is_root=True
    )
    assert code == 2
    assert "sans sudo" in "\n".join(lines)
    assert not (home / "agent.toml").exists()


# --- service -------------------------------------------------------------------------------------


def test_an_unreachable_user_systemd_is_explained_but_the_config_is_kept(home):
    code, text = run(home, systemd=FakeSystemd(reachable=False))
    assert code == 1
    assert "systemd utilisateur est injoignable" in text
    assert "mydock-agent run" in text
    assert config_of(home).token  # le jeton existe quand même, on peut le saisir sur le Pi


def test_a_missing_unit_points_at_the_package(home):
    code, text = run(home, systemd=FakeSystemd(unit_known=False))
    assert code == 1
    assert "sudo apt install" in text


def test_a_service_that_does_not_start_shows_where_to_look(home):
    code, text = run(home, systemd=FakeSystemd(restart_ok=False))
    assert code == 1
    assert "n'a pas démarré" in text
    assert "Boom" in text
    assert "journalctl --user -u mydock-agent" in text


def test_a_service_that_starts_but_does_not_answer_is_not_a_success(home):
    code, text = run(home, wait=lambda _config: False)
    assert code == 1
    assert "ne répond pas encore" in text


def test_no_service_leaves_systemd_alone(home):
    systemd = FakeSystemd()
    code, _ = run(home, systemd=systemd, start_service=False)
    assert code == 0
    assert systemd.calls == []


# --- ancienne installation -----------------------------------------------------------------------

LEGACY_UNIT = """[Unit]
Description=MyDock - agent de pilotage du PC (dock tactile)

[Service]
ExecStart=/home/mars/.local/share/mydock/venv/bin/mydock-agent run
"""

PACKAGED_UNIT = LEGACY_UNIT.replace("/home/mars/.local/share/mydock/venv/bin", "/usr/bin")


def test_the_unit_of_the_old_installer_is_replaced_before_the_new_service_starts(home):
    unit = home / ".config" / "systemd" / "user" / "mydock-agent.service"
    unit.parent.mkdir(parents=True)
    unit.write_text(LEGACY_UNIT)
    systemd = FakeSystemd()
    code, text = run(home, systemd=systemd)
    assert code == 0
    assert not unit.exists()
    assert "Ancienne installation" in text
    names = [call[0] for call in systemd.calls]
    # L'ancien service est arrêté (il tient le port) avant que le nouveau ne démarre.
    assert names.index("disable") < names.index("daemon-reload") < names.index("restart")
    assert ("disable", "--now", "mydock-agent.service") in systemd.calls


def test_a_unit_that_already_runs_the_packaged_program_is_left_alone(home):
    unit = home / ".config" / "systemd" / "user" / "mydock-agent.service"
    unit.parent.mkdir(parents=True)
    unit.write_text(PACKAGED_UNIT)
    run(home)
    assert unit.exists()


def test_an_unrelated_unit_with_the_same_name_is_never_deleted(home):
    unit = home / ".config" / "systemd" / "user" / "mydock-agent.service"
    unit.parent.mkdir(parents=True)
    unit.write_text("[Service]\nExecStart=/opt/autre-chose/serveur\n")
    run(home)
    assert unit.exists()


@pytest.mark.parametrize(
    ("text", "legacy"),
    [
        (LEGACY_UNIT, True),
        (PACKAGED_UNIT, False),
        ("[Service]\n", False),  # pas d'ExecStart : on ne devine pas
    ],
)
def test_legacy_units_are_recognised_by_their_command(text, legacy):
    assert setup.looks_like_legacy_unit(text) is legacy


# --- pare-feu ------------------------------------------------------------------------------------


def test_an_active_ufw_gets_the_exact_commands(home, monkeypatch):
    monkeypatch.setattr(firewall, "ufw_state", lambda conf=firewall.UFW_CONF: True)
    _, text = run(home, pi=["192.168.1.60", "192.168.1.61"])
    assert "sudo ufw allow from 192.168.1.60 to any port 8765 proto tcp" in text
    assert "sudo ufw allow from 192.168.1.61 to any port 8765 proto tcp" in text
    assert "--open-firewall" in text


def test_open_firewall_runs_sudo_ufw_for_each_client_and_nothing_else(home, monkeypatch):
    monkeypatch.setattr(firewall, "ufw_state", lambda conf=firewall.UFW_CONF: True)
    launched: list[list[str]] = []

    def fake_run(command, **kwargs):
        launched.append(command)
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(setup.subprocess, "run", fake_run)
    _, text = run(home, pi=["192.168.1.60"], open_firewall=True)
    assert launched == [
        [
            "sudo",
            "ufw",
            "allow",
            "from",
            "192.168.1.60",
            "to",
            "any",
            "port",
            "8765",
            "proto",
            "tcp",
            "comment",
            "MyDock agent",
        ]
    ]
    assert "ufw : 192.168.1.60 → port 8765" in text


def test_a_failing_ufw_command_is_reported(home, monkeypatch):
    monkeypatch.setattr(firewall, "ufw_state", lambda conf=firewall.UFW_CONF: True)
    monkeypatch.setattr(
        setup.subprocess, "run", lambda command, **kw: subprocess.CompletedProcess(command, 1)
    )
    _, text = run(home, open_firewall=True)
    assert "✘ ufw : 192.168.1.60" in text


def test_an_inactive_ufw_needs_nothing(home):
    _, text = run(home, open_firewall=True)
    assert "ufw n'est pas actif" in text
    assert "sudo ufw" not in text


# --- adresses de ce PC ---------------------------------------------------------------------------


def test_the_default_route_address_comes_first(monkeypatch):
    def interface(name, address, up, default):
        return {"name": name, "up": up, "ipv4": [address] if address else [], "default": default}

    monkeypatch.setattr(
        setup,
        "list_interfaces",
        lambda: [
            interface("enp0s31f6", "10.0.0.4", True, False),
            interface("wlp0s20f3", "192.168.1.3", True, True),
            interface("enx1", "", True, False),  # sans IPv4
            interface("wlan1", "192.168.50.2", False, False),  # inactive
        ],
    )
    assert [a.address for a in setup.pc_addresses()] == ["192.168.1.3", "10.0.0.4"]


def test_no_address_at_all_is_called_out(home, monkeypatch):
    monkeypatch.setattr(setup, "pc_addresses", lambda: [])
    _, text = run(home)
    assert "aucune adresse IPv4" in text


# --- ligne de commande ---------------------------------------------------------------------------


def test_the_cli_exposes_setup_and_enable_wol():
    parser = cli.build_parser()
    args = parser.parse_args(["setup", "--pi", "192.168.1.60", "--open-firewall", "--no-service"])
    assert (args.command, args.pi, args.open_firewall, args.no_service) == (
        "setup",
        ["192.168.1.60"],
        True,
        True,
    )
    assert parser.parse_args(["enable-wol"]).command == "enable-wol"


def test_enable_wol_hands_over_to_the_script_with_its_options(tmp_path, monkeypatch):
    script = tmp_path / "enable-wol.sh"
    script.write_text("#!/bin/bash\n")
    monkeypatch.setattr(cli, "find_wol_script", lambda: script)
    calls = []
    monkeypatch.setattr(cli.os, "execv", lambda path, argv: calls.append((path, argv)))
    cli.main(["enable-wol", "--dry-run"])
    assert calls == [("/bin/bash", ["bash", str(script), "--dry-run"])]


def test_enable_wol_without_its_script_says_so(monkeypatch, capsys):
    monkeypatch.setattr(cli, "find_wol_script", lambda: None)
    assert cli.main(["enable-wol"]) == 2
    assert "introuvable" in capsys.readouterr().err
