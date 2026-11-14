"""Tests des scripts de déploiement, sans Pi ni systemd : installation réelle dans un dossier temporaire.

Ce qui est vérifié ici : les fichiers écrits, leurs droits, l'idempotence, la simulation, la
reprise d'une ancienne installation, le retrait, les refus (nom introuvable, root sur le PC) et le
lanceur du kiosque (avec un faux Chromium).
Ce qui ne peut pas l'être ici et reste à valider sur les vraies machines : apt, useradd, systemd,
ufw, NetworkManager, labwc, Chromium et l'écran lui-même.
"""

from __future__ import annotations

import contextlib
import os
import re
import shutil
import signal
import stat
import subprocess
import sys
import textwrap
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
PC = ROOT / "deploy" / "pc"
PI = ROOT / "deploy" / "pi"
ALL_SCRIPTS = [ROOT / "deploy" / "lib.sh", *sorted(PC.glob("*.sh")), *sorted(PI.glob("*.sh"))]
TOKEN = "T" * 8 + "-jeton-de-test-pour-le-deploiement-" + "x" * 10  # 24+ caractères, motif sûr


def sh(cmd: list[str | Path], *, env: dict[str, str], input: str | None = None, timeout: int = 600):
    return subprocess.run(
        [str(c) for c in cmd],
        capture_output=True,
        text=True,
        env=env,
        input=input,
        timeout=timeout,
        check=False,
        stdin=None if input is not None else subprocess.DEVNULL,
    )


def base_env(**extra: str) -> dict[str, str]:
    env = {
        k: v
        for k, v in os.environ.items()
        if not k.startswith(("XDG_", "MYDOCK_", "WAYLAND", "DISPLAY"))
    }
    env.update(NO_COLOR="1", PYTHON=sys.executable, **extra)
    return env


def mode(path: Path) -> int:
    return stat.S_IMODE(path.stat().st_mode)


@pytest.fixture(scope="session")
def wheelhouse(tmp_path_factory) -> Path:
    """Roues de MyDock et de leurs dépendances, construites une fois : les installations de test
    se font ensuite hors ligne (variables PIP_NO_INDEX / PIP_FIND_LINKS lues par pip lui-même)."""
    work = tmp_path_factory.mktemp("wheelhouse-venv")
    wheels = tmp_path_factory.mktemp("wheelhouse")
    subprocess.run([sys.executable, "-m", "venv", str(work)], check=True)
    pip = work / "bin" / "python"
    subprocess.run(
        [str(pip), "-m", "pip", "wheel", "--quiet", "--disable-pip-version-check", "--wheel-dir", str(wheels),
         "hatchling", str(ROOT / "common"), str(ROOT / "agent"), str(ROOT / "dock" / "server")],
        check=True,
    )  # fmt: skip
    return wheels


def offline(wheels: Path) -> dict[str, str]:
    return {
        "PIP_NO_INDEX": "1",
        "PIP_FIND_LINKS": str(wheels),
        "PIP_DISABLE_PIP_VERSION_CHECK": "1",
    }


# --- hygiène générale ---------------------------------------------------------------------------


@pytest.mark.parametrize("script", ALL_SCRIPTS, ids=lambda p: str(p.relative_to(ROOT)))
def test_scripts_are_valid_bash_and_executable(script):
    shell = (
        "sh"
        if script.name in ("kiosk-browser.sh", "screen-sleep.sh", "player-session.sh")
        else "bash"
    )
    assert subprocess.run([shell, "-n", str(script)], check=False).returncode == 0
    if script.name != "lib.sh":
        assert os.access(script, os.X_OK), f"{script} n'est pas exécutable"


@pytest.mark.skipif(shutil.which("shellcheck") is None, reason="shellcheck absent")
def test_shellcheck_is_clean():
    result = subprocess.run(
        ["shellcheck", "-x", *map(str, ALL_SCRIPTS)], capture_output=True, text=True, check=False
    )
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize("script", [*sorted(PC.glob("install-*.sh")), PI / "setup.sh"])
def test_help_is_in_french_and_exits_cleanly(script):
    result = sh([script, "--help"], env=base_env())
    assert result.returncode == 0
    assert "Usage" in result.stdout
    assert "--dry-run" in result.stdout


# --- PC : agent --------------------------------------------------------------------------------


@pytest.fixture(scope="module")
def pc_install(tmp_path_factory, wheelhouse):
    home = tmp_path_factory.mktemp("pc-home")
    env = base_env(HOME=str(home), MYDOCK_ALLOW_ROOT="1", **offline(wheelhouse))
    args = [
        "--pi",
        "192.0.2.50",
        "--pi",
        "198.51.100.0/24",
        "--no-apt",
        "--no-firewall",
        "--no-systemd",
    ]
    first = sh([PC / "install-agent.sh", *args], env=env)
    config = home / ".config" / "mydock" / "agent.toml"
    snapshot = config.read_bytes() if config.exists() else b""
    second = sh([PC / "install-agent.sh", *args], env=env)
    return {
        "home": home,
        "env": env,
        "args": args,
        "first": first,
        "second": second,
        "config": config,
        "snapshot": snapshot,
    }


@pytest.mark.slow
def test_pc_first_install_creates_a_private_config_and_a_working_agent(pc_install):
    first, home, config = pc_install["first"], pc_install["home"], pc_install["config"]
    assert first.returncode == 0, first.stdout + first.stderr
    assert mode(config) == 0o600
    assert mode(config.parent) == 0o700
    text = config.read_text()
    assert "192.0.2.50/32" in text
    assert "198.51.100.0/24" in text
    venv = home / ".local" / "share" / "mydock" / "venv"
    version = sh([venv / "bin" / "mydock-agent", "--version"], env=pc_install["env"])
    assert version.stdout.startswith("mydock-agent 1.7.2")


@pytest.mark.slow
def test_pc_unit_file_points_at_the_installed_venv(pc_install):
    unit = pc_install["home"] / ".config" / "systemd" / "user" / "mydock-agent.service"
    text = unit.read_text()
    venv = pc_install["home"] / ".local" / "share" / "mydock" / "venv"
    assert f"ExecStart={venv}/bin/mydock-agent run" in text
    assert "WantedBy=graphical-session.target" in text
    assert not re.findall(r"@[A-Z_]+@", text)  # tous les marqueurs @NOM@ sont remplacés
    assert mode(unit) == 0o644


