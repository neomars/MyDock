import stat

import pytest

from mydock_agent.__main__ import main
from mydock_agent.doctor import _handshake
from mydock_agent.testing import TOKEN, running_agent


def test_init_then_show_token(tmp_path, capsys):
    path = tmp_path / "agent.toml"
    assert main(["init", "--config", str(path), "--allow", "192.0.2.50"]) == 0
    out = capsys.readouterr().out
    assert "nouveau jeton généré" in out
    assert "192.0.2.50" in out
    assert stat.S_IMODE(path.stat().st_mode) == 0o600

    assert main(["show-token", "--config", str(path)]) == 0
    token = capsys.readouterr().out.strip()
    assert len(token) >= 43

    assert main(["init", "--config", str(path)]) == 0
    assert "conservé" in capsys.readouterr().out
    main(["show-token", "--config", str(path)])
    assert capsys.readouterr().out.strip() == token


def test_show_token_reports_a_missing_config(tmp_path, capsys):
    assert main(["show-token", "--config", str(tmp_path / "absent.toml")]) == 2
    assert "introuvable" in capsys.readouterr().err


def test_run_refuses_to_start_without_a_valid_config(tmp_path, capsys):
    assert main(["run", "--config", str(tmp_path / "absent.toml")]) == 2
    assert "Erreur de configuration" in capsys.readouterr().err


def test_run_refuses_a_world_readable_config(tmp_path, capsys):
    path = tmp_path / "agent.toml"
    main(["init", "--config", str(path)])
    path.chmod(0o644)
    capsys.readouterr()
    assert main(["run", "--config", str(path)]) == 2
    assert "chmod 600" in capsys.readouterr().err


def test_version_flag(capsys):
    with pytest.raises(SystemExit) as info:
        main(["--version"])
    assert info.value.code == 0
    assert "mydock-agent" in capsys.readouterr().out


def test_doctor_reports_sections_and_never_prints_the_token(tmp_path, capsys):
    path = tmp_path / "agent.toml"
    main(["init", "--config", str(path), "--allow", "192.0.2.50", "--port", "1"])
    token = path.read_text().split('token = "')[1].split('"')[0]
    capsys.readouterr()
    main(["doctor", "--config", str(path), "--cpu", "0.1"])
    out = capsys.readouterr().out
    for section in ("diagnostic de l'agent", "Configuration", "Session graphique", "Réseau"):
        assert section in out
    assert "Clients autorisés : 192.0.2.50/32" in out
    assert "Aucun agent n'écoute" in out  # rien ne tourne sur le port 1
    assert token not in out


def test_doctor_explains_a_missing_config(tmp_path, capsys):
    assert main(["doctor", "--config", str(tmp_path / "absent.toml")]) == 1
    assert "introuvable" in capsys.readouterr().out


async def test_handshake_against_a_live_agent():
    async with running_agent() as agent:
        assert await _handshake("127.0.0.1", agent.port, TOKEN) == "ok"
        assert "refusé (HTTP 403)" in await _handshake("127.0.0.1", agent.port, "x" * 40)
        port = agent.port
    assert await _handshake("127.0.0.1", port, TOKEN) == "arrêté"
