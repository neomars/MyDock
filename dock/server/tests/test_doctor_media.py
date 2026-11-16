"""`mydock-dock doctor` : la section Divertissement dit ce qui manque sur le Pi et si le serveur de fichiers répond."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from mydock_common.report import Report
from mydock_dock.config import DockConfig
from mydock_dock.doctor import check_media, check_media_network, player_session_running

DEMO = Path(__file__).resolve().parents[3] / "scripts" / "demo"


def config_for(tmp_path, **media) -> DockConfig:
    return DockConfig.from_dict(
        {"paths": {"state_dir": str(tmp_path / "state")}, "media": media}, tmp_path / "d.toml"
    )


ALL = ("vlc", "wlrctl", "swayidle", "xwayland", "smbclient")


PLUGINS = frozenset({"smb", "avcodec", "xcb_x11", "gl"})


def report_for(
    config,
    *,
    tools=ALL,
    running=True,
    flags=(),
    plugins=PLUGINS,
    journal=None,
    failures=(),
) -> str:
    report = Report()
    check_media(
        report,
        config,
        which=lambda name: f"/usr/bin/{name}" if name in tools else None,
        running=lambda: running,
        flags=lambda: list(flags),
        plugin=lambda name: name in plugins,
        journal=lambda: journal,
        failures=lambda: list(failures),
    )
    return report.render()


# --- ce qui est installé -------------------------------------------------------------------------------------------------


def test_everything_in_place_is_green(tmp_path):
    out = report_for(config_for(tmp_path))
    assert "✔ VLC présent" in out
    assert "✔ wlrctl présent" in out
    assert "✔ swayidle présent" in out
    assert "✔ smbclient présent" in out
    assert "✔ L'assistant de lecture tourne" in out
    assert "⚠" not in out
    assert "XWayland est absent" not in out


def test_vlc_modules_are_checked_one_by_one_with_the_package_that_brings_them(tmp_path):
    out = report_for(config_for(tmp_path))
    assert "✔ VLC sait lire les fichiers du Samba" in out
    assert "✔ VLC sait décoder les vidéos" in out
    assert "aucun module d'affichage" not in out
    out = report_for(config_for(tmp_path), plugins=frozenset({"avcodec", "xcb_x11"}))
    assert (
        "⚠ VLC ne sait pas lire les fichiers du Samba (Kiosque) : sudo apt install vlc-plugin-samba"
        in out
    )
    out = report_for(config_for(tmp_path), plugins=frozenset({"smb", "xcb_x11"}))
    assert "⚠ VLC ne sait pas décoder les vidéos : sudo apt install vlc-plugin-base" in out
    out = report_for(config_for(tmp_path), plugins=frozenset({"smb", "avcodec"}))
    assert "⚠ VLC n'a aucun module d'affichage : sudo apt install vlc-plugin-video-output" in out


def test_the_journal_is_named_so_it_can_be_read_and_the_last_failures_are_shown(tmp_path):
    out = report_for(config_for(tmp_path), journal=Path("/var/log/mydock/player.log"))
    assert (
        "Journal de la lecture : /var/log/mydock/player.log (à lire avec : sudo cat /var/log/mydock/player.log)"
        in out
    )
    assert "dock.log" in out
    assert "Lecture ratée" not in out
    row = "2026-11-12 09:00:00 ÉCHEC lecture 3 : Accès refusé au partage"
    out = report_for(
        config_for(tmp_path), journal=Path("/var/log/mydock/player.log"), failures=[row]
    )
    assert f"⚠ Lecture ratée, d'après le journal : {row}" in out


def test_without_a_journal_yet_the_doctor_says_where_it_will_be(tmp_path):
    out = report_for(config_for(tmp_path), journal=None)
    assert "/var/log/mydock/player.log (créé à la première lecture" in out
    assert "sudo /usr/lib/mydock/setup" in out


def test_without_vlc_its_modules_are_not_blamed(tmp_path):
    out = report_for(
        config_for(tmp_path),
        tools=("wlrctl", "swayidle", "smbclient"),
        plugins=frozenset(),
    )
    assert "⚠ VLC est absent" in out
    assert "VLC ne sait pas" not in out


def test_the_modules_are_looked_for_in_the_plugin_directories(monkeypatch):
    from mydock_dock import player_session

    seen: list[str] = []
    monkeypatch.setattr(player_session.glob, "glob", lambda pattern: seen.append(pattern) or [])
    assert player_session.has_vlc_plugin("smb") is False
    assert seen == [
        "/usr/lib/*/vlc/plugins/*/libsmb_plugin.so",
        "/usr/lib/vlc/plugins/*/libsmb_plugin.so",
    ]
    monkeypatch.setattr(
        player_session.glob,
        "glob",
        lambda pattern: ["/usr/lib/x/vlc/plugins/access/libsmb_plugin.so"],
    )
    assert player_session.has_vlc_plugin("smb") is True


def test_without_vlc_the_screen_says_what_to_install(tmp_path):
    out = report_for(config_for(tmp_path), tools=("wlrctl", "swayidle", "smbclient"))
    assert "⚠ VLC est absent" in out
    assert "sudo apt install vlc-bin vlc-plugin-base vlc-plugin-video-output" in out


def test_xdotool_is_enough_when_wlrctl_is_missing(tmp_path):
    tools = ("vlc", "xdotool", "swayidle", "smbclient")
    out = report_for(config_for(tmp_path), tools=tools)
    assert "✔ xdotool présent" in out
    assert "⚠" not in out
    tools = ("vlc", "swayidle", "smbclient")
    out = report_for(config_for(tmp_path), tools=tools)
    assert "⚠ Ni wlrctl ni xdotool" in out


def test_a_missing_touch_watcher_or_smb_client_is_a_warning(tmp_path):
    out = report_for(config_for(tmp_path), tools=("vlc", "wlrctl", "smbclient"))
    assert "⚠ swayidle est absent" in out
    assert "sudo apt install swayidle" in out
    out = report_for(config_for(tmp_path), tools=("vlc", "wlrctl", "swayidle"))
    assert "⚠ smbclient est absent" in out
    assert "sudo apt install smbclient" in out


def test_a_helper_that_is_not_running_says_when_it_starts(tmp_path):
    out = report_for(config_for(tmp_path), running=False)
    assert "L'assistant de lecture ne tourne pas" in out
    assert "sudo reboot" in out
    assert "⚠" not in out  # normal avant l'ouverture de la session de l'écran


def test_extra_vlc_options_are_listed(tmp_path):
    out = report_for(config_for(tmp_path), flags=["--aout=alsa", "--avcodec-hw=none"])
    assert "Options de VLC ajoutées" in out
    assert "--aout=alsa --avcodec-hw=none" in out


# --- les réglages ------------------------------------------------------------------------------------------------


def test_the_settings_are_shown_but_never_the_password(tmp_path):
    secret = "pas-a-afficher-123"
    config = config_for(
        tmp_path,
        smb_host="mafreebox.freebox.fr",
        smb_user="freebox",
        smb_password=secret,
    )
    out = report_for(config)
    assert "Liste des chaînes" not in out  # Freetv a été remplacé par Free TV
    assert (
        "mafreebox.freebox.fr (identifiant « freebox », mot de passe configuré (non affiché))"
        in out
    )
    assert secret not in out


def test_an_anonymous_share_and_a_missing_server_are_told(tmp_path):
    assert "accès sans identifiant, sans mot de passe" in report_for(config_for(tmp_path))
    out = report_for(config_for(tmp_path, smb_host=""))
    assert "aucun serveur (Réglages → Divertissement)" in out


# --- le réseau ----------------------------------------------------------------------------------------------------


@pytest.fixture
def demo_smbclient(tmp_path, monkeypatch):
    """Le faux smbclient de la pile de démonstration, seul de son espèce dans le PATH."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for name in ("smbclient",):
        shutil.copy(DEMO / name, bin_dir / name)
        (bin_dir / name).chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}:/usr/bin:/bin")


