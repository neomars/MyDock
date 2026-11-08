"""La commande `mydock-agent spotify` : connecter, déconnecter, vérifier. Jamais le vrai dossier de l'utilisateur."""

import json
import stat

import pytest

from mydock_agent.__main__ import main
from mydock_agent.spotify.auth import Credentials, TokenStore
from mydock_agent.spotify.cli import cmd_login, cmd_logout, cmd_status
from mydock_agent.spotify.http import HttpError, HttpResponse

CLIENT_ID = "0123456789abcdef0123456789abcdef"


def creds(**more):
    values = {
        "client_id": CLIENT_ID,
        "refresh_token": "renouvellement",
        "access_token": "acces",
        "expires_at": 10**12,
        "user": "Marie",
    }
    values.update(more)
    return Credentials(**values)


class Transport:
    def __init__(self, *answers):
        self.answers = list(answers)
        self.requests = []

    def __call__(self, request, timeout):
        self.requests.append(request)
        item = self.answers.pop(0)
        if isinstance(item, Exception):
            raise item
        return HttpResponse(item[0], {}, json.dumps(item[1]).encode())


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    return TokenStore()


def test_the_store_lives_under_the_config_folder_of_the_user(store, tmp_path):
    assert store.path == tmp_path / "mydock" / "spotify.toml"


def test_status_without_an_account(store, capsys):
    assert cmd_status(store=store) == 1
    assert "aucun compte Spotify" in capsys.readouterr().out


def test_status_of_a_connected_account_needs_no_network_by_default(store, capsys):
    store.save(creds())
    transport = Transport()
    assert cmd_status(store=store, transport=transport) == 0
    out = capsys.readouterr().out
    assert "en tant que Marie" in out
    assert str(store.path) in out
    assert transport.requests == []


def test_status_check_asks_spotify_and_renews_an_expired_token(store, capsys):
    store.save(creds(expires_at=0.0))
    transport = Transport(
        (200, {"access_token": "neuf", "expires_in": 3600}), (200, {"display_name": "Marie"})
    )
    assert cmd_status(check=True, store=store, transport=transport) == 0
    assert "Spotify répond : compte « Marie »" in capsys.readouterr().out
    saved = store.load()
    assert saved is not None
    assert saved.access_token == "neuf"
    assert stat.S_IMODE(store.path.stat().st_mode) == 0o600


def test_status_check_reports_a_refused_token(store, capsys):
    store.save(creds())
    assert cmd_status(check=True, store=store, transport=Transport((403, {}))) == 1
    assert "Spotify refuse le jeton" in capsys.readouterr().out
    assert cmd_status(check=True, store=store, transport=Transport(HttpError("x"))) == 1


def test_status_reports_a_file_open_to_others(store, capsys):
    store.save(creds())
    store.path.chmod(0o644)
    assert cmd_status(store=store) == 1
    assert "chmod 600" in capsys.readouterr().out


def test_logout_removes_the_token_and_is_harmless_twice(store, capsys):
    store.save(creds())
    assert cmd_logout(store=store) == 0
    assert "retirée" in capsys.readouterr().out
    assert not store.path.exists()
    assert cmd_logout(store=store) == 0
    assert "aucun compte" in capsys.readouterr().out


def test_login_without_a_client_id_and_without_a_terminal_explains_what_to_do(monkeypatch):
    monkeypatch.setattr("sys.stdin", type("NoTty", (), {"isatty": lambda self: False})())
    lines = []
    assert cmd_login(None, port=8888, open_browser=False, out=lines.append) == 2
    text = "\n".join(lines)
    assert "--client-id" in text
    assert "http://127.0.0.1:8888/callback" in text


def test_login_asks_for_the_client_id_in_a_terminal_and_refuses_a_bad_one(monkeypatch):
    monkeypatch.setattr("sys.stdin", type("Tty", (), {"isatty": lambda self: True})())
    lines = []
    code = cmd_login(
        None, port=8888, open_browser=False, ask=lambda _prompt: "pas-valable", out=lines.append
    )
    assert code == 2
    assert "32 caractères" in "\n".join(lines)


def test_the_commands_are_wired_to_the_main_entry_point(store, capsys):
    assert main(["spotify", "status"]) == 1
    assert "aucun compte Spotify" in capsys.readouterr().out
    store.save(creds())
    assert main(["spotify", "status"]) == 0
    assert "Marie" in capsys.readouterr().out
    assert main(["spotify", "logout"]) == 0
    assert not store.path.exists()
    capsys.readouterr()


def test_login_through_main_checks_the_client_id_before_anything_else(store, capsys):
    assert main(["spotify", "login", "--client-id", "faux", "--no-browser"]) == 2
    assert "32 caractères" in capsys.readouterr().out
    assert not store.path.exists()


def test_spotify_needs_a_subcommand(capsys):
    with pytest.raises(SystemExit) as info:
        main(["spotify"])
    assert info.value.code == 2
    capsys.readouterr()
