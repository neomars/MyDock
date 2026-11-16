"""`mydock-dock doctor` : la section Free TV dit ce qui manque pour ouvrir tv.free.fr, et ce qui reste fragile."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from mydock_common.report import Report
from mydock_dock import doctor as doctor_module
from mydock_dock.config import DockConfig
from mydock_dock.doctor import check_freetv, check_freetv_network, free_tv_pids


def config_for(tmp_path, **freetv) -> DockConfig:
    return DockConfig.from_dict(
        {"paths": {"state_dir": str(tmp_path / "state")}, "freetv": freetv}, tmp_path / "d.toml"
    )


CHROMIUM_LINE = "Chromium 156.0.7339.80 built on Debian GNU/Linux 13 (trixie)"


def report_for(config, tmp_path, **kw) -> str:
    report = Report()
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    defaults = {
        "find": lambda: "/usr/bin/chromium",
        "version": lambda _p: CHROMIUM_LINE,
        "widevine": lambda: ("/opt/WidevineCdm", "4.10.2662.3"),
        "keyboard": lambda: "/usr/bin/wvkbd-mobintl",
        "groups": lambda _g: (True, {"mydock-kiosk"}),
        "kiosk": lambda: "mydock-kiosk",
        "home": lambda _u: home,
        "flags": lambda: [],
        "opened": lambda: [],
        "journal": lambda: None,
    }
    check_freetv(report, config, **{**defaults, **kw})
    return report.render()


def test_everything_in_place_is_green_and_the_identity_workaround_is_called_fragile(tmp_path):
    out = report_for(config_for(tmp_path), tmp_path)
    assert "✔ Chromium présent (/usr/bin/chromium, Chromium 156.0.7339.80" in out
    assert "✔ Widevine présent (/opt/WidevineCdm, version 4.10.2662.3)" in out
    assert "Chrome/156.0.0.0" in out  # l'identité calculée avec la version de Chromium
    assert "contournement fragile" in out
    assert "✔ Clavier à l'écran présent" in out
    assert "✔ mydock-kiosk est dans le groupe input" in out
    assert "⚠" not in out


def test_without_chromium_or_widevine_the_doctor_says_what_to_install(tmp_path):
    out = report_for(config_for(tmp_path), tmp_path, find=lambda: None, widevine=lambda: None)
    assert "⚠ Chromium est absent" in out
    assert "sudo apt install chromium" in out
    assert "⚠ Widevine est absent" in out
    assert "sudo apt install libwidevinecdm0" in out


def test_the_identity_follows_the_setting_and_a_version_that_cannot_be_read_is_a_warning(tmp_path):
    out = report_for(config_for(tmp_path, user_agent=""), tmp_path)
    assert "Identité de Chromium d'origine" in out
    assert "Chrome/156" not in out
    out = report_for(config_for(tmp_path, user_agent="Mon UA/{major}"), tmp_path)
    assert "Mon UA/156" in out
    out = report_for(config_for(tmp_path), tmp_path, version=lambda _p: None)
    assert "⚠ Version de Chromium illisible" in out


def test_without_the_keyboard_the_button_is_said_not_to_work_and_a_usb_one_is_suggested(tmp_path):
    out = report_for(config_for(tmp_path), tmp_path, keyboard=lambda: None)
    assert "⚠ Pas de clavier à l'écran" in out
    assert "sudo apt install wvkbd" in out
    assert "clavier USB" in out


def test_the_corner_gesture_needs_the_input_group(tmp_path):
    out = report_for(config_for(tmp_path), tmp_path, groups=lambda _g: (True, set()))
    assert "⚠ mydock-kiosk n'est pas dans le groupe input" in out
    assert "le bouton physique et Select + Start oui" in out
    out = report_for(config_for(tmp_path), tmp_path, groups=lambda _g: (False, set()))
    assert "⚠ Le groupe input n'existe pas" in out
    out = report_for(config_for(tmp_path), tmp_path, kiosk=lambda: None)
    assert "Mode bureau" in out


def test_the_settings_and_the_extra_options_are_shown(tmp_path):
    out = report_for(
        config_for(tmp_path, scale=0.8, corner="bottom-right", fullscreen=False),
        tmp_path,
        flags=lambda: ["--disable-gpu"],
    )
    assert "zoom 0.8, fenêtre maximisée, retour au dock par un appui long bottom-right" in out
    assert "Options de Chromium ajoutées" in out
    assert "--disable-gpu" in out


def test_the_profile_is_checked_for_private_rights_and_said_to_be_created_later(tmp_path):
    out = report_for(config_for(tmp_path), tmp_path)
    assert "créé à la première ouverture de Free TV" in out
    profile = tmp_path / "home" / ".config" / "mydock-freetv"
    profile.mkdir(parents=True)
    profile.chmod(0o700)
    assert "✔ Profil de Free TV" in report_for(config_for(tmp_path), tmp_path)
    profile.chmod(0o755)
    out = report_for(config_for(tmp_path), tmp_path)
    assert "⚠ Profil de Free TV" in out
    assert "lisible par d'autres" in out
    assert "chmod 700" in out


def test_what_is_open_and_the_last_word_of_the_helper_are_shown(tmp_path):
    out = report_for(config_for(tmp_path), tmp_path, opened=lambda: [1234])
    assert "Free TV est ouvert (Chromium 1234)" in out
    assert "n'a encore rien dit de Free TV" in out
    journal = tmp_path / "player.log"
    journal.write_text(
        "2026-11-16 09:00:00 free tv 1 : la fenêtre est affichée après 1.2 s (identifiant « chrome-tv.free.fr__-Default »)\n",
        encoding="utf-8",
    )
    out = report_for(config_for(tmp_path), tmp_path, journal=lambda: journal)
    assert (
        "Dernier mot de l'assistant de l'écran sur Free TV : free tv 1 : la fenêtre est affichée"
        in out
    )


def test_the_free_tv_chromium_is_found_by_its_profile_and_the_dock_one_is_not(tmp_path):
    proc = tmp_path / "proc"
    for pid, args in {
        "11": [
            "chromium",
            "--app=https://tv.free.fr",
            "--user-data-dir=/home/k/.config/mydock-freetv",
        ],
        "12": ["chromium", "--kiosk", "--user-data-dir=/home/k/.cache/mydock-chromium"],
        "13": ["vim", "mydock-freetv"],
    }.items():
        (proc / pid).mkdir(parents=True)
        (proc / pid / "cmdline").write_bytes("\0".join(args).encode() + b"\0")
    assert free_tv_pids(str(proc)) == [11]
    assert free_tv_pids(str(tmp_path / "rien")) == []


# --- le réseau ----------------------------------------------------------------------------------------------------------


async def network_report(**kw) -> str:
    report = Report()
    await check_freetv_network(report, **kw)
    return report.render()


async def test_offline_nothing_is_probed():
    assert "Test de tv.free.fr ignoré (--offline)" in await network_report(online=False)


async def test_an_unknown_host_is_a_dns_warning(monkeypatch):
    async def no(*_a, **_k):
        return False

    monkeypatch.setattr(doctor_module.freetv_playback, "host_resolves", no)
    out = await network_report()
    assert "⚠ tv.free.fr est introuvable (DNS)" in out


async def test_a_host_that_answers_is_checked_with_a_verified_certificate(monkeypatch):
    async def yes(*_a, **_k):
        return True

    calls: list[dict] = []

    class Writer:
        def close(self):
            calls.append({"closed": True})

    async def connect(host, port, *, ssl, server_hostname):
        calls.append(
            {"host": host, "port": port, "verify": ssl.verify_mode, "name": server_hostname}
        )
        return object(), Writer()

    import ssl as ssl_module

    monkeypatch.setattr(doctor_module.freetv_playback, "host_resolves", yes)
    monkeypatch.setattr(asyncio, "open_connection", connect)
    out = await network_report()
    assert "✔ tv.free.fr répond (TLS vérifié)" in out
    assert calls[0] == {
        "host": "tv.free.fr",
        "port": 443,
        "verify": ssl_module.CERT_REQUIRED,
        "name": "tv.free.fr",
    }  # jamais sans vérifier


async def test_a_refused_connection_and_a_bad_certificate_are_told_apart(monkeypatch):
    import ssl as ssl_module

    async def yes(*_a, **_k):
        return True

    monkeypatch.setattr(doctor_module.freetv_playback, "host_resolves", yes)

    async def refused(*_a, **_k):
        raise ConnectionRefusedError("refusée")

    monkeypatch.setattr(asyncio, "open_connection", refused)
    assert "⚠ tv.free.fr ne répond pas sur le port 443" in await network_report()

    async def bad_cert(*_a, **_k):
        raise ssl_module.SSLCertVerificationError("certificate has expired")

    monkeypatch.setattr(asyncio, "open_connection", bad_cert)
    out = await network_report()
    assert "certificat n'est pas accepté" in out
    assert "l'heure du Pi est-elle juste" in out


def test_the_doctor_message_about_a_gray_screen_no_longer_kills_every_chromium():
    source = Path(doctor_module.__file__).read_text(encoding="utf-8")
    assert "pkill -x chromium" not in source
    assert "mydock-chromium" in source


@pytest.mark.parametrize("name", ["check_freetv", "check_freetv_network"])
def test_the_checks_are_part_of_the_doctor(name):
    source = Path(doctor_module.__file__).read_text(encoding="utf-8")
    assert f"{name}(report" in source.split("def run_doctor", 1)[1]
