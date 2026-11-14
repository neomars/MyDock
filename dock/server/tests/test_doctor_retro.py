"""`mydock-dock doctor` : la section Retrogame dit ce qui manque sur le Pi (MAME, jeux, bouton, manettes, saisie)."""

from __future__ import annotations

import json
import os
import threading
from collections import namedtuple
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from types import SimpleNamespace

import pytest

from mydock_common.report import Report
from mydock_dock.config import DockConfig
from mydock_dock.doctor import check_retrogame, check_running, group_members, mame_version

TOUCH_AND_PAD = """\
I: Bus=0003 Vendor=222a Product=0001 Version=0110
N: Name="ILITEK Multi-Touch-V5100"
H: Handlers=mouse0 event3
B: EV=b
B: KEY=400 0 0 0 0 0
B: ABS=260800000000000

I: Bus=0005 Vendor=045e Product=0b13 Version=0903
N: Name="Xbox Wireless Controller"
H: Handlers=js0 event6
B: EV=20000b
B: KEY=7cdb000000000000 0 0 0 0
B: ABS=3003f
"""


def config_for(tmp_path: Path, **retro) -> DockConfig:
    return DockConfig.from_dict(
        {"paths": {"state_dir": str(tmp_path / "state")}, "retrogame": retro}, tmp_path / "d.toml"
    )


def groups_of(**present):
    """Les groupes du système : {nom: membres} ; un groupe absent du dictionnaire n'existe pas."""
    return lambda name: (name in present, set(present.get(name, ())))


def render(config, **overrides) -> str:
    report = Report()
    options = {
        "find": lambda: "/usr/games/mame",
        "version": lambda _mame: "0.264 (unknown)",
        "groups": groups_of(gpio={"mydock-kiosk"}, input={"mydock-kiosk"}, bluetooth={"mydock"}),
        "kiosk": lambda: "mydock-kiosk",
        "devices": lambda: TOUCH_AND_PAD,
        "adapters": lambda: ["hci0"],
        "pads": lambda: {
            "available": True,
            "reason": None,
            "devices": [
                {
                    "id": "A",
                    "name": "Xbox Wireless Controller",
                    "paired": True,
                    "connected": True,
                    "battery": 78,
                },
                {
                    "id": "B",
                    "name": "DualSense",
                    "paired": True,
                    "connected": False,
                    "battery": None,
                },
            ],
        },
        "gpiochips": lambda: ["/dev/gpiochip0"],
        "libgpiod": lambda: True,
        "rfkill": lambda: False,
        "which": lambda name: f"/usr/sbin/{name}" if name == "bluetoothd" else None,
        "journal": lambda: None,
    }
    options.update(overrides)
    check_retrogame(report, config, **options)
    return report.render()


def make_roms(config: DockConfig, *names: str, mode: int = 0o755) -> Path:
    folder = config.roms_path
    folder.mkdir(parents=True)
    for name in names:
        (folder / name).write_bytes(b"PK\x05\x06" + bytes(18))
    folder.chmod(mode)
    return folder


# --- MAME et les jeux ----------------------------------------------------------------------------------------------------


def test_everything_in_place_is_green(tmp_path):
    config = config_for(tmp_path)
    make_roms(config, "pacman.zip", "galaga.7z", "readme.txt")
    out = render(config)
    assert "✔ MAME présent (/usr/games/mame, 0.264 (unknown))" in out
    assert "2 archive(s) .zip ou .7z" in out  # le .txt ne compte pas
    assert "✔ libgpiod" in out
    assert "✔ Puces GPIO : /dev/gpiochip0" in out
    assert "✔ mydock-kiosk est dans le groupe gpio" in out
    assert "✔ mydock-kiosk est dans le groupe input" in out
    assert "✔ Écran tactile vu par le noyau : ILITEK Multi-Touch-V5100 (/dev/input/event3)" in out
    assert (
        "✔ Manettes vues par le noyau : Xbox Wireless Controller (/dev/input/event6, Bluetooth)"
        in out
    )
    assert "✔ BlueZ présent" in out
    assert "✔ Adaptateur Bluetooth : hci0" in out
    assert "✔ Le serveur du dock (mydock) est dans le groupe bluetooth" in out
    assert "Xbox Wireless Controller (connectée, 78 %), DualSense (hors ligne)" in out
    assert "GPIO17 (broche 11)" in out


def test_without_mame_the_install_command_is_given(tmp_path):
    out = render(config_for(tmp_path), find=lambda: None)
    assert "MAME est absent" in out
    assert "sudo apt install mame" in out


def test_a_missing_rom_folder_is_explained(tmp_path):
    out = render(config_for(tmp_path))
    assert "Dossier des jeux absent" in out
    assert str(tmp_path / "state" / "roms") in out