@pytest.mark.slow
def test_pc_never_prints_the_token_but_says_how_to_read_it(pc_install):
    first, second = pc_install["first"], pc_install["second"]
    venv = pc_install["home"] / ".local" / "share" / "mydock" / "venv"
    token = sh([venv / "bin" / "mydock-agent", "show-token"], env=pc_install["env"]).stdout.strip()
    assert len(token) >= 43
    # Le dock reçoit le jeton pendant l'association : plus rien à recopier, donc rien à afficher.
    assert token not in first.stdout + first.stderr
    assert token not in second.stdout + second.stderr
    assert second.returncode == 0, second.stdout + second.stderr
    assert "mydock-agent show-token" in first.stdout  # pour une saisie à la main


@pytest.mark.slow
def test_pc_second_run_changes_nothing(pc_install):
    assert pc_install["config"].read_bytes() == pc_install["snapshot"]
    assert "= inchangé" in pc_install["second"].stdout
    assert "+ écrit" not in pc_install["second"].stdout


@pytest.mark.slow
def test_pc_rotating_the_token_gives_a_new_one_and_says_to_pair_again(pc_install):
    venv = pc_install["home"] / ".local" / "share" / "mydock" / "venv"
    before = sh([venv / "bin" / "mydock-agent", "show-token"], env=pc_install["env"]).stdout.strip()
    result = sh(
        [PC / "install-agent.sh", *pc_install["args"], "--rotate-token"], env=pc_install["env"]
    )
    after = sh([venv / "bin" / "mydock-agent", "show-token"], env=pc_install["env"]).stdout.strip()
    assert result.returncode == 0, result.stdout + result.stderr
    assert after != before
    assert after not in result.stdout
    assert "mydock-agent pair --pi 192.0.2.50" in result.stdout  # le dock reprend le nouveau jeton


@pytest.mark.slow
def test_pc_final_message_gives_the_pairing_command(pc_install):
    out = pc_install["first"].stdout
    venv = pc_install["home"] / ".local" / "share" / "mydock" / "venv"
    assert f"{venv}/bin/mydock-agent pair --pi 192.0.2.50" in out  # l'adresse du Pi donnée à --pi
    assert "install-dock" not in out  # l'ancien installateur du Pi n'existe plus
    assert "bail DHCP" in out
    assert "Réglages du dock → PC piloté" in out  # la saisie à la main reste possible


def test_pc_script_steps_aside_when_the_deb_package_is_installed(tmp_path):
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    stub = fake_bin / "dpkg-query"
    stub.write_text("#!/bin/sh\necho 'install ok installed'\n")
    stub.chmod(0o755)
    env = base_env(HOME=str(tmp_path), MYDOCK_ALLOW_ROOT="1")
    env["PATH"] = f"{fake_bin}:{env['PATH']}"
    result = sh([PC / "install-agent.sh", "--pi", "192.0.2.50", "--no-apt"], env=env)
    assert result.returncode != 0
    assert "paquet mydock-agent est installé" in result.stderr
    assert "mydock-agent setup" in result.stderr
    assert not (tmp_path / ".config" / "mydock").exists()  # rien n'a été touché


def test_pc_dry_run_touches_nothing(tmp_path):
    env = base_env(HOME=str(tmp_path), MYDOCK_ALLOW_ROOT="1")
    result = sh([PC / "install-agent.sh", "--pi", "192.0.2.50", "--no-apt", "--dry-run"], env=env)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "simulation" in result.stdout
    assert list(tmp_path.iterdir()) == []


@pytest.mark.skipif(os.getuid() != 0, reason="le refus ne s'applique qu'à root")
def test_pc_refuses_to_run_as_root(tmp_path):
    env = base_env(HOME=str(tmp_path))
    result = sh([PC / "install-agent.sh", "--no-apt", "--dry-run"], env=env)
    assert result.returncode != 0
    assert "Ne lance pas ce script avec sudo" in result.stderr


def test_pc_rejects_an_unresolvable_pi_name(tmp_path):
    env = base_env(HOME=str(tmp_path), MYDOCK_ALLOW_ROOT="1")
    result = sh(
        [PC / "install-agent.sh", "--pi", "introuvable.invalid", "--no-apt", "--no-systemd"],
        env=env,
    )
    assert result.returncode != 0
    assert "Impossible de résoudre" in result.stderr
    assert not (tmp_path / ".config").exists()  # rien n'a été écrit avant l'erreur


def test_pc_unknown_option_is_explained(tmp_path):
    result = sh([PC / "install-agent.sh", "--n-importe-quoi"], env=base_env(HOME=str(tmp_path)))
    assert result.returncode != 0
    assert "Option inconnue" in result.stderr


# --- Pi : intégration système du dock (deploy/pi/setup.sh, lancé par le paquet « mydock ») --------


def dock_command(directory: Path) -> Path:
    """Le « mydock-dock » du paquet, remplacé par le programme du dépôt (l'environnement de test)."""
    path = directory / "mydock-dock"
    path.write_text(f'#!/bin/sh\nexec "{sys.executable}" -m mydock_dock "$@"\n')
    path.chmod(0o755)
    return path


def setup_env(tmp_path: Path) -> dict[str, str]:
    return base_env(HOME=str(tmp_path), MYDOCK_DOCK_BIN=str(dock_command(tmp_path)))


@pytest.fixture(scope="module")
def pi_setup(tmp_path_factory):
    work = tmp_path_factory.mktemp("pi-setup")
    prefix = work / "root"
    env = setup_env(work)
    args = ["--prefix", str(prefix), "--kiosk", "systemd", "--force-mode", "1024x600@60"]
    first = sh([PI / "setup.sh", *args], env=env)
    config = prefix / "etc" / "mydock" / "dock.toml"
    snapshot = config.read_bytes() if config.exists() else b""
    second = sh([PI / "setup.sh", *args], env=env)
    return {
        "prefix": prefix,
        "env": env,
        "first": first,
        "second": second,
        "config": config,
        "snapshot": snapshot,
    }


