"""`mydock-agent doctor` : pare-feu, adresse d'écoute et vérification depuis le Pi."""

import ipaddress
import subprocess

from mydock_agent import doctor, firewall
from mydock_agent.config import AgentConfig
from mydock_agent.doctor import (
    check_config,
    check_firewall,
    check_from_pi,
    check_gpus,
    check_music,
    check_sensors,
    check_service,
    curl_check,
)
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
        firewall, "local_lan_networks", lambda: [ipaddress.ip_network("192.168.1.0/24")]
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
    monkeypatch.setattr(firewall.shutil, "which", lambda name: f"/usr/bin/{name}")
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


# --- service systemd -----------------------------------------------------------------------------


def service_text(**props: str) -> tuple[str, Report]:
    base = {
        "LoadState": "loaded",
        "ActiveState": "active",
        "SubState": "running",
        "UnitFileState": "enabled",
        "FragmentPath": "/usr/lib/systemd/user/mydock-agent.service",
        "ConditionResult": "yes",
    }
    report = Report()
    check_service(report, lambda: {**base, **props})
    return report.render(), report


def test_a_running_packaged_service_is_all_green():
    text, report = service_text()
    assert "Unité fournie par le système" in text
    assert "Service actif (running)" in text
    assert "⚠" not in text
    assert not report.failed


def test_an_unconfigured_service_says_how_to_configure_it():
    text, _ = service_text(ActiveState="inactive", SubState="dead", ConditionResult="no")
    assert "pas encore de configuration" in text
    assert "mydock-agent setup --pi" in text


def test_a_failed_service_points_at_the_journal():
    text, report = service_text(ActiveState="failed", SubState="failed")
    assert report.failed
    assert "journalctl --user -u mydock-agent" in text


def test_a_unit_in_the_home_directory_is_flagged_as_masking_the_package():
    text, _ = service_text(FragmentPath="/home/mars/.config/systemd/user/mydock-agent.service")
    assert "masque celle du paquet" in text
    assert "mydock-agent setup" in text


def test_a_missing_service_points_at_the_package():
    text, _ = service_text(LoadState="not-found", FragmentPath="")
    assert "sudo apt install" in text


def test_a_masked_service_is_flagged():
    text, _ = service_text(UnitFileState="masked", ActiveState="inactive", ConditionResult="yes")
    assert "masked" in text


def test_an_unreachable_user_systemd_is_not_an_error():
    report = Report()
    check_service(report, lambda: None)
    assert "non joignable" in report.render()
    assert not report.failed


# --- capteurs, cartes graphiques, musique ----------------------------------------------------------


class FakeSensors:
    def __init__(self, *, temps=None, battery=None, cores=(10.0, 20.0)):
        self._temps, self._battery, self._cores = temps or {}, battery, cores

    def cpu_percent(self):
        return list(self._cores)

    def temperatures(self):
        return self._temps

    def battery(self):
        return self._battery


def test_sensors_report_what_the_dock_will_be_able_to_show():
    report = Report()
    check_sensors(
        report,
        FakeSensors(
            temps={"coretemp": [("Package id 0", 54.0)], "nvme": [("Composite", 38.0)]},
            battery={"percent": 82, "plugged": True, "secs_left": None},
        ),
    )
    text = report.render()
    assert "2 cœur(s)" in text
    assert "Température du processeur : 54 °C" in text
    assert "SSD : 38 °C" in text
    assert "Batterie : 82 % (sur secteur)" in text


def test_a_desktop_without_sensors_is_not_an_error():
    report = Report()
    check_sensors(report, FakeSensors())
    text = report.render()
    assert "aucun capteur lisible" in text
    assert "Pas de batterie" in text
    assert not report.failed


def gpu_entry(**kw):
    return {
        "id": "0000:01:00.0",
        "vendor": "nvidia",
        "name": "NVIDIA RTX 4000 Ada Generation Laptop GPU",
        "state": "active",
        "util": None,
        "freq_mhz": None,
        **kw,
    }


def test_a_sleeping_nvidia_card_is_reported_as_fine():
    report = Report()
    check_gpus(report, lambda: [gpu_entry(state="suspended")])
    text = report.render()
    assert "en veille" in text
    assert "ne la réveille pas" in text
    assert "⚠" not in text


def test_active_cards_show_their_load_or_the_reason_they_cannot_be_read():
    report = Report()
    check_gpus(
        report,
        lambda: [
            gpu_entry(util=63),
            gpu_entry(name="Intel UHD Graphics 770", vendor="intel", freq_mhz=450),
            gpu_entry(name="Autre", note="Bibliothèque NVML absente"),
        ],
    )
    text = report.render()
    assert "charge 63 %" in text
    assert "Intel UHD Graphics 770 : active, 450 MHz" in text
    assert "⚠ Autre : Bibliothèque NVML absente" in text


def test_no_graphics_card_is_only_informational():
    report = Report()
    check_gpus(report, lambda: [])
    assert "Aucune carte graphique" in report.render()
    assert not report.failed


class FakeMprisBus:
    def __init__(self, names, *, fail=None):
        self.names, self.fail = names, fail

    async def connect(self):
        if self.fail:
            raise self.fail

    async def disconnect(self):
        pass

    async def call(self, destination, path, interface, member, signature="", body=None):
        if member == "ListNames":
            return [self.names]
        return [{"Identity": "Spotify" if destination.endswith("spotify") else ""}]


def test_open_players_are_listed():
    report = Report()
    check_music(
        report,
        lambda: FakeMprisBus(
            [
                "org.freedesktop.Notifications",
                "org.mpris.MediaPlayer2.spotify",
                "org.mpris.MediaPlayer2.brave.instance1",
            ]
        ),
    )
    text = report.render()
    assert "lecteurs ouverts" in text
    assert "spotify  (Spotify)" in text
    assert "brave.instance1" in text
    assert "Notifications" not in text


def test_no_open_player_is_informational():
    report = Report()
    check_music(report, lambda: FakeMprisBus(["org.freedesktop.Notifications"]))
    assert "aucun lecteur ouvert" in report.render()
    assert not report.failed


def test_a_missing_session_bus_explains_where_to_run_the_doctor():
    from mydock_agent.collectors.music import BusError

    report = Report()
    check_music(
        report,
        lambda: FakeMprisBus(
            [], fail=BusError("Bus de session D-Bus injoignable (FileNotFoundError).")
        ),
    )
    text = report.render()
    assert "injoignable" in text
    assert "session graphique" in text
    assert not report.failed