def test_an_empty_rom_folder_says_how_to_fill_it(tmp_path):
    config = config_for(tmp_path)
    make_roms(config)
    out = render(config)
    assert "0 archive(s)" in out
    assert "Envoyer des jeux" in out


def test_a_rom_folder_the_screen_user_cannot_read_is_a_warning(tmp_path):
    config = config_for(tmp_path)
    make_roms(config, "pacman.zip", mode=0o700)
    out = render(config)
    assert "ne peut pas lire les jeux" in out
    assert str(config.roms_path) in out


def test_a_custom_rom_folder_is_the_one_checked(tmp_path):
    elsewhere = tmp_path / "jeux"
    elsewhere.mkdir()
    elsewhere.chmod(0o755)
    (elsewhere / "pong.zip").write_bytes(b"PK\x05\x06" + bytes(18))
    out = render(config_for(tmp_path, roms_dir=str(elsewhere)))
    assert f"Dossier des jeux : {elsewhere} (1 archive(s)" in out


def test_the_upload_rule_is_stated(tmp_path):
    config = config_for(tmp_path)
    make_roms(config)
    config.agent_host = "192.168.1.20"
    assert "sans code pour le PC associé (192.168.1.20)" in render(config)
    config.agent_host = ""
    assert "aucun pour l'instant" in render(config)
    config.upload_without_code = False
    assert "le code de l'écran est exigé pour tous" in render(config)


def test_the_configured_pin_is_shown(tmp_path):
    out = render(config_for(tmp_path, button_gpio=22))
    assert "GPIO22 (broche 15)" in out


# --- le bouton et la saisie -----------------------------------------------------------------------------------------------


def test_missing_gpio_pieces_each_get_a_fix(tmp_path):
    out = render(
        config_for(tmp_path),
        libgpiod=lambda: False,
        gpiochips=lambda: [],
        groups=groups_of(gpio=set(), input=set(), bluetooth=set()),
    )
    assert "python3-libgpiod est absente" in out
    assert "sudo apt install python3-libgpiod" in out
    assert "Aucune puce GPIO" in out
    assert "mydock-kiosk n'est pas dans le groupe gpio" in out
    assert "mydock-kiosk n'est pas dans le groupe input" in out
    assert "mydock n'est pas dans le groupe bluetooth" in out


def test_a_missing_group_is_reported_not_crashed(tmp_path):
    out = render(config_for(tmp_path), groups=groups_of())
    assert "Le groupe gpio n'existe pas" in out
    assert "Le groupe input n'existe pas" in out


def test_in_desktop_mode_the_session_user_is_not_guessed(tmp_path):
    out = render(config_for(tmp_path), kiosk=lambda: None)
    assert "Mode bureau" in out
    assert "groupe gpio" in out


def test_a_usb_pad_is_named_as_such(tmp_path):
    usb = TOUCH_AND_PAD.replace("Bus=0005", "Bus=0003")
    out = render(config_for(tmp_path), devices=lambda: usb)
    assert "Xbox Wireless Controller (/dev/input/event6, USB)" in out


def test_no_touchscreen_and_no_pad_are_noticed(tmp_path):
    out = render(config_for(tmp_path), devices=lambda: "")
    assert "Aucun écran tactile" in out
    assert "Aucune manette vue par le noyau" in out


def test_the_helper_status_comes_from_the_player_log(tmp_path):
    journal = tmp_path / "player.log"
    journal.write_text(
        "2025-01-01 10:00:00 assistant de lecture démarré\n"
        "2025-01-01 10:00:01 bouton physique : GPIO17 (broche 11) : accès refusé — l'utilisateur de l'écran doit être dans le groupe gpio\n"
        "2025-01-01 10:00:09 bouton physique : appui (lecture en cours : aucune)\n"
    )
    out = render(config_for(tmp_path), journal=lambda: journal)
    assert (
        "Bouton physique, d'après l'assistant de l'écran : GPIO17 (broche 11) : accès refusé" in out
    )
    assert "appui (lecture" not in out
    journal.write_text("2025-01-01 10:00:01 bouton physique : GPIO17 (broche 11) : prêt.\n")
    assert (
        "✔ Bouton physique, d'après l'assistant de l'écran : GPIO17 (broche 11) : prêt."
        in render(config_for(tmp_path), journal=lambda: journal)
    )


def test_a_log_without_the_button_line_says_so(tmp_path):
    journal = tmp_path / "player.log"
    journal.write_text("rien\n")
    assert "n'a encore rien dit du bouton" in render(config_for(tmp_path), journal=lambda: journal)


# --- le Bluetooth ---------------------------------------------------------------------------------------------------------


def test_without_bluez_or_adapter_the_cause_is_named(tmp_path):
    out = render(config_for(tmp_path), which=lambda _n: None, adapters=lambda: [])
    assert "BlueZ est absent" in out
    assert "sudo apt install bluez" in out
    assert "Aucun adaptateur Bluetooth" in out


