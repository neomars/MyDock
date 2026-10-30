"""Tests des scripts de déploiement, sans Pi ni systemd : installation réelle dans un dossier temporaire.

Ce qui est vérifié ici : les fichiers écrits, leurs droits, l'idempotence, la simulation, les refus
(jeton absent, nom introuvable, root sur le PC) et le lanceur du kiosque (avec un faux Chromium).
Ce qui ne peut pas l'être ici et reste à valider sur les vraies machines : apt, useradd, systemd,
ufw, NetworkManager, labwc, Chromium et l'écran lui-même.
"""

from __future__ import annotations

import os
import re
import shutil
import stat
import subprocess
import sys
import textwrap
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
    shell = "sh" if script.name == "kiosk-browser.sh" else "bash"
    assert subprocess.run([shell, "-n", str(script)], check=False).returncode == 0
    if script.name != "lib.sh":
        assert os.access(script, os.X_OK), f"{script} n'est pas exécutable"


@pytest.mark.skipif(shutil.which("shellcheck") is None, reason="shellcheck absent")
def test_shellcheck_is_clean():
    result = subprocess.run(
        ["shellcheck", "-x", *map(str, ALL_SCRIPTS)], capture_output=True, text=True, check=False
    )
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize(
    "script", [*sorted(PC.glob("install-*.sh")), *sorted(PI.glob("install-*.sh"))]
)
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
    assert version.stdout.startswith("mydock-agent 0.4.0")


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
def test_pc_prints_the_token_once_then_keeps_it(pc_install):
    first, second = pc_install["first"], pc_install["second"]
    venv = pc_install["home"] / ".local" / "share" / "mydock" / "venv"
    token = sh([venv / "bin" / "mydock-agent", "show-token"], env=pc_install["env"]).stdout.strip()
    assert len(token) >= 43
    assert "Jeton à saisir sur le Pi" in first.stdout
    assert token in first.stdout
    assert second.returncode == 0, second.stdout + second.stderr
    assert token not in second.stdout  # le jeton n'est plus affiché
    assert "Jeton existant conservé" in second.stdout


@pytest.mark.slow
def test_pc_second_run_changes_nothing(pc_install):
    assert pc_install["config"].read_bytes() == pc_install["snapshot"]
    assert "= inchangé" in pc_install["second"].stdout
    assert "+ écrit" not in pc_install["second"].stdout


@pytest.mark.slow
def test_pc_rotating_the_token_gives_a_new_one(pc_install):
    venv = pc_install["home"] / ".local" / "share" / "mydock" / "venv"
    before = sh([venv / "bin" / "mydock-agent", "show-token"], env=pc_install["env"]).stdout.strip()
    result = sh(
        [PC / "install-agent.sh", *pc_install["args"], "--rotate-token"], env=pc_install["env"]
    )
    after = sh([venv / "bin" / "mydock-agent", "show-token"], env=pc_install["env"]).stdout.strip()
    assert result.returncode == 0, result.stdout + result.stderr
    assert after != before
    assert after in result.stdout


@pytest.mark.slow
def test_pc_final_message_gives_an_ip_address_not_a_local_name(pc_install):
    out = pc_install["first"].stdout
    assert "sudo ./deploy/pi/install-dock.sh --pc " in out
    assert "Préfère l'adresse IP" in out
    assert "bail DHCP" in out
    assert "-w '%{http_code}\\n'" in out  # le test curl à lancer depuis le Pi


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


# --- Pi : dock ----------------------------------------------------------------------------------


@pytest.fixture(scope="module")
def pi_install(tmp_path_factory, wheelhouse):
    prefix = tmp_path_factory.mktemp("pi-root")
    token_file = prefix.parent / "jeton.txt"
    token_file.write_text(TOKEN + "\n")
    env = base_env(HOME=str(prefix.parent), **offline(wheelhouse))
    args = [
        "--prefix", str(prefix), "--pc", "Ubuntu.local", "--token-file", str(token_file),
        "--kiosk", "systemd", "--force-mode", "1024x600@60",
    ]  # fmt: skip
    first = sh([PI / "install-dock.sh", *args], env=env)
    config = prefix / "etc" / "mydock" / "dock.toml"
    snapshot = config.read_bytes() if config.exists() else b""
    second = sh([PI / "install-dock.sh", *args], env=env)
    # Mise à jour sans rien redonner : la configuration existante suffit.
    third = sh([PI / "install-dock.sh", "--prefix", str(prefix), "--kiosk", "systemd"], env=env)
    return {
        "prefix": prefix,
        "env": env,
        "first": first,
        "second": second,
        "third": third,
        "config": config,
        "snapshot": snapshot,
    }


