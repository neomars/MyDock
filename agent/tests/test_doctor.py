"""`mydock-agent doctor` : pare-feu, adresse d'écoute et vérification depuis le Pi."""

import ipaddress
import subprocess

from mydock_agent import doctor
from mydock_agent.config import AgentConfig
from mydock_agent.doctor import check_config, check_firewall, check_from_pi, curl_check
from mydock_common.auth import generate_token
from mydock_common.report import Report


def make_config(*clients: str, port: int = 8765, bind: str = "0.0.0.0") -> AgentConfig:
    return AgentConfig(
        token=generate_token(),
        bind_host=bind,
        port=port,
        allowed_clients=tuple(ipaddress.ip_network(c) for c in clients),
    )


def never(_unit: str) -> bool:
    return False


def ufw_conf(tmp_path, enabled: str):
    path = tmp_path / "ufw.conf"
    path.write_text(f"# commentaire\nENABLED={enabled}\nLOGLEVEL=low\n")
    return path


def firewall_text(config: AgentConfig, **kw) -> tuple[str, Report]:
    report = Report()
    check_firewall(report, config, **kw)
    return report.render(), report


# --- pare-feu ------------------------------------------------------------------------------------


def test_an_active_ufw_comes_with_the_exact_command_for_the_pi(tmp_path):
    text, report = firewall_text(
        make_config("192.168.1.60"),
        ufw_conf=ufw_conf(tmp_path, "yes"),
        service_active=never,
    )
    assert "ufw est actif" in text
    assert "« PC hors ligne »" in text
    assert "sudo ufw status | grep 8765" in text
    assert "sudo ufw allow from 192.168.1.60 to any port 8765 proto tcp" in text
    assert not report.failed  # un avertissement, pas une panne : les règles sont illisibles


def test_the_command_follows_the_configured_port_and_every_client(tmp_path):
    text, _ = firewall_text(
        make_config("192.168.1.60", "10.0.0.0/24", port=9000),
        ufw_conf=ufw_conf(tmp_path, "yes"),
        service_active=never,
    )
    assert "sudo ufw allow from 192.168.1.60 to any port 9000 proto tcp" in text
    assert "sudo ufw allow from 10.0.0.0/24 to any port 9000 proto tcp" in text


def test_without_allowed_clients_the_local_networks_are_suggested(tmp_path, monkeypatch):
    monkeypatch.setattr(
        doctor, "local_lan_networks", lambda: [ipaddress.ip_network("192.168.1.0/24")]
    )
    text, _ = firewall_text(make_config(), ufw_conf=ufw_conf(tmp_path, "yes"), service_active=never)
    assert "sudo ufw allow from 192.168.1.0/24 to any port 8765 proto tcp" in text


def test_an_inactive_ufw_is_reassuring(tmp_path):
    text, _ = firewall_text(
        make_config("192.168.1.60"), ufw_conf=ufw_conf(tmp_path, "no"), service_active=never
    )
    assert "inactif" in text
    assert "sudo ufw allow" not in text


def test_ufw_conf_is_parsed_loosely(tmp_path):
    for line in ("ENABLED=yes", "enabled = yes", "ENABLED=YES"):
        path = tmp_path / "ufw.conf"
        path.write_text(f"{line}\n")
        text, _ = firewall_text(make_config("192.168.1.60"), ufw_conf=path, service_active=never)
        assert "ufw est actif" in text, line


def test_no_known_firewall_means_nothing_to_open(tmp_path):
    text, report = firewall_text(
        make_config("192.168.1.60"), ufw_conf=tmp_path / "absent.conf", service_active=never
    )
    assert "rien à ouvrir" in text
    assert not report.failed


def test_an_active_firewalld_gets_its_own_instructions(tmp_path):
    text, _ = firewall_text(
        make_config("192.168.1.60", port=9000),
        ufw_conf=tmp_path / "absent.conf",
        service_active=lambda unit: unit == "firewalld",
    )
    assert "firewalld est actif" in text
    assert "--add-port=9000/tcp" in text
    assert "firewall-cmd --reload" in text


