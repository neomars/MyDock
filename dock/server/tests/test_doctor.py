"""`mydock-dock doctor` : chaque adresse du PC est testée, et le résultat est expliqué."""

import stat

import pytest

from mydock_agent.testing import TOKEN, running_agent
from mydock_common.report import Report
from mydock_dock import doctor
from mydock_dock.config import DockConfig
from mydock_dock.doctor import TcpResult, _check_pc, ping, tcp_probe
from mydock_dock.testing import free_port


def config_for(tmp_path, host: str, port: int, token: str = TOKEN) -> DockConfig:
    return DockConfig.from_dict(
        {"pc": {"host": host, "port": port, "token": token}}, tmp_path / "d.toml"
    )


async def run_check(config: DockConfig) -> tuple[str, bool]:
    report = Report()
    await _check_pc(report, config)
    return report.render(), report.failed


# --- les sondes ----------------------------------------------------------------------------------


async def test_tcp_probe_tells_an_open_port_from_a_refused_one():
    async with running_agent() as agent:
        opened = await tcp_probe("127.0.0.1", agent.port)
    refused = await tcp_probe("127.0.0.1", free_port())
    assert opened.outcome == "open"
    assert opened.ms >= 0
    assert refused.outcome == "refused"


def fake_ping(tmp_path, monkeypatch, exit_code: int) -> None:
    script = tmp_path / "bin" / "ping"
    script.parent.mkdir(exist_ok=True)
    script.write_text(f"#!/bin/sh\nexit {exit_code}\n")
    script.chmod(script.stat().st_mode | stat.S_IXUSR)
    monkeypatch.setenv("PATH", str(script.parent))


async def test_ping_reports_what_the_command_says(tmp_path, monkeypatch):
    fake_ping(tmp_path, monkeypatch, 0)
    assert await ping("192.0.2.1") is True
    fake_ping(tmp_path, monkeypatch, 1)
    assert await ping("192.0.2.1") is False


async def test_ping_is_unknown_when_the_command_is_missing(tmp_path, monkeypatch):
    monkeypatch.setenv("PATH", str(tmp_path))  # dossier vide
    assert await ping("192.0.2.1") is None


# --- le verdict ----------------------------------------------------------------------------------


async def test_a_live_agent_is_reported_with_every_step(tmp_path):
    async with running_agent() as agent:
        text, failed = await run_check(config_for(tmp_path, "127.0.0.1", agent.port))
    assert not failed
    assert "Adresses de ce Pi" in text
    assert f"127.0.0.1:{agent.port} : le port répond" in text
    assert "Agent joignable et jeton accepté" in text


async def test_a_refused_port_means_the_agent_is_not_listening(tmp_path):
    text, failed = await run_check(config_for(tmp_path, "127.0.0.1", free_port()))
    assert failed
    assert "connexion refusée" in text
    assert "l'agent MyDock n'écoute pas" in text
    assert "systemctl --user status mydock-agent" in text
    assert "Test du jeton ignoré" in text


@pytest.mark.parametrize("outcome", ["silent", "unreachable"])
async def test_silence_with_a_pingable_pc_points_at_the_firewall(tmp_path, monkeypatch, outcome):
    async def silent(ip, port, timeout=3.0):
        return TcpResult(outcome, 3000.0)

    async def pingable(ip, timeout=4.0):
        return True

    monkeypatch.setattr(doctor, "tcp_probe", silent)
    monkeypatch.setattr(doctor, "ping", pingable)
    text, failed = await run_check(config_for(tmp_path, "127.0.0.1", 8765))
    assert failed
    assert "répond au ping" in text
    assert "pare-feu du PC" in text
    # La commande est prête à copier, avec l'adresse que le PC voit arriver.
    assert "sudo ufw allow from 127.0.0.1 to any port 8765 proto tcp" in text


async def test_silence_without_ping_means_off_or_isolated(tmp_path, monkeypatch):
    async def silent(ip, port, timeout=3.0):
        return TcpResult("silent", 3000.0)

    async def no_answer(ip, timeout=4.0):
        return False

    monkeypatch.setattr(doctor, "tcp_probe", silent)
    monkeypatch.setattr(doctor, "ping", no_answer)
    text, failed = await run_check(config_for(tmp_path, "127.0.0.1", 8765))
    assert failed
    assert "ni au ping" in text
    assert "éteint ou en veille" in text
    assert "isole les appareils" in text
    assert "ufw" not in text


async def test_silence_on_a_pi_without_ping_says_so(tmp_path, monkeypatch):
    async def silent(ip, port, timeout=3.0):
        return TcpResult("silent", 3000.0)

    async def unknown(ip, timeout=4.0):
        return None

    monkeypatch.setattr(doctor, "tcp_probe", silent)
    monkeypatch.setattr(doctor, "ping", unknown)
    text, _failed = await run_check(config_for(tmp_path, "127.0.0.1", 8765))
    assert "iputils-ping" in text


async def test_every_address_of_a_name_is_tested(tmp_path, monkeypatch):
    async def two_addresses(host, port, timeout=5.0):
        return ["127.0.0.2", "127.0.0.1"]

    monkeypatch.setattr(doctor, "resolve_ipv4_all", two_addresses)
    async with running_agent() as agent:
        text, failed = await run_check(config_for(tmp_path, "pc.local", agent.port))
    assert f"127.0.0.2:{agent.port} : connexion refusée" in text
    assert f"127.0.0.1:{agent.port} : le port répond" in text
    assert "pc.local → 127.0.0.2, 127.0.0.1" in text
    assert "Plusieurs adresses" in text
    assert "Agent joignable" in text  # une adresse suffit
    assert failed  # mais l'autre est signalée


async def test_a_wrong_token_is_told_apart_from_a_closed_port(tmp_path):
    async with running_agent() as agent:
        text, failed = await run_check(config_for(tmp_path, "127.0.0.1", agent.port, "x" * 40))
    assert failed
    assert "le port répond" in text
    assert "jeton incorrect" in text
    assert "ce Pi (127.0.0.1)" in text


async def test_an_unknown_name_stops_the_check_with_advice(tmp_path):
    text, failed = await run_check(config_for(tmp_path, "introuvable.invalid", 8765))
    assert failed
    assert "introuvable" in text
    assert "adresse IP" in text
    assert "le port répond" not in text


async def test_nothing_is_tested_without_address_or_token(tmp_path):
    text, failed = await run_check(DockConfig.from_dict({}, tmp_path / "d.toml"))
    assert not failed
    assert "rien à tester" in text