@pytest.mark.slow
def test_pi_install_writes_everything_where_the_services_expect_it(pi_install):
    first, prefix = pi_install["first"], pi_install["prefix"]
    assert first.returncode == 0, first.stdout + first.stderr
    assert (prefix / "opt/mydock/src/dock/web/dist/index.html").is_file()
    assert (prefix / "opt/mydock/src/deploy/pi/kiosk-browser.sh").stat().st_mode & stat.S_IXUSR
    assert (prefix / "opt/mydock/venv/bin/mydock-dock").exists()
    version = sh([prefix / "opt/mydock/venv/bin/mydock-dock", "--version"], env=pi_install["env"])
    assert version.stdout.startswith("mydock-dock 0.4.0")
    # les tests et les sources inutiles n'encombrent pas le Pi
    assert not list((prefix / "opt/mydock/src").rglob("tests"))


@pytest.mark.slow
def test_pi_config_is_private_and_normalized(pi_install):
    config = pi_install["config"]
    assert mode(config) == 0o600
    text = config.read_text()
    assert 'host = "ubuntu.local"' in text  # saisi « Ubuntu.local » : normalisé
    assert f'token = "{TOKEN}"' in text
    assert 'web_dir = "/opt/mydock/src/dock/web/dist"' in text
    assert 'listen_host = "127.0.0.1"' in text


@pytest.mark.slow
def test_pi_token_never_leaks_outside_the_private_config(pi_install):
    prefix, config = pi_install["prefix"], pi_install["config"]
    for result in ("first", "second", "third"):
        out = pi_install[result]
        assert TOKEN not in out.stdout + out.stderr, f"jeton affiché par l'étape {result}"
    leaks = [
        p
        for p in prefix.rglob("*")
        if p.is_file() and p != config and not p.is_symlink() and "venv" not in p.parts
        and TOKEN.encode() in p.read_bytes()
    ]  # fmt: skip
    assert leaks == []


@pytest.mark.slow
def test_pi_service_units_are_rendered_and_hardened(pi_install):
    prefix = pi_install["prefix"]
    dock = (prefix / "etc/systemd/system/mydock-dock.service").read_text()
    assert "ExecStart=/opt/mydock/venv/bin/mydock-dock run --config /etc/mydock/dock.toml" in dock
    assert "ReadWritePaths=/var/lib/mydock /etc/mydock" in dock
    for hardening in (
        "NoNewPrivileges=yes",
        "ProtectSystem=strict",
        "ProtectHome=yes",
        "PrivateTmp=yes",
    ):
        assert hardening in dock
    assert "User=mydock\n" in dock
    kiosk = (prefix / "etc/systemd/system/mydock-kiosk.service").read_text()
    assert "User=mydock-kiosk" in kiosk
    assert "ExecStart=/usr/bin/labwc --config-dir /etc/mydock-kiosk/labwc" in kiosk
    for text in (dock, kiosk):
        assert not re.findall(r"@[A-Z_]+@", text)  # tous les marqueurs @NOM@ sont remplacés


@pytest.mark.slow
def test_pi_kiosk_configuration_is_readable_by_the_browser_user(pi_install):
    prefix = pi_install["prefix"]
    autostart = (prefix / "etc/mydock-kiosk/labwc/autostart").read_text()
    assert "/opt/mydock/src/deploy/pi/kiosk-browser.sh &" in autostart
    assert (
        'MYDOCK_OUTPUT_MODE="1024x600@60"' in (prefix / "etc/mydock-kiosk/display.conf").read_text()
    )
    assert (prefix / "etc/mydock-kiosk/chromium.flags").is_file()
    for path in (prefix / "etc/mydock-kiosk").rglob("*"):
        if path.is_file():
            assert (
                mode(path) == 0o644
            )  # lisible par mydock-kiosk, jamais par le jeton (dans /etc/mydock)
    assert mode(prefix / "etc/mydock") == 0o750


@pytest.mark.slow
def test_pi_reinstall_changes_no_config_and_keeps_the_token(pi_install):
    assert pi_install["second"].returncode == 0, (
        pi_install["second"].stdout + pi_install["second"].stderr
    )
    assert pi_install["config"].read_bytes() == pi_install["snapshot"]
    assert "= inchangé" in pi_install["second"].stdout
    assert "+ écrit" not in pi_install["second"].stdout