def test_pi_setup_creates_a_private_config_for_a_dock_without_a_pc(pi_setup):
    first, config = pi_setup["first"], pi_setup["config"]
    assert first.returncode == 0, first.stdout + first.stderr
    assert mode(config) == 0o600
    text = config.read_text()
    # Un dock neuf n'a ni PC ni jeton : le PC s'associe avec un code (portail), rien n'est demandé.
    assert 'host = ""' in text
    assert 'token = ""' in text
    assert 'state_dir = "/var/lib/mydock"' in text
    assert 'web_dir = "/usr/share/mydock/web"' in text
    assert 'clients_dir = "/usr/share/mydock/clients"' in text
    assert 'listen_host = "127.0.0.1"' in text  # l'interface du dock reste locale...
    assert "[portal]" in text
    assert 'listen_host = "0.0.0.0"' in text  # ... seul le portail est sur le réseau local
    assert "port = 80" in text
    assert mode(pi_setup["prefix"] / "etc/mydock") == 0o750
    assert "--pc" not in first.stdout + first.stderr


def test_pi_setup_says_where_the_pc_goes_next(pi_setup):
    out = pi_setup["first"].stdout
    assert "ouvre   http://" in out
    assert ".local/" in out
    assert "mydock-dock doctor" in out


def test_pi_setup_creates_the_journal_directory_everyone_can_write_to(pi_setup):
    """player.log (session de l'écran) et dock.log (utilisateur « mydock ») : un dossier à bit collant, comme /tmp."""
    log_dir = pi_setup["prefix"] / "var/log/mydock"
    assert log_dir.is_dir()
    assert mode(log_dir) == 0o1777
    out = pi_setup["first"].stdout
    assert "/var/log/mydock/player.log" in out
    assert "/var/log/mydock/dock.log" in out


def test_pi_kiosk_configuration_is_readable_by_the_browser_user(pi_setup):
    prefix = pi_setup["prefix"]
    autostart = (prefix / "etc/mydock-kiosk/labwc/autostart").read_text()
    assert "/usr/lib/mydock/kiosk-browser.sh &" in autostart
    assert "/usr/lib/mydock/screen-sleep.sh &" in autostart  # la veille de l'écran
    assert (
        "/usr/lib/mydock/player-session.sh &" in autostart
    )  # l'assistant de lecture du Divertissement
    assert not re.findall(r"@[A-Z_]+@", autostart)
    assert (
        'MYDOCK_OUTPUT_MODE="1024x600@60"' in (prefix / "etc/mydock-kiosk/display.conf").read_text()
    )
    assert (prefix / "etc/mydock-kiosk/chromium.flags").is_file()
    assert (
        prefix / "etc/mydock-kiosk/vlc.flags"
    ).is_file()  # options de VLC en plus (Divertissement)
    for path in (prefix / "etc/mydock-kiosk").rglob("*"):
        if path.is_file():
            # lisible par mydock-kiosk, jamais ce qui tient le jeton (dans /etc/mydock)
            assert mode(path) == 0o644


def test_pi_setup_prepares_retrogame(pi_setup):
    """Le dossier des jeux est lisible par l'utilisateur de l'écran (MAME) sans ouvrir le reste de l'état du dock."""
    prefix = pi_setup["prefix"]
    assert mode(prefix / "var/lib/mydock") == 0o751  # traversable, jamais listable par les autres
    assert mode(prefix / "var/lib/mydock/roms") == 0o755
    assert mode(prefix / "etc/mydock") == 0o750  # la configuration (le jeton) reste fermée
    flags = (prefix / "etc/mydock-kiosk/mame.flags").read_text()
    assert flags.startswith("# Options supplémentaires de MAME")
    assert "# -video opengl" in flags
    assert mode(prefix / "etc/mydock-kiosk/mame.flags") == 0o644
    assert "Retrogame" in pi_setup["first"].stdout


def test_pi_setup_keeps_the_arcade_flags_the_user_edited(pi_setup):
    flags = pi_setup["prefix"] / "etc/mydock-kiosk/mame.flags"
    flags.write_text("-video opengl\n")
    again = sh(
        [
            PI / "setup.sh",
            "--prefix",
            str(pi_setup["prefix"]),
            "--kiosk",
            "systemd",
            "--force-mode",
            "1024x600@60",
        ],
        env=pi_setup["env"],
    )
    assert again.returncode == 0, again.stdout + again.stderr
    assert flags.read_text() == "-video opengl\n"


def test_pi_kiosk_unit_is_rendered(pi_setup):
    unit = (pi_setup["prefix"] / "etc/systemd/system/mydock-kiosk.service").read_text()
    assert "User=mydock-kiosk" in unit
    assert "ExecStart=/usr/bin/labwc --config-dir /etc/mydock-kiosk/labwc" in unit
    assert not re.findall(r"@[A-Z_]+@", unit)  # tous les marqueurs @NOM@ sont remplacés


def test_pi_setup_run_again_changes_nothing(pi_setup):
    second = pi_setup["second"]
    assert second.returncode == 0, second.stdout + second.stderr
    assert pi_setup["config"].read_bytes() == pi_setup["snapshot"]
    assert "= inchangé" in second.stdout
    assert "+ écrit" not in second.stdout


@pytest.mark.skipif(shutil.which("systemd-analyze") is None, reason="systemd-analyze absent")
@pytest.mark.parametrize("unit", ["mydock-dock.service", "mydock-kiosk.service"])
def test_pi_units_are_valid_systemd_syntax(pi_setup, tmp_path, unit):
    root = tmp_path / "root"
    (root / "etc/systemd/system").mkdir(parents=True)
    source = (
        ROOT / "packaging/deb-pi/mydock-dock.service"
        if unit == "mydock-dock.service"
        else pi_setup["prefix"] / "etc/systemd/system" / unit
    )
    (root / "etc/systemd/system" / unit).write_text(source.read_text())
    result = subprocess.run(
        ["systemd-analyze", "verify", "--man=no", f"--root={root}", f"/etc/systemd/system/{unit}"],
        capture_output=True,
        text=True,
        check=False,
    )
    problems = [
        line
        for line in (result.stdout + result.stderr).splitlines()
        if any(
            marker in line
            for marker in (
                "Unknown key",
                "Unknown section",
                "Failed to parse",
                "Invalid",
                "not a valid",
            )
        )
    ]
    assert problems == [], "\n".join(problems)


