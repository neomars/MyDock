"""Le journal de débogage du Divertissement : où il va, ce qu'il garde, ce qu'il ne garde jamais."""

from __future__ import annotations

import logging
import os
import re
import stat

import pytest

from mydock_dock import playerlog

INSTALLED_DIR = playerlog.LOG_DIR  # /var/log/mydock : ce que l'installation crée


@pytest.fixture(autouse=True)
def clean(tmp_path, monkeypatch):
    """Aucun essai ne touche le vrai /var/log/mydock : le dossier du paquet est un dossier de test qui n'existe pas."""
    monkeypatch.setattr(playerlog, "LOG_DIR", tmp_path / "var-log-mydock")
    playerlog.detach()
    playerlog.detach_dock()
    yield
    playerlog.detach()
    playerlog.detach_dock()


def test_the_journal_goes_where_it_is_asked_then_to_the_package_directory_then_to_the_session(
    monkeypatch,
):
    env = {playerlog.ENV_PATH: "/x/mon.log", "XDG_RUNTIME_DIR": "/run/user/1000"}
    assert playerlog.candidates(env) == [
        playerlog.Path("/x/mon.log"),
        playerlog.LOG_DIR / "player.log",
        playerlog.Path("/run/user/1000/mydock-player.log"),
    ]
    paths = playerlog.candidates({})
    assert paths[0] == playerlog.LOG_DIR / "player.log"
    assert paths[1].name.startswith(
        "mydock-player-"
    )  # pas de dossier d'exécution : le dossier temporaire


def test_an_unwritable_place_is_skipped_for_the_next_one(tmp_path):
    runtime = tmp_path / "run"
    runtime.mkdir()
    env = {
        playerlog.ENV_PATH: str(tmp_path / "absent" / "mon.log"),
        "XDG_RUNTIME_DIR": str(runtime),
    }
    # ni le chemin demandé ni le dossier du paquet n'existent : le journal va dans le dossier d'exécution de la session
    assert playerlog.attach(env) == runtime / "mydock-player.log"
    playerlog.event("bonjour")
    assert "bonjour" in (runtime / "mydock-player.log").read_text(encoding="utf-8")


def test_the_package_directory_is_used_when_it_exists(tmp_path):
    playerlog.LOG_DIR.mkdir()
    assert playerlog.attach({"XDG_RUNTIME_DIR": str(tmp_path)}) == playerlog.LOG_DIR / "player.log"


def test_no_place_at_all_is_not_an_error(tmp_path):
    env = {
        playerlog.ENV_PATH: str(tmp_path / "a" / "b.log"),
        "XDG_RUNTIME_DIR": str(tmp_path / "c"),
    }
    assert playerlog.attach(env) is None  # la lecture marche sans journal
    playerlog.event("ne va nulle part")
    assert not playerlog.active()


def test_nothing_is_written_before_the_journal_is_opened(tmp_path):
    playerlog.event("ne va nulle part")
    playerlog.line("vlc", "ni ceci")
    assert not playerlog.active()
    assert playerlog.current_path() is None


def test_events_and_program_lines_are_written_with_their_time(tmp_path):
    path = tmp_path / "player.log"
    assert playerlog.attach({playerlog.ENV_PATH: str(path)}) == path
    playerlog.event("lecture 1 : tentative 1/2")
    playerlog.line("vlc", "[0000aa] smb stream error: open failed\r\n")
    playerlog.line("ffmpeg", "rtsp: Connection refused")
    rows = path.read_text(encoding="utf-8").splitlines()
    assert re.match(r"\d{4}-\d\d-\d\d \d\d:\d\d:\d\d lecture 1", rows[0])  # 2026-11-12 09:00:00 …
    assert rows[0].endswith("lecture 1 : tentative 1/2")
    assert rows[1].endswith("vlc| [0000aa] smb stream error: open failed")  # sans retour chariot
    assert rows[2].endswith("ffmpeg| rtsp: Connection refused")
    assert stat.S_IMODE(path.stat().st_mode) == 0o644  # lisible par le diagnostic


def test_a_password_is_never_written(tmp_path):
    path = tmp_path / "player.log"
    playerlog.attach({playerlog.ENV_PATH: str(path)})
    playerlog.add_secret("mot-de-passe-secret")
    playerlog.event("commande : smbclient --pass mot-de-passe-secret")
    playerlog.line("vlc", "config: smb-pwd=mot-de-passe-secret")
    playerlog.failure(3, "refus de mot-de-passe-secret")
    text = path.read_text(encoding="utf-8")
    assert "mot-de-passe-secret" not in text
    assert text.count("***") == 3
    playerlog.drop_secret("mot-de-passe-secret")
    playerlog.event("un autre mot-de-passe-secret")
    assert "un autre mot-de-passe-secret" in path.read_text(
        encoding="utf-8"
    )  # plus un secret : on l'écrit tel quel


