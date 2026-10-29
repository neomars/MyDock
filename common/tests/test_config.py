import os
import stat

import pytest

from mydock_common.config import (
    ConfigError,
    check_private_file,
    ensure_private_dir,
    load_toml,
    write_toml_private,
)


def test_write_then_load_roundtrip(tmp_path):
    path = tmp_path / "sub" / "agent.toml"
    data = {"token": "t" * 30, "port": 8765, "allowed_clients": ["192.0.2.10"], "x": {"y": True}}
    write_toml_private(path, data, header="Fichier privé\nne pas publier")
    assert load_toml(path) == data
    assert path.read_text().startswith("# Fichier privé\n# ne pas publier\n")


def test_written_file_is_mode_600(tmp_path):
    path = tmp_path / "agent.toml"
    write_toml_private(path, {"token": "x"})
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_rewrite_keeps_mode_600_and_leaves_no_temp_files(tmp_path):
    path = tmp_path / "agent.toml"
    write_toml_private(path, {"v": 1})
    write_toml_private(path, {"v": 2})
    assert load_toml(path) == {"v": 2}
    assert [p.name for p in tmp_path.iterdir()] == ["agent.toml"]
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


@pytest.mark.parametrize("mode", [0o644, 0o640, 0o604, 0o660, 0o666, 0o607])
def test_loading_refuses_files_open_to_others(tmp_path, mode):
    path = tmp_path / "dock.toml"
    path.write_text('token = "secret"\n')
    path.chmod(mode)
    with pytest.raises(ConfigError, match="chmod 600"):
        load_toml(path)


@pytest.mark.parametrize("mode", [0o600, 0o400])
def test_loading_accepts_private_modes(tmp_path, mode):
    path = tmp_path / "dock.toml"
    path.write_text('token = "secret"\n')
    path.chmod(mode)
    assert load_toml(path) == {"token": "secret"}


def test_private_check_can_be_disabled_for_non_secret_files(tmp_path):
    path = tmp_path / "public.toml"
    path.write_text("a = 1\n")
    path.chmod(0o644)
    assert load_toml(path, private=False) == {"a": 1}


def test_missing_file_message(tmp_path):
    with pytest.raises(ConfigError, match="introuvable"):
        load_toml(tmp_path / "absent.toml")


def test_invalid_toml_message(tmp_path):
    path = tmp_path / "bad.toml"
    path.write_text("ceci = n'est pas du toml [")
    path.chmod(0o600)
    with pytest.raises(ConfigError, match="TOML valide"):
        load_toml(path)


def test_foreign_owner_is_refused(tmp_path, monkeypatch):
    path = tmp_path / "dock.toml"
    path.write_text('token = "secret"\n')
    path.chmod(0o600)
    check_private_file(path)  # le bon propriétaire passe
    monkeypatch.setattr(os, "getuid", lambda: path.stat().st_uid + 1)
    with pytest.raises(ConfigError, match="autre utilisateur"):
        check_private_file(path)


def test_ensure_private_dir(tmp_path):
    target = ensure_private_dir(tmp_path / "a" / "b")
    assert target.is_dir()
    assert stat.S_IMODE(target.stat().st_mode) == 0o700