def test_pi_upgrade_from_the_old_installer_keeps_the_pc_and_the_token(tmp_path):
    prefix = tmp_path / "root"
    config = prefix / "etc" / "mydock" / "dock.toml"
    init = sh(
        [dock_command(tmp_path), "init", "--config", config, "--pc", "192.0.2.20", "--token-stdin",
         "--web-dir", "/opt/mydock/src/dock/web/dist", "--state-dir", "/var/lib/mydock"],
        env=base_env(HOME=str(tmp_path)),
        input=TOKEN + "\n",
    )  # fmt: skip
    assert init.returncode == 0, init.stdout + init.stderr
    unit = prefix / "etc/systemd/system/mydock-dock.service"
    unit.parent.mkdir(parents=True)
    unit.write_text("[Service]\nExecStart=/opt/mydock/venv/bin/mydock-dock run\n")

    result = sh(
        [PI / "setup.sh", "--prefix", str(prefix), "--kiosk", "none"], env=setup_env(tmp_path)
    )

    assert result.returncode == 0, result.stdout + result.stderr
    text = config.read_text()
    assert 'host = "192.0.2.20"' in text
    assert f'token = "{TOKEN}"' in text
    assert 'web_dir = "/usr/share/mydock/web"' in text  # l'ancien dossier n'existe plus
    assert mode(config) == 0o600
    assert not unit.exists()  # son unité masquerait celle du paquet
    assert "Ancienne unité mydock-dock retirée" in result.stdout
    assert "/opt/mydock" in result.stdout  # et on dit quoi supprimer
    assert TOKEN not in result.stdout + result.stderr