def test_the_firewall_check_only_reads(tmp_path, monkeypatch):
    """Le diagnostic ne lance que `systemctl is-active` : jamais sudo, jamais de modification."""
    launched = []

    def fake_run(command, **kwargs):
        launched.append(command)
        return subprocess.CompletedProcess(command, 3, stdout="inactive\n", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setattr(doctor.shutil, "which", lambda name: f"/usr/bin/{name}")
    firewall_text(make_config("192.168.1.60"), ufw_conf=ufw_conf(tmp_path, "yes"))
    assert launched == [["systemctl", "is-active", "firewalld"]]


# --- adresse d'écoute ----------------------------------------------------------------------------


def test_an_agent_bound_to_loopback_is_flagged_because_the_pi_can_never_reach_it(tmp_path):
    path = tmp_path / "agent.toml"
    config = make_config("192.168.1.60", bind="127.0.0.1")
    path.write_text(
        f'token = "{config.token}"\nbind_host = "127.0.0.1"\nallowed_clients = ["192.168.1.60"]\n'
    )
    path.chmod(0o600)
    report = Report()
    check_config(report, path)
    text = report.render()
    assert "n'accepte que les connexions de ce PC" in text
    assert "0.0.0.0" in text


def test_the_default_bind_is_not_flagged(tmp_path):
    path = tmp_path / "agent.toml"
    config = make_config("192.168.1.60")
    path.write_text(f'token = "{config.token}"\nallowed_clients = ["192.168.1.60"]\n')
    path.chmod(0o600)
    report = Report()
    check_config(report, path)
    assert "n'accepte que" not in report.render()


# --- vérification depuis le Pi -------------------------------------------------------------------


def iface(name: str, kind: str, up: bool, *ipv4: str, default: bool = False) -> dict:
    return {
        "name": name,
        "mac": "02:00:5e:10:00:01",
        "kind": kind,
        "up": up,
        "ipv4": list(ipv4),
        "default": default,
    }


def test_the_curl_command_is_ready_to_paste_on_the_pi():
    assert curl_check("192.168.1.3", 8765) == (
        "curl -sS -m 5 -o /dev/null -w '%{http_code}\\n' http://192.168.1.3:8765/"
    )


def test_one_curl_line_per_active_address_with_its_meaning(monkeypatch):
    monkeypatch.setattr(
        doctor,
        "list_interfaces",
        lambda: [
            iface("enp0s31f6", "ethernet", False),
            iface("wlp0s20f3", "wifi", True, "192.168.1.3", default=True),
        ],
    )
    report = Report()
    check_from_pi(report, make_config("192.168.1.60", port=9000))
    text = report.render()
    assert "curl -sS -m 5 -o /dev/null -w '%{http_code}\\n' http://192.168.1.3:9000/" in text
    assert "wlp0s20f3 [route par défaut]" in text
    assert "enp0s31f6" not in text  # câble débranché : pas d'adresse à essayer
    for meaning in ("404 =", "403 =", "timed out", "Connection refused"):
        assert meaning in text
    assert "\n' http" not in text  # le \n de curl reste un \n écrit, pas un saut de ligne


def test_two_active_networks_give_two_commands(monkeypatch):
    monkeypatch.setattr(
        doctor,
        "list_interfaces",
        lambda: [
            iface("enp0s31f6", "ethernet", True, "192.168.1.20"),
            iface("wlp0s20f3", "wifi", True, "192.168.1.3", default=True),
        ],
    )
    report = Report()
    check_from_pi(report, make_config("192.168.1.60"))
    text = report.render()
    assert "http://192.168.1.20:8765/" in text
    assert "http://192.168.1.3:8765/" in text


def test_no_active_network_is_called_out(monkeypatch):
    monkeypatch.setattr(doctor, "list_interfaces", lambda: [iface("wlp0s20f3", "wifi", False)])
    report = Report()
    check_from_pi(report, make_config("192.168.1.60"))
    assert "aucune adresse IPv4 sur un réseau actif" in report.render()