def test_a_blocked_bluetooth_gets_the_rfkill_command(tmp_path):
    out = render(config_for(tmp_path), rfkill=lambda: True)
    assert "sudo rfkill unblock bluetooth" in out


def test_a_bluez_that_does_not_answer_is_not_a_failure(tmp_path):
    out = render(config_for(tmp_path), pads=lambda: None)
    assert "BlueZ n'a pas répondu d'ici" in out


def test_a_bluez_that_says_unavailable_is_reported(tmp_path):
    out = render(
        config_for(tmp_path),
        pads=lambda: {"available": False, "reason": "Aucun adaptateur Bluetooth.", "devices": []},
    )
    assert "BlueZ : Aucun adaptateur Bluetooth." in out


def test_no_paired_pad_is_not_an_error(tmp_path):
    out = render(
        config_for(tmp_path), pads=lambda: {"available": True, "reason": None, "devices": []}
    )
    assert "Aucune manette associée" in out


def test_extra_mame_options_are_listed(tmp_path, monkeypatch):
    flags = tmp_path / "mame.flags"
    flags.write_text("# x\n-video opengl\n")
    monkeypatch.setenv("MYDOCK_MAME_FLAGS", str(flags))
    assert "-video opengl" in render(config_for(tmp_path))


# --- les petits outils ------------------------------------------------------------------------------------------------------


def test_group_members_of_an_unknown_group():
    assert group_members("groupe-qui-n-existe-pas-xyz") == (False, set())


def test_group_members_of_root_include_its_primary_users():
    exists, members = group_members("root")
    assert exists
    assert "root" in members


def test_mame_version_of_a_missing_program():
    assert mame_version("/nulle/part/mame") == "version inconnue"


def test_mame_version_reads_the_first_line(tmp_path):
    script = tmp_path / "mame"
    script.write_text("#!/bin/sh\necho '0.264 (unknown)'\necho autre\n")
    script.chmod(0o755)
    assert mame_version(str(script)) == "0.264 (unknown)"
    assert os.access(script, os.X_OK)


# --- la place libre pour les envois, et l'écran du dock ---------------------------------------------------------------


Usage = namedtuple("Usage", "total used free")


def test_a_card_with_little_room_says_the_biggest_game_it_accepts(tmp_path, monkeypatch):
    config = config_for(tmp_path)
    make_roms(config, "pacman.zip")
    monkeypatch.setattr("mydock_dock.doctor.shutil.disk_usage", lambda _p: Usage(0, 0, 700 << 20))
    out = render(config)
    assert "Place libre pour les jeux : 0.7 Go" in out
    assert "le dock en garde 500 Mo libres" in out
    assert "un jeu de 200 Mo au plus" in out


def test_a_card_with_less_than_the_reserve_accepts_no_game(tmp_path, monkeypatch):
    config = config_for(tmp_path)
    make_roms(config, "pacman.zip")
    monkeypatch.setattr("mydock_dock.doctor.shutil.disk_usage", lambda _p: Usage(0, 0, 300 << 20))
    assert "un jeu de 0 Mo au plus" in render(config)


def test_a_card_with_plenty_of_room_stays_quiet_about_the_reserve(tmp_path, monkeypatch):
    config = config_for(tmp_path)
    make_roms(config, "pacman.zip")
    monkeypatch.setattr("mydock_dock.doctor.shutil.disk_usage", lambda _p: Usage(0, 0, 5 << 30))
    out = render(config)
    assert "✔ Place libre pour les jeux : 5.0 Go" in out
    assert "en garde" not in out


@pytest.fixture
def health_server():
    """Un faux dock qui répond à /api/health ce qu'on lui dit."""
    answer = {"body": {}}

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            payload = json.dumps(answer["body"]).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, *_args):
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield SimpleNamespace(port=server.server_address[1], answer=answer)
    server.shutdown()
    thread.join()


def running_report(health_server, **body) -> str:
    health_server.answer["body"] = {"ok": True, "link": "connected", **body}
    report = Report()
    check_running(report, SimpleNamespace(port=health_server.port))  # type: ignore[arg-type]
    return report.render()


def test_a_dock_without_a_browser_is_called_a_grey_screen(health_server):
    out = running_report(health_server, browsers=0)
    assert "⚠ Aucun navigateur n'est connecté au dock : l'écran est gris ou vide" in out
    assert "sudo pkill -x chromium" in out


def test_a_dock_with_its_browser_is_green(health_server):
    assert "✔ L'écran du dock est connecté (1 navigateur(s))" in running_report(
        health_server, browsers=1
    )


def test_an_older_dock_that_does_not_count_browsers_is_not_blamed(health_server):
    out = running_report(health_server)
    assert "Le serveur dock répond" in out
    assert "navigateur" not in out.split("Le serveur dock répond")[1].split("\n")[0]
    assert "Aucun navigateur" not in out