def test_pi_setup_never_deletes_a_unit_it_did_not_install(tmp_path):
    prefix = tmp_path / "root"
    unit = prefix / "etc/systemd/system/mydock-dock.service"
    unit.parent.mkdir(parents=True)
    unit.write_text("[Service]\nExecStart=/usr/bin/mydock-dock run\n")
    result = sh(
        [PI / "setup.sh", "--prefix", str(prefix), "--kiosk", "none"], env=setup_env(tmp_path)
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert unit.exists()


def test_pi_without_a_kiosk_writes_only_the_configuration(tmp_path):
    prefix = tmp_path / "root"
    result = sh(
        [PI / "setup.sh", "--prefix", str(prefix), "--kiosk", "none"], env=setup_env(tmp_path)
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert (prefix / "etc/mydock/dock.toml").is_file()
    assert not (prefix / "etc/mydock-kiosk").exists()
    assert not (prefix / "etc/systemd/system/mydock-kiosk.service").exists()


@pytest.mark.parametrize(
    ("extra", "message"),
    [
        (["--kiosk", "bizarre"], "valeur inconnue"),
        (["--force-mode", "grand"], "format attendu"),
        (["--kiosk"], "attend une valeur"),
        (["--n-importe-quoi"], "Option inconnue"),
    ],
)
def test_pi_bad_options_are_explained(tmp_path, extra, message):
    result = sh([PI / "setup.sh", "--prefix", str(tmp_path / "p"), *extra], env=setup_env(tmp_path))
    assert result.returncode != 0
    assert message in result.stderr
    assert not (tmp_path / "p").exists()  # rien n'est écrit avant l'erreur


def test_pi_dry_run_touches_nothing(tmp_path):
    prefix = tmp_path / "p"
    result = sh(
        [PI / "setup.sh", "--prefix", str(prefix), "--kiosk", "systemd", "--dry-run"],
        env=setup_env(tmp_path),
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "simulation" in result.stdout
    assert not prefix.exists() or [p for p in prefix.rglob("*") if p.is_file()] == []


def test_pi_refuses_to_run_without_root_outside_tests(tmp_path):
    if os.getuid() == 0:
        pytest.skip("le test suppose un utilisateur non root")
    result = sh([PI / "setup.sh"], env=setup_env(tmp_path))
    assert result.returncode != 0
    assert "avec sudo" in result.stderr


def desktop_install(tmp_path: Path, *extra: str):
    prefix = tmp_path / "p"
    home = prefix / "home" / "alice"
    (home / ".config" / "labwc").mkdir(parents=True, exist_ok=True)
    cmd = [
        PI / "setup.sh",
        "--prefix",
        prefix,
        "--kiosk",
        "desktop",
        "--kiosk-user",
        "alice",
        *extra,
    ]
    return prefix, home, sh(cmd, env=setup_env(tmp_path))


def test_pi_desktop_mode_edits_the_session_autostart_and_backs_up_the_original(tmp_path):
    prefix = tmp_path / "p"
    home = prefix / "home" / "alice"
    (home / ".config" / "labwc").mkdir(parents=True)
    original = "wf-panel-pi &\npcmanfm --desktop &\n"
    (home / ".config" / "labwc" / "autostart").write_text(original)
    for _ in range(2):  # deux fois : la sauvegarde ne doit être faite qu'une fois
        _, _, result = desktop_install(tmp_path)
        assert result.returncode == 0, result.stdout + result.stderr
    autostart = home / ".config" / "labwc" / "autostart"
    assert "/usr/lib/mydock/kiosk-browser.sh &" in autostart.read_text()
    assert "wf-panel-pi" not in autostart.read_text()  # le bureau ne se lance plus
    assert (autostart.parent / "autostart.avant-mydock").read_text() == original


def test_pi_removal_gives_the_desktop_back_and_keeps_the_configuration(tmp_path):
    prefix, home, _ = desktop_install(tmp_path)
    autostart = home / ".config" / "labwc" / "autostart"
    original = "wf-panel-pi &\n"
    backup = autostart.parent / "autostart.avant-mydock"
    autostart.write_text(original)
    desktop_install(tmp_path)  # sauvegarde l'autostart d'origine puis installe celui de MyDock
    assert backup.read_text() == original

    result = sh([PI / "setup.sh", "--prefix", str(prefix), "--remove"], env=setup_env(tmp_path))

    assert result.returncode == 0, result.stdout + result.stderr
    assert autostart.read_text() == original
    assert not backup.exists()
    assert not (prefix / "etc/mydock-kiosk").exists()
    assert (prefix / "etc/mydock/dock.toml").is_file()  # « apt remove » garde la configuration
    assert "apt purge mydock" in result.stdout


def test_pi_removal_without_a_backup_removes_only_what_mydock_wrote(tmp_path):
    prefix, home, _ = desktop_install(tmp_path)
    autostart = home / ".config" / "labwc" / "autostart"
    assert "MyDock" in autostart.read_text()
    other = home / ".config" / "labwc" / "rc.xml"
    other.write_text("<labwc_config/>")

    result = sh([PI / "setup.sh", "--prefix", str(prefix), "--remove"], env=setup_env(tmp_path))

    assert result.returncode == 0, result.stdout + result.stderr
    assert not autostart.exists()
    assert other.read_text() == "<labwc_config/>"


def test_pi_removal_leaves_an_autostart_that_is_not_ours(tmp_path):
    prefix = tmp_path / "p"
    autostart = prefix / "home" / "bob" / ".config" / "labwc" / "autostart"
    autostart.parent.mkdir(parents=True)
    autostart.write_text("wf-panel-pi &\n")
    result = sh([PI / "setup.sh", "--prefix", str(prefix), "--remove"], env=setup_env(tmp_path))
    assert result.returncode == 0, result.stdout + result.stderr
    assert autostart.read_text() == "wf-panel-pi &\n"


# --- le lanceur du kiosque, avec un faux Chromium ---------------------------------------------------


def make_fake_bin(directory: Path, name: str, body: str) -> None:
    path = directory / name
    path.write_text("#!/bin/sh\n" + textwrap.dedent(body))
    path.chmod(0o755)


def run_launcher(env: dict[str, str], seconds: float, script: Path | None = None):
    """Lance le lanceur du kiosque, le laisse tourner `seconds` secondes, puis arrête tout son groupe de processus.

    Le lanceur boucle sans fin et tient un verrou par un processus fils : on arrête le groupe entier, sinon des lanceurs
    resteraient en vie après le test.
    """
    process = subprocess.Popen(
        ["sh", str(script or PI / "kiosk-browser.sh")],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=True,
    )
    try:
        out, err = process.communicate(timeout=seconds)
    except subprocess.TimeoutExpired:
        os.killpg(process.pid, signal.SIGKILL)
        out, err = process.communicate()
    return process.returncode, out, err


class Kiosk:
    """Un faux Pi autour du lanceur : un faux Chromium (qui note ses lancements), un faux dock (/api/health), de faux outils."""

    def __init__(self, tmp_path: Path) -> None:
        self.tmp = tmp_path
        self.bin = tmp_path / "bin"
        self.bin.mkdir()
        self.calls = tmp_path / "calls.log"
        self.profile = tmp_path / "profil"
        self.conf = tmp_path / "conf"
        self.conf.mkdir()
        self.windows = tmp_path / "windows.txt"
        self.health(browsers=1)
        self.chromium(stays=True)

    def health(self, *, browsers: int | None) -> None:
        """Ce que répond le dock : `browsers` navigateurs connectés (None : un dock qui ne le dit pas)."""
        body = '{"ok":true,"version":"1","link":"connected"' + (
            "" if browsers is None else f',"browsers":{browsers}'
        )
        make_fake_bin(self.bin, "curl", f"echo '{body}}}'\n")

    def chromium(self, *, stays: bool) -> None:
        """`stays` : Chromium reste en vie (comme un vrai) ; sinon il se ferme tout de suite."""
        tail = "exec sleep 60\n" if stays else "exit 0\n"
        make_fake_bin(self.bin, "chromium", f'echo "$(date +%s) $@" >> "{self.calls}"\n{tail}')

    def wlrctl(self, listing: str) -> None:
        """Les fenêtres que le compositeur annonce (une par ligne : « identifiant: titre »)."""
        self.windows.write_text(listing)
        make_fake_bin(self.bin, "wlrctl", f'[ "$2" = list ] && cat "{self.windows}"\nexit 0\n')

    def outputs(self, text: str) -> None:
        """Ce que dit wlr-randr : rien tant que le compositeur n'a pas d'écran."""
        make_fake_bin(self.bin, "wlr-randr", f'echo "{text}"\n' if text else "exit 0\n")

    def env(self, **extra: str) -> dict[str, str]:
        env = {
            "PATH": f"{self.bin}:/usr/bin:/bin",
            "HOME": str(self.tmp),
            "MYDOCK_CONF_DIR": str(self.conf),
            "MYDOCK_PROFILE": str(self.profile),
            "MYDOCK_PORT": "8081",
            "MYDOCK_LOCK": str(self.tmp / "kiosk.lock"),
            "MYDOCK_OUTPUT_WAIT": "0",
            "MYDOCK_SHOW_TIMEOUT": "2",
            "MYDOCK_WATCH_STEP": "1",
            "MYDOCK_RETRY_PAUSE": "1",
        }
        env.update(extra)
        return env

    def launches(self) -> list[str]:
        return self.calls.read_text().splitlines() if self.calls.exists() else []


@pytest.fixture
def kiosk(tmp_path):
    return Kiosk(tmp_path)


def test_kiosk_browser_launcher_builds_the_right_command_line(kiosk):
    kiosk.chromium(stays=False)  # se ferme tout de suite : le lanceur doit le relancer
    kiosk.outputs('HDMI-A-1 \\"écran\\"')
    make_fake_bin(
        kiosk.bin,
        "wlr-randr",
        f'if [ "$#" -eq 0 ]; then echo "HDMI-A-1 \\"écran\\""; echo "  1024x600"; else echo "wlr-randr $@" >> "{kiosk.calls}"; fi\n',
    )
    (kiosk.conf / "display.conf").write_text('MYDOCK_OUTPUT_MODE="1024x600@60"\n')
    (kiosk.conf / "chromium.flags").write_text(
        "# commentaire ignoré\n--use-gl=angle\n--mon-option-test\n"
    )
    run_launcher(kiosk.env(MYDOCK_RETRY_PAUSE="2"), 5)
    lines = kiosk.launches()
    assert lines[0] == "wlr-randr --output HDMI-A-1 --mode 1024x600@60"
    browser = lines[1]
    for expected in (
        "--kiosk http://127.0.0.1:8081/",
        f"--user-data-dir={kiosk.profile}",
        "--ozone-platform=wayland",
        "--disable-pinch",
        "--overscroll-history-navigation=0",
        "--force-device-scale-factor=1",
        "--use-gl=angle",
        "--mon-option-test",
    ):
        assert expected in browser, f"{expected!r} absent de : {browser}"
    assert "commentaire" not in browser
    assert len(lines) >= 3, (
        "Chromium doit être relancé quand il se ferme"
    )  # 2 s entre deux lancements


def test_kiosk_launcher_relaunches_a_chromium_that_stays_grey(kiosk):
    """Le cas du Pi : Chromium vit, rien ne s'affiche (le dock ne voit aucun navigateur) : il est arrêté puis relancé."""
    kiosk.health(browsers=0)
    _, _, err = run_launcher(kiosk.env(), 9)
    assert len(kiosk.launches()) >= 2
    assert "écran gris" in err


@pytest.mark.parametrize(
    ("windows", "browsers", "restarted"),
    [
        ("chromium: MyDock", 0, True),  # la fenêtre est là mais la page n'a pas rejoint le dock
        ("", 1, True),  # la page a rejoint le dock mais aucune fenêtre n'est affichée
        ("chromium-browser: MyDock", 1, False),  # les deux : tout va bien
        ("foot: terminal\nchromium: MyDock", 1, False),
        ("foot: terminal", 1, True),  # une fenêtre, mais pas celle de Chromium
    ],
)
def test_kiosk_launcher_needs_both_the_window_and_the_dock_to_see_the_page(
    kiosk, windows, browsers, restarted
):
    kiosk.wlrctl(windows)
    kiosk.health(browsers=browsers)
    run_launcher(kiosk.env(), 7)
    assert (len(kiosk.launches()) >= 2) is restarted


def test_kiosk_launcher_leaves_a_chromium_whose_page_is_shown_alone(kiosk):
    kiosk.health(browsers=1)
    _, _, err = run_launcher(kiosk.env(), 6)
    assert len(kiosk.launches()) == 1
    assert "écran gris" not in err


def test_kiosk_launcher_does_not_act_when_the_dock_cannot_say(kiosk):
    """Un dock qui ne répond pas (ou ne dit rien du navigateur) n'est pas une raison de tuer Chromium."""
    kiosk.health(browsers=None)
    run_launcher(kiosk.env(), 6)
    assert len(kiosk.launches()) == 1
    # Le dock répond au démarrage du lanceur, puis plus du tout : le navigateur reste en place.
    make_fake_bin(
        kiosk.bin,
        "curl",
        f'n=$(cat "{kiosk.tmp}/curl-count" 2>/dev/null || echo 0)\necho $((n + 1)) > "{kiosk.tmp}/curl-count"\n[ "$n" -eq 0 ] && exit 0\nexit 22\n',
    )
    kiosk.calls.unlink()
    run_launcher(kiosk.env(MYDOCK_LOCK=str(kiosk.tmp / "autre.lock")), 6)
    assert len(kiosk.launches()) == 1


def test_a_second_launcher_does_not_open_a_second_window(kiosk):
    """Lancer le lanceur à la main pendant que celui du système tourne ne doit rien faire (deux Chromium sur un profil)."""
    first = subprocess.Popen(
        ["sh", str(PI / "kiosk-browser.sh")],
        env=kiosk.env(),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    try:
        deadline = time.monotonic() + 10
        while not kiosk.launches() and time.monotonic() < deadline:
            time.sleep(0.1)
        assert len(kiosk.launches()) == 1
        code, _, err = run_launcher(kiosk.env(), 5)
        assert code == 0
        assert "tourne déjà" in err
        assert len(kiosk.launches()) == 1
    finally:
        os.killpg(first.pid, signal.SIGKILL)
        first.wait()


def test_kiosk_launcher_clears_a_stale_profile_lock(kiosk):
    """Après une coupure de courant, le verrou du profil désigne un processus qui n'existe plus (ou un numéro réutilisé)."""
    kiosk.profile.mkdir()
    for name in ("SingletonLock", "SingletonSocket", "SingletonCookie"):
        os.symlink("raspberrypi-4242", kiosk.profile / name)
    (kiosk.profile / "Default").mkdir()
    run_launcher(kiosk.env(), 3)
    assert kiosk.launches()
    assert not any(
        os.path.lexists(kiosk.profile / n)
        for n in ("SingletonLock", "SingletonSocket", "SingletonCookie")
    )
    assert (kiosk.profile / "Default").is_dir()  # le reste du profil n'est pas touché


def test_kiosk_launcher_stops_a_chromium_left_on_its_profile(kiosk):
    kiosk.profile.mkdir()
    orphan = subprocess.Popen(
        ["sh", "-c", "sleep 60; :", f"--user-data-dir={kiosk.profile}"], start_new_session=True
    )
    try:
        run_launcher(kiosk.env(), 5)
        assert orphan.wait(timeout=5) is not None  # arrêté par le lanceur
    finally:
        with contextlib.suppress(ProcessLookupError):
            os.killpg(orphan.pid, signal.SIGKILL)
    assert len(kiosk.launches()) == 1


def test_kiosk_launcher_waits_for_an_output_but_not_forever(kiosk):
    kiosk.outputs("")  # le compositeur n'a pas (encore) d'écran
    begun = int(time.time())
    run_launcher(kiosk.env(MYDOCK_OUTPUT_WAIT="2"), 8)
    first = int(kiosk.launches()[0].split()[0])
    assert first - begun >= 2, "Chromium ne doit pas s'ouvrir avant que l'écran existe"
    assert kiosk.launches(), "...mais il s'ouvre quand même si aucun écran n'apparaît"


def test_kiosk_launcher_falls_back_to_software_rendering_after_three_grey_starts(kiosk):
    kiosk.chromium(stays=False)  # trois lancements de suite sans page
    kiosk.health(browsers=0)
    _, _, err = run_launcher(kiosk.env(MYDOCK_RETRY_PAUSE="0"), 8)
    lines = kiosk.launches()
    assert len(lines) >= 4
    assert not any("--disable-gpu" in line for line in lines[:3])
    assert "--disable-gpu" in lines[3]
    assert "sans accélération graphique" in err


def test_kiosk_browser_launcher_explains_a_missing_chromium(tmp_path):
    fake = tmp_path / "bin"
    fake.mkdir()
    make_fake_bin(fake, "curl", "exit 0\n")
    result = subprocess.run(
        ["/bin/sh", str(PI / "kiosk-browser.sh")],
        env={"PATH": str(fake), "HOME": str(tmp_path), "MYDOCK_CONF_DIR": str(tmp_path)},
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    assert result.returncode != 0
    assert "Chromium est introuvable" in result.stderr


def test_player_launcher_starts_the_helper_on_the_local_dock_and_restarts_it(tmp_path):
    fake = tmp_path / "bin"
    fake.mkdir()
    calls = tmp_path / "calls.log"
    make_fake_bin(fake, "curl", "exit 0\n")  # le serveur du dock « répond » tout de suite
    make_fake_bin(fake, "mydock-dock", f'echo "$@" >> "{calls}"\nexit 0\n')
    env = {
        "PATH": f"{fake}:/usr/bin:/bin",
        "HOME": str(tmp_path),
        "MYDOCK_DOCK_BIN": str(fake / "mydock-dock"),
        "MYDOCK_PORT": "8081",
    }
    try:
        subprocess.run(
            ["sh", str(PI / "player-session.sh")],
            env=env,
            timeout=5,
            capture_output=True,
            check=False,
        )
    except subprocess.TimeoutExpired:
        pass  # normal : le lanceur boucle pour relancer l'assistant
    lines = calls.read_text().splitlines()
    assert lines[0] == "player-session --url ws://127.0.0.1:8081/ws/player"
    assert len(lines) >= 2, "l'assistant doit être relancé quand il s'arrête"


def test_player_launcher_only_talks_to_the_local_dock():
    text = (PI / "player-session.sh").read_text()
    assert "127.0.0.1" in text
    assert (
        "dock.toml" not in text
    )  # jamais le jeton : il vit dans /etc/mydock, illisible du kiosque
    assert "token" not in text.lower()


def test_player_launcher_explains_a_missing_dock_program(tmp_path):
    fake = tmp_path / "bin"
    fake.mkdir()
    make_fake_bin(fake, "curl", "exit 0\n")
    result = subprocess.run(
        ["/bin/sh", str(PI / "player-session.sh")],
        env={"PATH": str(fake), "HOME": str(tmp_path), "MYDOCK_DOCK_BIN": str(tmp_path / "absent")},
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    assert result.returncode != 0
    assert "mydock-dock est introuvable" in result.stderr


def test_kiosk_user_can_use_the_sound_card():
    setup = (PI / "setup.sh").read_text()
    assert "usermod -aG video,render,input,audio mydock-kiosk" in setup


def test_retrogame_groups_are_only_added_when_they_exist():
    """gpio (le bouton) pour l'utilisateur de l'écran, bluetooth (BlueZ) pour le serveur : jamais un groupe inventé."""
    setup = (PI / "setup.sh").read_text()
    assert "getent group gpio" in setup
    assert "usermod -aG gpio mydock-kiosk" in setup
    assert "getent group bluetooth" in setup
    assert "usermod -aG bluetooth mydock" in setup
    assert "groupadd" not in setup


def test_the_pi_package_recommends_what_retrogame_needs():
    control = (ROOT / "packaging/deb-pi/control.in").read_text()
    recommends = re.search(r"^Recommends:(.*?)^Section:", control, re.S | re.M).group(1)
    for package in ("mame", "python3-libgpiod", "python3-dbus-fast", "bluez"):
        assert package in recommends


def test_put_file_only_writes_when_content_or_mode_changes(tmp_path):
    target = tmp_path / "x" / "f.conf"
    script = f"""
    set -e
    . "{ROOT}/deploy/lib.sh"
    printf 'a\\n' | put_file "{target}" 644
    printf 'a\\n' | put_file "{target}" 644
    printf 'b\\n' | put_file "{target}" 644
    printf 'b\\n' | put_file "{target}" 600
    """
    result = subprocess.run(
        ["bash", "-c", script], capture_output=True, text=True, env=base_env(), check=False
    )
    assert result.returncode == 0, result.stdout + result.stderr
    verbs = [line.split()[0] for line in result.stdout.splitlines()]
    assert verbs == ["+", "=", "+", "+"]  # écrit, inchangé, contenu changé, droits changés
    assert mode(target) == 0o600
    assert target.read_text() == "b\n"


# --- la veille de l'écran, avec de faux swayidle, wlopm, wlr-randr et curl --------------------------------


class ScreenSleep:
    """Le script de veille de l'écran lancé pour de bon, avec de faux programmes qui notent tout."""

    def __init__(self, tmp_path: Path, *, tools: tuple[str, ...] = ("swayidle", "wlopm", "curl")):
        self.bin = tmp_path / "bin"
        self.bin.mkdir()
        self.calls = tmp_path / "calls.log"
        # Ce que « répond » le dock ; absent : le dock est en panne.
        self.seconds = tmp_path / "seconds"
        self.pids = tmp_path / "pids"  # un par swayidle lancé
        self.script = PI / "screen-sleep.sh"
        if "curl" in tools:
            make_fake_bin(
                self.bin,
                "curl",
                f'[ -f "{self.seconds}" ] || exit 22\nprintf \'{{"sleep_after":%s}}\' "$(cat "{self.seconds}")"\n',
            )
        if "swayidle" in tools:
            make_fake_bin(
                self.bin,
                "swayidle",
                f'echo "swayidle $*" >> "{self.calls}"\necho $$ >> "{self.pids}"\nexec sleep 300\n',
            )
        if "wlopm" in tools:
            make_fake_bin(self.bin, "wlopm", f'echo "wlopm $*" >> "{self.calls}"\n')
        if "wlr-randr" in tools:
            make_fake_bin(
                self.bin,
                "wlr-randr",
                f'if [ "$#" -eq 0 ]; then echo "HDMI-A-1 \\"écran\\""; echo "  1024x600"; echo "DSI-1"; '
                f'else echo "wlr-randr $*" >> "{self.calls}"; fi\n',
            )
        # Seulement les commandes courantes dont le script a besoin : un vrai swayidle, wlopm ou wlr-randr installé
        # sur la machine (un Pi de développement, par exemple) ne doit pas fausser les essais « sans ».
        basics = tmp_path / "basics"
        basics.mkdir()
        for name in ("sh", "readlink", "awk", "sed", "sleep", "cat", "kill"):
            found = shutil.which(name)
            if found:
                (basics / name).symlink_to(found)
        self.env = {
            "PATH": f"{self.bin}:{basics}",
            "HOME": str(tmp_path),
            "MYDOCK_SCREEN_POLL": "0.2",
            "MYDOCK_PORT": "8081",
        }
        self.process: subprocess.Popen[str] | None = None

    def set_seconds(self, value: int | None) -> None:
        if value is None:
            self.seconds.unlink(missing_ok=True)
        else:
            self.seconds.write_text(str(value))

    def start(self) -> None:
        self.process = subprocess.Popen(
            ["sh", str(self.script)],
            env=self.env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )

    def lines(self) -> list[str]:
        return self.calls.read_text().splitlines() if self.calls.exists() else []

    def watcher_pids(self) -> list[int]:
        return [int(p) for p in self.pids.read_text().split()] if self.pids.exists() else []

    def wait_for(self, predicate, timeout: float = 8.0) -> None:
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            if predicate():
                return
            time.sleep(0.05)
        raise AssertionError(f"condition jamais atteinte ; appels : {self.lines()}")

    def stop(self) -> tuple[int, str]:
        assert self.process is not None
        self.process.terminate()
        out, err = self.process.communicate(timeout=10)
        return self.process.returncode, out + err


@pytest.fixture
def screen(tmp_path):
    made: list[ScreenSleep] = []

    def build(**kwargs) -> ScreenSleep:
        fake = ScreenSleep(tmp_path, **kwargs)
        made.append(fake)
        return fake

    yield build
    for fake in made:  # jamais de swayidle orphelin après un test
        if fake.process is not None and fake.process.poll() is None:
            fake.process.kill()
            fake.process.communicate()
        for pid in fake.watcher_pids():
            with contextlib.suppress(ProcessLookupError):
                os.kill(pid, signal.SIGKILL)


def swayidle_lines(fake: ScreenSleep) -> list[str]:
    return [line for line in fake.lines() if line.startswith("swayidle ")]


def test_the_screen_sleeps_after_the_delay_chosen_in_the_settings(screen):
    fake = screen()
    fake.set_seconds(300)
    fake.start()
    fake.wait_for(lambda: swayidle_lines(fake))
    [line] = swayidle_lines(fake)
    script = fake.script.resolve()
    assert line == f"swayidle -w timeout 300 '{script}' --off resume '{script}' --on"
    code, _ = fake.stop()
    assert code == 0


def test_a_delay_of_zero_never_starts_the_watcher(screen):
    fake = screen()
    fake.set_seconds(0)
    fake.start()
    fake.wait_for(lambda: fake.process.poll() is None and fake.process is not None)
    time.sleep(0.8)  # plusieurs tours d'interrogation
    assert swayidle_lines(fake) == []
    fake.stop()


def test_changing_the_delay_restarts_the_watcher_and_never_leaves_the_screen_off(screen):
    fake = screen()
    fake.set_seconds(300)
    fake.start()
    fake.wait_for(lambda: len(swayidle_lines(fake)) == 1)
    fake.set_seconds(600)
    fake.wait_for(lambda: len(swayidle_lines(fake)) == 2)
    assert "timeout 600" in swayidle_lines(fake)[1]
    # entre les deux, l'ancien est arrêté et l'écran explicitement rallumé
    calls = fake.lines()
    first, second = [i for i, line in enumerate(calls) if line.startswith("swayidle ")]
    assert "wlopm --on *" in calls[first + 1 : second]
    fake.set_seconds(0)  # « jamais » : le dernier est arrêté, écran rallumé
    fake.wait_for(lambda: fake.lines()[-1] == "wlopm --on *")
    assert len(swayidle_lines(fake)) == 2
    fake.stop()


def test_a_dock_that_does_not_answer_changes_nothing_then_works_once_it_is_back(screen):
    fake = screen()
    fake.start()  # le dock ne répond pas encore (curl échoue)
    time.sleep(0.6)
    assert fake.lines() == []
    fake.set_seconds(60)
    fake.wait_for(lambda: swayidle_lines(fake))
    fake.set_seconds(None)  # le dock retombe : on garde ce qui est en place, sans relancer
    time.sleep(0.6)
    assert len(swayidle_lines(fake)) == 1
    fake.stop()


def test_a_watcher_that_dies_by_itself_is_started_again(screen):
    fake = screen()
    fake.set_seconds(300)
    fake.start()
    fake.wait_for(lambda: len(swayidle_lines(fake)) == 1)
    os.kill(fake.watcher_pids()[0], signal.SIGKILL)  # swayidle « plante »
    fake.wait_for(lambda: len(swayidle_lines(fake)) == 2)
    assert len(set(fake.watcher_pids())) == 2
    fake.stop()


def test_stopping_the_script_stops_the_watcher_and_turns_the_screen_back_on(screen):
    fake = screen()
    fake.set_seconds(300)
    fake.start()
    fake.wait_for(lambda: swayidle_lines(fake))
    code, _ = fake.stop()
    assert code == 0
    assert fake.lines()[-1] == "wlopm --on *"


def test_off_and_on_use_wlopm_when_it_exists(screen):
    fake = screen()
    for flag in ("--off", "--on"):
        assert sh(["sh", fake.script, flag], env=fake.env).returncode == 0
    assert fake.lines() == ["wlopm --off *", "wlopm --on *"]


def test_without_wlopm_every_output_is_switched_with_wlr_randr(screen):
    fake = screen(tools=("swayidle", "curl", "wlr-randr"))
    assert sh(["sh", fake.script, "--off"], env=fake.env).returncode == 0
    assert sh(["sh", fake.script, "--on"], env=fake.env).returncode == 0
    assert fake.lines() == [
        "wlr-randr --output HDMI-A-1 --off",
        "wlr-randr --output DSI-1 --off",
        "wlr-randr --output HDMI-A-1 --on",
        "wlr-randr --output DSI-1 --on",
    ]


def test_without_swayidle_the_script_says_so_and_stops_quietly(screen):
    fake = screen(tools=("curl", "wlopm"))
    result = sh(["sh", fake.script], env=fake.env, timeout=10)
    assert result.returncode == 0
    assert "swayidle est absent" in result.stderr
    assert "apt install swayidle" in result.stderr


def test_without_any_way_to_power_the_screen_the_script_says_so(screen):
    fake = screen(tools=("curl", "swayidle"))
    result = sh(["sh", fake.script], env=fake.env, timeout=10)
    assert result.returncode == 0
    assert "ni wlopm ni wlr-randr" in result.stderr