@pytest.mark.slow
def test_pi_update_without_arguments_reuses_the_existing_configuration(pi_install):
    third = pi_install["third"]
    assert third.returncode == 0, third.stdout + third.stderr
    assert pi_install["config"].read_bytes() == pi_install["snapshot"]
    assert "celui de la configuration existante" in third.stdout


@pytest.mark.skipif(shutil.which("systemd-analyze") is None, reason="systemd-analyze absent")
@pytest.mark.slow
@pytest.mark.parametrize("unit", ["mydock-dock.service", "mydock-kiosk.service"])
def test_pi_units_are_valid_systemd_syntax(pi_install, unit):
    prefix = pi_install["prefix"]
    result = subprocess.run(
        [
            "systemd-analyze",
            "verify",
            "--man=no",
            f"--root={prefix}",
            f"/etc/systemd/system/{unit}",
        ],
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


def test_pi_first_install_without_a_token_is_refused_with_instructions(tmp_path):
    env = base_env(HOME=str(tmp_path))
    result = sh(
        [
            PI / "install-dock.sh",
            "--prefix",
            str(tmp_path / "p"),
            "--pc",
            "ubuntu.local",
            "--kiosk",
            "none",
        ],
        env=env,
    )
    assert result.returncode != 0
    assert "jeton" in result.stderr.lower()
    assert "show-token" in result.stderr


def test_pi_first_install_without_a_pc_is_refused(tmp_path):
    result = sh(
        [PI / "install-dock.sh", "--prefix", str(tmp_path / "p"), "--kiosk", "none"],
        env=base_env(HOME=str(tmp_path)),
    )
    assert result.returncode != 0
    assert "--pc est nécessaire" in result.stderr


@pytest.mark.parametrize(
    ("extra", "message"),
    [
        (["--kiosk", "bizarre"], "valeur inconnue"),
        (["--force-mode", "grand"], "format attendu"),
        (["--n-importe-quoi"], "Option inconnue"),
    ],
)
def test_pi_bad_options_are_explained(tmp_path, extra, message):
    result = sh(
        [PI / "install-dock.sh", "--prefix", str(tmp_path / "p"), "--pc", "x.local", *extra],
        env=base_env(HOME=str(tmp_path)),
    )
    assert result.returncode != 0
    assert message in result.stderr


def test_pi_dry_run_touches_nothing(tmp_path):
    prefix = tmp_path / "p"
    env = base_env(HOME=str(tmp_path), MYDOCK_TOKEN=TOKEN)
    result = sh(
        [PI / "install-dock.sh", "--prefix", str(prefix), "--pc", "ubuntu.local", "--dry-run"],
        env=env,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "simulation" in result.stdout
    assert not prefix.exists() or [p for p in prefix.rglob("*") if p.is_file()] == []
    assert TOKEN not in result.stdout


def test_pi_refuses_to_run_without_root_outside_tests(tmp_path):
    if os.getuid() == 0:
        pytest.skip("le test suppose un utilisateur non root")
    result = sh([PI / "install-dock.sh", "--pc", "ubuntu.local"], env=base_env(HOME=str(tmp_path)))
    assert result.returncode != 0
    assert "avec sudo" in result.stderr


@pytest.mark.slow
def test_pi_desktop_mode_edits_the_session_autostart_and_backs_up_the_original(
    tmp_path, wheelhouse
):
    prefix = tmp_path / "p"
    home = prefix / "home" / "alice"
    (home / ".config" / "labwc").mkdir(parents=True)
    original = "wf-panel-pi &\npcmanfm --desktop &\n"
    (home / ".config" / "labwc" / "autostart").write_text(original)
    token_file = tmp_path / "jeton"
    token_file.write_text(TOKEN)
    cmd = [
        PI / "install-dock.sh", "--prefix", str(prefix), "--pc", "192.0.2.20", "--token-file", str(token_file),
        "--kiosk", "desktop", "--kiosk-user", "alice",
    ]  # fmt: skip
    env = base_env(HOME=str(tmp_path), **offline(wheelhouse))
    for _ in range(2):  # deux fois : la sauvegarde ne doit être faite qu'une fois
        result = sh(cmd, env=env)
        assert result.returncode == 0, result.stdout + result.stderr
    autostart = home / ".config" / "labwc" / "autostart"
    assert "kiosk-browser.sh" in autostart.read_text()
    assert "wf-panel-pi" not in autostart.read_text()  # le bureau ne se lance plus
    assert (autostart.parent / "autostart.avant-mydock").read_text() == original


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