async def network_report(config, *, online=True) -> str:
    report = Report()
    await check_media_network(report, config, online=online)
    return report.render()


async def test_a_server_that_answers_gives_its_shares(tmp_path, demo_smbclient):
    config = config_for(tmp_path, smb_host="127.0.0.1")
    out = await network_report(config)
    assert "✔ 127.0.0.1 répond : 2 partage(s) (Disque dur, Enregistrements)" in out
    assert "⚠" not in out
    assert "Liste des chaînes" not in out


async def test_a_share_server_that_fails_is_a_warning(tmp_path, monkeypatch):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    fake = bin_dir / "smbclient"
    fake.write_text(
        "#!/bin/sh\necho 'Connection to host failed (Error NT_STATUS_HOST_UNREACHABLE)'\nexit 1\n"
    )
    fake.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}:/usr/bin:/bin")
    config = config_for(tmp_path, smb_host="192.0.2.50")
    out = await network_report(config)
    assert "⚠ Partages Samba" in out


async def test_offline_nothing_is_probed(tmp_path):
    config = config_for(tmp_path, smb_host="192.0.2.50")
    out = await network_report(config, online=False)
    assert "Test du serveur de fichiers ignoré (--offline)" in out
    assert "⚠" not in out


def test_the_helper_is_detected_by_its_command_line():
    assert player_session_running() in (True, False)  # pgrep absent ou présent : jamais d'exception
