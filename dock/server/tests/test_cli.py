import io
import stat

import pytest

from mydock_agent.testing import TOKEN, running_agent
from mydock_common.report import Report
from mydock_dock.__main__ import main
from mydock_dock.config import DockConfig
from mydock_dock.doctor import _check_pc, check_web, check_wol
from mydock_dock.testing import free_port


def test_init_reads_the_token_from_stdin_never_from_arguments(tmp_path, monkeypatch, capsys):
    path = tmp_path / "dock.toml"
    monkeypatch.setattr("sys.stdin", io.StringIO(TOKEN + "\n"))
    code = main(["init", "--config", str(path), "--pc", "ubuntu.local", "--token-stdin"])
    out = capsys.readouterr().out
    assert code == 0
    assert "ubuntu.local:8765" in out
    assert "Jeton : présent" in out
    assert TOKEN not in out  # jamais affiché
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert DockConfig.load(path).token == TOKEN


def test_init_can_read_the_token_from_an_environment_variable(tmp_path, monkeypatch):
    path = tmp_path / "dock.toml"
    monkeypatch.setenv("MON_JETON", TOKEN)
    assert main(["init", "--config", str(path), "--token-env", "MON_JETON"]) == 0
    assert DockConfig.load(path).token == TOKEN


def test_init_is_idempotent_and_keeps_what_it_is_not_told_to_change(tmp_path, monkeypatch):
    path = tmp_path / "dock.toml"
    monkeypatch.setenv("MON_JETON", TOKEN)
    main(["init", "--config", str(path), "--pc", "ubuntu.local", "--token-env", "MON_JETON"])
    assert main(["init", "--config", str(path), "--port", "8081"]) == 0
    config = DockConfig.load(path)
    assert (config.agent_host, config.token, config.port) == ("ubuntu.local", TOKEN, 8081)


@pytest.mark.parametrize(
    "extra",
    [["--pc", "http://pc"], ["--pc-port", "0"], ["--port", "99999"]],
)
def test_init_rejects_bad_values_with_a_message(tmp_path, capsys, extra):
    assert main(["init", "--config", str(tmp_path / "dock.toml"), *extra]) == 2
    assert "Erreur" in capsys.readouterr().err


def test_init_rejects_a_malformed_token(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr("sys.stdin", io.StringIO("court\n"))
    path = tmp_path / "dock.toml"
    assert main(["init", "--config", str(path), "--token-stdin"]) == 2
    assert "Jeton invalide" in capsys.readouterr().err
    assert not path.exists()


def test_run_refuses_to_start_on_a_bad_config(tmp_path, capsys):
    assert main(["run", "--config", str(tmp_path / "absent.toml")]) == 2
    assert "introuvable" in capsys.readouterr().err
    path = tmp_path / "dock.toml"
    main(["init", "--config", str(path)])
    path.chmod(0o644)
    capsys.readouterr()
    assert main(["run", "--config", str(path)]) == 2
    assert "chmod 600" in capsys.readouterr().err


def test_doctor_explains_without_leaking(tmp_path, monkeypatch, capsys):
    path = tmp_path / "dock.toml"
    monkeypatch.setenv("MON_JETON", TOKEN)
    port = free_port()
    args = ["init", "--config", str(path), "--pc", "127.0.0.1", "--pc-port", str(port)]
    args += ["--token-env", "MON_JETON", "--state-dir", str(tmp_path / "state")]
    args += ["--web-dir", str(tmp_path / "web")]
    main(args)
    capsys.readouterr()
    assert main(["doctor", "--config", str(path)]) == 1  # interface web absente
    out = capsys.readouterr().out
    for section in ("diagnostic du dock", "Configuration", "Interface web", "Liaison avec le PC"):
        assert section in out
    assert "index.html introuvable" in out
    assert "l'agent MyDock n'écoute pas" in out
    assert TOKEN not in out


async def test_doctor_pc_check_against_a_live_agent(tmp_path):
    async with running_agent() as agent:
        config = DockConfig.from_dict(
            {"pc": {"host": "127.0.0.1", "port": agent.port, "token": TOKEN}}, tmp_path / "d.toml"
        )
        report = Report()
        await _check_pc(report, config)
        text = report.render()
        assert "Agent joignable et jeton accepté" in text
        config.token = "x" * 40
        bad = Report()
        await _check_pc(bad, config)
        assert bad.failed
        assert "jeton incorrect" in bad.render()


def test_doctor_web_and_wol_checks(tmp_path):
    web = tmp_path / "web"
    (web / "assets").mkdir(parents=True)
    (web / "index.html").write_text("x")
    (web / "assets" / "a.js").write_text("x")
    state = tmp_path / "state"
    state.mkdir()
    config = DockConfig.from_dict(
        {"paths": {"web_dir": str(web), "state_dir": str(state)}}, tmp_path / "d.toml"
    )
    report = Report()
    check_web(report, config)
    assert "1 fichier(s) d'assets" in report.render()
    assert not report.failed

    wol = Report()
    check_wol(wol, config)
    assert "MAC du PC inconnue" in wol.render()
    config.wol_mac = "02:00:5e:10:00:01"
    wol = Report()
    check_wol(wol, config)
    assert "02:00:5e:10:00:01" in wol.render()