def test_too_short_a_secret_is_not_erased_to_avoid_ruining_the_text(tmp_path):
    path = tmp_path / "player.log"
    playerlog.attach({playerlog.ENV_PATH: str(path)})
    playerlog.add_secret("pas")  # « passer », « passe » : on ne les abîme pas
    playerlog.event("on passe à la suite")
    assert "on passe à la suite" in path.read_text(encoding="utf-8")


def test_the_journal_turns_over_instead_of_growing_forever(tmp_path, monkeypatch):
    monkeypatch.setattr(playerlog, "MAX_BYTES", 2000)
    path = tmp_path / "player.log"
    playerlog.attach({playerlog.ENV_PATH: str(path)})
    for i in range(200):
        playerlog.line("vlc", f"ligne {i} " + "x" * 40)
    names = sorted(p.name for p in tmp_path.iterdir())
    assert "player.log" in names
    assert "player.log.1" in names
    assert len(names) <= playerlog.BACKUPS + 1  # le journal et ses anciens, jamais plus
    assert path.stat().st_size <= 2200


def test_the_last_failures_are_found_for_the_doctor(tmp_path):
    path = tmp_path / "player.log"
    playerlog.attach({playerlog.ENV_PATH: str(path)})
    playerlog.event("lecture 1 : fin normale")
    playerlog.failure(2, "Accès refusé au partage")
    playerlog.line("vlc", "bruit")
    playerlog.failure(3, "VLC ne sait pas lire le Samba")
    playerlog.failure(4, "Fichier introuvable")
    playerlog.failure(5, "La chaîne ne répond pas")
    found = playerlog.recent_failures(3, {playerlog.ENV_PATH: str(path)})
    assert len(found) == 3
    assert found[-1].endswith(f"{playerlog.FAILURE_MARK} lecture 5 : La chaîne ne répond pas")
    assert not any("lecture 2" in row for row in found)  # les trois dernières seulement


def test_without_any_failure_or_journal_the_doctor_finds_nothing(tmp_path):
    env = {
        playerlog.ENV_PATH: str(tmp_path / "absent.log"),
        "XDG_RUNTIME_DIR": str(tmp_path / "rien"),
    }
    assert playerlog.recent_failures(3, env) == []
    assert playerlog.existing_path(env) is None
    path = tmp_path / "player.log"
    path.write_text("2026-11-12 09:00:00 lecture 1 : fin normale\n", encoding="utf-8")
    assert (
        playerlog.recent_failures(3, {playerlog.ENV_PATH: str(path)}) == []
    )  # un journal sans échec
    assert playerlog.existing_path({playerlog.ENV_PATH: str(path)}) == path


def test_a_big_journal_is_only_read_by_its_end(tmp_path):
    path = tmp_path / "player.log"
    old = "x" * 100 + f" {playerlog.FAILURE_MARK} très ancienne\n"
    path.write_text(
        old * 8000 + f"2026 {playerlog.FAILURE_MARK} lecture 9 : la dernière\n", encoding="utf-8"
    )
    found = playerlog.recent_failures(2, {playerlog.ENV_PATH: str(path)})
    assert found[-1].endswith("lecture 9 : la dernière")
    assert len(found) == 2


def test_attaching_twice_changes_nothing(tmp_path):
    first = playerlog.attach({playerlog.ENV_PATH: str(tmp_path / "a.log")})
    second = playerlog.attach({playerlog.ENV_PATH: str(tmp_path / "b.log")})
    assert first == second == tmp_path / "a.log"


def test_the_dock_journal_collects_the_media_events_of_the_server(tmp_path):
    path = tmp_path / "dock.log"
    assert playerlog.attach_dock({playerlog.DOCK_ENV_PATH: str(path)}) == path
    logging.getLogger(playerlog.DOCK_LOGGER).info("Freetv : 27 chaînes lues")
    logging.getLogger(playerlog.DOCK_LOGGER).warning("Kiosque : partages de nas illisibles")
    text = path.read_text(encoding="utf-8")
    assert "INFO Freetv : 27 chaînes lues" in text
    assert "WARNING Kiosque : partages de nas illisibles" in text
    assert stat.S_IMODE(path.stat().st_mode) == 0o644


def test_the_dock_journal_falls_back_to_the_state_directory(tmp_path):
    state = tmp_path / "state"
    state.mkdir()
    chosen = playerlog.attach_dock(
        {playerlog.DOCK_ENV_PATH: str(tmp_path / "absent" / "x.log")}, fallback_dir=state
    )
    assert chosen == state / "dock.log"  # le dossier du paquet n'existe pas dans cet essai


def test_the_journal_directory_is_the_one_the_installation_creates():
    assert str(INSTALLED_DIR) == "/var/log/mydock"
    assert os.path.basename(playerlog.LOG_NAME) == "player.log"
