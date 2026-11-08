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
    shell = "sh" if script.name in ("kiosk-browser.sh", "screen-sleep.sh") else "bash"
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
    assert version.stdout.startswith("mydock-agent 1.3.0")


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


def test_pi_kiosk_configuration_is_readable_by_the_browser_user(pi_setup):
    prefix = pi_setup["prefix"]
    autostart = (prefix / "etc/mydock-kiosk/labwc/autostart").read_text()
    assert "/usr/lib/mydock/kiosk-browser.sh &" in autostart
    assert "/usr/lib/mydock/screen-sleep.sh &" in autostart  # la veille de l'écran
    assert not re.findall(r"@[A-Z_]+@", autostart)
    assert (
        'MYDOCK_OUTPUT_MODE="1024x600@60"' in (prefix / "etc/mydock-kiosk/display.conf").read_text()
    )
    assert (prefix / "etc/mydock-kiosk/chromium.flags").is_file()
    for path in (prefix / "etc/mydock-kiosk").rglob("*"):
        if path.is_file():
            # lisible par mydock-kiosk, jamais ce qui tient le jeton (dans /etc/mydock)
            assert mode(path) == 0o644


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


def test_kiosk_browser_launcher_builds_the_right_command_line(tmp_path):
    fake = tmp_path / "bin"
    fake.mkdir()
    calls = tmp_path / "calls.log"
    make_fake_bin(fake, "curl", "exit 0\n")  # le serveur du dock « répond » tout de suite
    make_fake_bin(fake, "chromium", f'echo "$@" >> "{calls}"\nexit 0\n')
    make_fake_bin(
        fake,
        "wlr-randr",
        f'if [ "$#" -eq 0 ]; then echo "HDMI-A-1 \\"écran\\""; echo "  1024x600"; else echo "wlr-randr $@" >> "{calls}"; fi\n',
    )
    conf = tmp_path / "conf"
    conf.mkdir()
    (conf / "display.conf").write_text('MYDOCK_OUTPUT_MODE="1024x600@60"\n')
    (conf / "chromium.flags").write_text(
        "# commentaire ignoré\n--use-gl=angle\n--mon-option-test\n"
    )
    env = {
        "PATH": f"{fake}:/usr/bin:/bin",
        "HOME": str(tmp_path),
        "MYDOCK_CONF_DIR": str(conf),
        "MYDOCK_PROFILE": str(tmp_path / "profil"),
        "MYDOCK_PORT": "8081",
    }
    try:
        subprocess.run(
            ["sh", str(PI / "kiosk-browser.sh")],
            env=env,
            timeout=4,
            capture_output=True,
            check=False,
        )
    except subprocess.TimeoutExpired:
        pass  # normal : le lanceur boucle pour relancer Chromium
    lines = calls.read_text().splitlines()
    assert lines[0] == "wlr-randr --output HDMI-A-1 --mode 1024x600@60"
    browser = lines[1]
    for expected in (
        "--kiosk http://127.0.0.1:8081/",
        f"--user-data-dir={tmp_path / 'profil'}",
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
    )  # 2 s de pause entre deux lancements


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
        self.env = {
            "PATH": f"{self.bin}:/usr/bin:/bin",
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
