"""Le paquet .deb de l'agent : contenu, droits, scripts du paquet, et code qui tourne une fois extrait.

Ce qui est vérifié ici : le .deb est construit par `packaging/build_deb.py`, ses métadonnées et ses
dépendances (déduites des pyproject.toml), chaque fichier et ses droits, les scripts de
maintenance (syntaxe, shellcheck, comportement avec de faux `runuser`/`getent`/`py3compile`),
l'unité systemd, et surtout que le code **extrait du paquet** démarre sans le dépôt.
Ce qui reste à valider sur le PC : `apt install` avec les dépendances d'Ubuntu 26.04 et le
démarrage du service dans la vraie session GNOME.
"""

from __future__ import annotations

import asyncio
import configparser
import gzip
import hashlib
import importlib.util
import os
import re
import shutil
import socket
import stat
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SOURCES = ROOT / "packaging" / "deb"

pytestmark = pytest.mark.skipif(shutil.which("dpkg-deb") is None, reason="dpkg-deb absent")


def load_builder():
    spec = importlib.util.spec_from_file_location("build_deb", ROOT / "packaging" / "build_deb.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules["build_deb"] = module
    spec.loader.exec_module(module)
    return module


builder = load_builder()


def dpkg(*args: str | Path) -> str:
    result = subprocess.run(
        ["dpkg-deb", *map(str, args)], capture_output=True, text=True, check=True
    )
    return result.stdout


@pytest.fixture(scope="session")
def deb(tmp_path_factory) -> Path:
    return builder.build(tmp_path_factory.mktemp("deb-output"))


@pytest.fixture(scope="session")
def extracted(deb, tmp_path_factory) -> Path:
    root = tmp_path_factory.mktemp("deb-root")
    dpkg("-x", deb, root)
    dpkg("-e", deb, root / "DEBIAN")
    return root


def mode(path: Path) -> int:
    return stat.S_IMODE(path.lstat().st_mode)


def field(deb: Path, name: str) -> str:
    return dpkg("-f", deb, name).strip()


# --- métadonnées ---------------------------------------------------------------------------------


def test_the_package_is_named_after_the_code_version(deb):
    from mydock_agent import __version__

    assert deb.name == f"mydock-agent_{__version__}_all.deb"
    assert field(deb, "Package") == "mydock-agent"
    assert field(deb, "Version") == __version__
    assert field(deb, "Architecture") == "all"  # Python pur : le même fichier sert partout


def test_dependencies_come_from_the_pyproject_files(deb):
    depends = field(deb, "Depends").split(", ")
    assert "python3 (>= 3.11)" in depends
    for project in ("common", "agent"):
        data = tomllib.loads((ROOT / project / "pyproject.toml").read_text())["project"]
        for requirement in data["dependencies"]:
            name, bounds = builder._lower_bounds(requirement)
            if name == "mydock-common":
                continue
            wanted = f"python3-{name} (>= {bounds[0]})" if bounds else f"python3-{name}"
            assert wanted in depends, requirement
    assert "systemd" in depends
    # Aucune borne haute : une distribution qui met à jour fastapi ne doit pas retirer l'agent.
    assert "<<" not in field(deb, "Depends")
    assert "<" not in field(deb, "Depends")


def test_optional_features_are_recommended_not_required(deb):
    recommends = field(deb, "Recommends")
    for package in ("libglib2.0-bin", "pulseaudio-utils", "ethtool", "python3-pil"):
        assert package in recommends
        assert package not in field(deb, "Depends")


def test_an_unknown_python_dependency_stops_the_build(monkeypatch):
    monkeypatch.setattr(builder, "DEBIAN_NAMES", {})
    with pytest.raises(builder.BuildError, match="DEBIAN_NAMES"):
        builder.runtime_dependencies()


def test_the_changelog_must_match_the_code_version(monkeypatch, tmp_path):
    monkeypatch.setattr(builder, "package_version", lambda: "9.9.9")
    with pytest.raises(builder.BuildError, match="changelog"):
        builder.build(tmp_path)


def test_the_installed_size_is_declared(deb):
    assert int(field(deb, "Installed-Size")) > 50


# --- contenu -------------------------------------------------------------------------------------


LISTING = re.compile(r"^\S+\s+\S+\s+\d+\s+\S+\s+\S+\s+\.(?P<path>\S*?)(?: -> \S+)?$")


def packaged_files(deb: Path) -> dict[str, str]:
    """chemin -> ligne de `dpkg-deb -c` (droits, propriétaire…)."""
    files = {}
    for line in dpkg("-c", deb).splitlines():
        match = LISTING.match(line)
        assert match, f"ligne illisible : {line!r}"
        files[match["path"].rstrip("/") or "/"] = line
    return files


EXPECTED = (
    "/usr/bin/mydock-agent",
    "/usr/lib/python3/dist-packages/mydock_agent/__main__.py",
    "/usr/lib/python3/dist-packages/mydock_agent/collectors/host.py",
    "/usr/lib/python3/dist-packages/mydock_agent/demo/__init__.py",
    "/usr/lib/python3/dist-packages/mydock_agent/setup.py",
    "/usr/lib/python3/dist-packages/mydock_common/protocol.py",
    "/usr/lib/systemd/user/mydock-agent.service",
    "/usr/lib/systemd/user/graphical-session.target.wants/mydock-agent.service",
    "/etc/ufw/applications.d/mydock-agent",
    "/usr/lib/mydock-agent/restart-user-agents",
    "/usr/lib/mydock-agent/enable-wol.sh",
    "/usr/lib/mydock-agent/lib.sh",
    "/usr/share/doc/mydock-agent/README.Debian",
    "/usr/share/doc/mydock-agent/copyright",
    "/usr/share/doc/mydock-agent/changelog.gz",
)


EXECUTABLES = (
    "/usr/bin/mydock-agent",
    "/usr/lib/mydock-agent/restart-user-agents",
    "/usr/lib/mydock-agent/enable-wol.sh",
)


@pytest.mark.parametrize("path", EXPECTED)
def test_every_expected_file_is_packaged(deb, path):
    assert path in packaged_files(deb)


def test_nothing_unwanted_is_packaged(deb):
    for path in packaged_files(deb):
        assert "__pycache__" not in path
        assert not path.endswith(".pyc")
        assert "/tests/" not in path
        assert not path.endswith("testing.py")  # aides de test
        assert "agent.toml" not in path  # aucune configuration, donc aucun jeton


def test_every_file_belongs_to_root_with_sane_permissions(extracted, deb):
    listing = packaged_files(deb)
    assert all(" root/root " in line for line in listing.values())
    for path in extracted.rglob("*"):
        if "DEBIAN" in path.relative_to(extracted).parts or path.is_symlink():
            continue
        relative = "/" + str(path.relative_to(extracted))
        if path.is_dir() or relative in EXECUTABLES:
            assert mode(path) == 0o755, relative
        else:
            assert mode(path) == 0o644, relative  # ni exécutable ni modifiable par un autre


def test_the_unit_is_enabled_for_every_user_through_a_symlink_in_the_package(extracted):
    link = extracted / "usr/lib/systemd/user/graphical-session.target.wants/mydock-agent.service"
    assert link.is_symlink()
    assert os.readlink(link) == "../mydock-agent.service"
    assert link.resolve() == extracted / "usr/lib/systemd/user/mydock-agent.service"


def test_the_changelog_is_compressed_in_debian_format(extracted):
    text = gzip.decompress((extracted / "usr/share/doc/mydock-agent/changelog.gz").read_bytes())
    assert text.decode().startswith("mydock-agent (")


def test_md5sums_describe_exactly_the_packaged_files(extracted):
    listed = {}
    for line in (extracted / "DEBIAN/md5sums").read_text().splitlines():
        digest, _, name = line.partition("  ")
        listed[name] = digest
    actual = {
        str(p.relative_to(extracted)): hashlib.md5(
            p.read_bytes(), usedforsecurity=False
        ).hexdigest()
        for p in extracted.rglob("*")
        if p.is_file() and not p.is_symlink() and "DEBIAN" not in p.relative_to(extracted).parts
    }
    assert listed == actual


def test_the_ufw_profile_is_a_conffile_and_matches_the_default_port(extracted):
    from mydock_agent.config import DEFAULT_PORT

    assert (extracted / "DEBIAN/conffiles").read_text().split() == [
        "/etc/ufw/applications.d/mydock-agent"
    ]
    profile = configparser.ConfigParser()
    profile.read(extracted / "etc/ufw/applications.d/mydock-agent")
    assert profile["MyDock agent"]["ports"] == f"{DEFAULT_PORT}/tcp"
    assert profile["MyDock agent"]["title"]


def test_the_build_is_reproducible(tmp_path):
    first = builder.build(tmp_path / "a")
    second = builder.build(tmp_path / "b")
    assert first.read_bytes() == second.read_bytes()


# --- l'unité systemd -----------------------------------------------------------------------------


def unit_directives(path: Path) -> dict[str, set[str]]:
    sections: dict[str, set[str]] = {}
    section = ""
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("["):
            section = line
        else:
            sections.setdefault(section, set()).add(line)
    return sections


def test_the_packaged_unit_runs_the_installed_program_once_configured(extracted):
    unit = unit_directives(extracted / "usr/lib/systemd/user/mydock-agent.service")
    assert "ExecStart=/usr/bin/mydock-agent run" in unit["[Service]"]
    assert (
        "ConditionPathExists=%E/mydock/agent.toml" in unit["[Unit]"]
    )  # configuré par l'utilisateur
    assert "WantedBy=graphical-session.target" in unit["[Install]"]
    assert "PartOf=graphical-session.target" in unit["[Unit]"]


def test_the_packaged_unit_and_the_script_unit_only_differ_by_their_paths(extracted):
    packaged = unit_directives(extracted / "usr/lib/systemd/user/mydock-agent.service")
    script = unit_directives(ROOT / "deploy" / "pc" / "mydock-agent.service")
    ignored = ("ExecStart=", "ConditionPathExists=")
    for section in packaged:
        assert {d for d in packaged[section] if not d.startswith(ignored)} == {
            d for d in script[section] if not d.startswith(ignored)
        }, section


@pytest.mark.skipif(shutil.which("systemd-analyze") is None, reason="systemd-analyze absent")
def test_the_unit_is_valid_systemd_syntax(extracted, tmp_path):
    markers = ("Unknown key", "Unknown section", "Failed to parse", "Invalid", "not a valid")

    def problems(unit: Path) -> list[str]:
        result = subprocess.run(
            ["systemd-analyze", "verify", "--man=no", str(unit)],
            capture_output=True,
            text=True,
            check=False,
        )
        return [
            line
            for line in (result.stdout + result.stderr).splitlines()
            if any(m in line for m in markers)
        ]

    assert problems(extracted / "usr/lib/systemd/user/mydock-agent.service") == []
    # Témoin : le même contrôle voit bien une unité mal écrite (le test n'est pas vide).
    bad = tmp_path / "bad.service"
    bad.write_text("[Unit]\nDescription=x\nBogusKey=1\n[Service]\nExecStart=/usr/bin/true\n")
    assert problems(bad)


# --- scripts du paquet ---------------------------------------------------------------------------

SCRIPTS = ("postinst", "prerm", "restart-user-agents")


@pytest.mark.parametrize("name", SCRIPTS)
def test_package_scripts_are_posix_sh_and_clean(name):
    path = SOURCES / name
    assert path.read_text().startswith("#!/bin/sh\n")
    subprocess.run(["sh", "-n", str(path)], check=True)
    if shutil.which("shellcheck"):
        result = subprocess.run(
            ["shellcheck", "-s", "sh", str(path)], capture_output=True, text=True, check=False
        )
        assert result.returncode == 0, result.stdout


def stub(directory: Path, name: str, body: str) -> None:
    path = directory / name
    path.write_text(f"#!/bin/sh\n{body}\n")
    path.chmod(0o755)


def run_script(script: Path, *args: str, path: Path, log: Path) -> subprocess.CompletedProcess[str]:
    env = {"PATH": f"{path}:/usr/bin:/bin", "LOG": str(log)}
    return subprocess.run(
        ["sh", str(script), *args], capture_output=True, text=True, env=env, check=False
    )


@pytest.fixture
def fake_system(tmp_path):
    """Faux py3compile, py3clean et faux assistant : on observe ce que les scripts déclenchent."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    log = tmp_path / "log"
    stub(bin_dir, "py3compile", 'echo "py3compile $*" >> "$LOG"')
    stub(bin_dir, "py3clean", 'echo "py3clean $*" >> "$LOG"')
    helper_dir = tmp_path / "lib"
    helper_dir.mkdir()
    stub(helper_dir, "restart-user-agents", 'echo "restart-user-agents $*" >> "$LOG"')

    def script(name: str) -> Path:
        # Les scripts appellent le programme installé par son chemin absolu : on le redirige.
        text = (SOURCES / name).read_text().replace("/usr/lib/mydock-agent", str(helper_dir))
        copy = tmp_path / name
        copy.write_text(text)
        return copy

    return {"bin": bin_dir, "log": log, "script": script}


def calls(system) -> list[str]:
    return system["log"].read_text().splitlines() if system["log"].exists() else []


def test_a_first_install_compiles_and_explains_the_next_step(fake_system):
    result = run_script(
        fake_system["script"]("postinst"),
        "configure",
        path=fake_system["bin"],
        log=fake_system["log"],
    )
    assert result.returncode == 0
    assert calls(fake_system) == ["py3compile -p mydock-agent"]
    assert "mydock-agent setup --pi ADRESSE_IP_DU_PI" in result.stdout


def test_an_upgrade_restarts_the_open_sessions_instead(fake_system):
    result = run_script(
        fake_system["script"]("postinst"),
        "configure",
        "0.0.9",
        path=fake_system["bin"],
        log=fake_system["log"],
    )
    assert result.returncode == 0
    assert calls(fake_system) == ["py3compile -p mydock-agent", "restart-user-agents try-restart"]
    assert "mydock-agent setup" not in result.stdout  # déjà configuré


def test_postinst_never_fails_the_installation(fake_system):
    stub(fake_system["bin"], "py3compile", "exit 1")
    result = run_script(
        fake_system["script"]("postinst"),
        "configure",
        "0.0.9",
        path=fake_system["bin"],
        log=fake_system["log"],
    )
    assert result.returncode == 0


def test_removal_stops_the_agent_but_an_upgrade_does_not(fake_system):
    script = fake_system["script"]("prerm")
    run_script(script, "remove", path=fake_system["bin"], log=fake_system["log"])
    assert calls(fake_system) == ["restart-user-agents stop", "py3clean -p mydock-agent"]
    fake_system["log"].unlink()
    run_script(script, "upgrade", "0.2.0", path=fake_system["bin"], log=fake_system["log"])
    assert calls(fake_system) == ["py3clean -p mydock-agent"]


def test_the_restart_helper_only_acts_on_real_open_sessions(tmp_path):
    run_user = tmp_path / "run" / "user"
    for name in ("1000", "1001", "abc"):
        (run_user / name).mkdir(parents=True)
    with socket.socket(socket.AF_UNIX) as bus:  # seule la session 1000 a un bus utilisateur
        bus.bind(str(run_user / "1000" / "bus"))
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        log = tmp_path / "log"
        stub(bin_dir, "getent", 'echo "mars:x:$2:$2::/home/mars:/bin/bash"')
        stub(bin_dir, "runuser", 'echo "runuser $*" >> "$LOG"')
        result = run_script(
            SOURCES / "restart-user-agents", "try-restart", str(run_user), path=bin_dir, log=log
        )
    assert result.returncode == 0
    assert log.read_text().splitlines() == [
        f"runuser -u mars -- env XDG_RUNTIME_DIR={run_user}/1000 "
        f"DBUS_SESSION_BUS_ADDRESS=unix:path={run_user}/1000/bus "
        "systemctl --user try-restart mydock-agent.service"
    ]


def test_the_restart_helper_is_harmless_without_any_session(tmp_path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    result = run_script(
        SOURCES / "restart-user-agents",
        "stop",
        str(tmp_path / "absent"),
        path=bin_dir,
        log=tmp_path / "log",
    )
    assert result.returncode == 0


# --- le code extrait du paquet démarre sans le dépôt ------------------------------------------------


def packaged_python(
    extracted: Path, *args: str, timeout: int = 60
) -> subprocess.CompletedProcess[str]:
    """Interpréteur de test, mais avec le code du paquet en tête de sys.path (pas celui du dépôt)."""
    env = {
        **{k: v for k, v in os.environ.items() if k != "PYTHONPATH"},
        "PYTHONPATH": str(extracted / "usr/lib/python3/dist-packages"),
    }
    return subprocess.run(
        [sys.executable, *args],
        capture_output=True,
        text=True,
        env=env,
        cwd=extracted,
        timeout=timeout,
        check=False,
    )


def test_the_packaged_modules_are_the_ones_that_get_imported(extracted):
    result = packaged_python(
        extracted,
        "-c",
        "import mydock_agent, mydock_common; print(mydock_agent.__file__); print(mydock_common.__file__)",
    )
    assert result.returncode == 0, result.stderr
    for line in result.stdout.splitlines():
        assert line.startswith(str(extracted)), line  # pas le dépôt : le test serait sans valeur


def test_every_packaged_module_imports_on_its_own(extracted):
    modules = sorted(
        ".".join(
            p.relative_to(extracted / "usr/lib/python3/dist-packages").with_suffix("").parts
        ).removesuffix(".__init__")
        for p in (extracted / "usr/lib/python3/dist-packages").rglob("*.py")
        if p.name != "__main__.py"
    )
    code = "import importlib, sys\n" + "".join(f"importlib.import_module({m!r})\n" for m in modules)
    result = packaged_python(extracted, "-c", code)
    assert result.returncode == 0, result.stderr  # un module oublié dans le paquet se verrait ici


def test_the_installed_command_reports_its_version(extracted):
    from mydock_agent import __version__

    result = packaged_python(extracted, str(extracted / "usr/bin/mydock-agent"), "--version")
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == f"mydock-agent {__version__}"


def test_the_packaged_agent_serves_the_protocol_in_demo_mode(extracted):
    """L'agent extrait du paquet démarre et répond au `hello` sur un vrai port."""
    from websockets.asyncio.client import connect

    from mydock_agent.demo import DEMO_TOKEN
    from mydock_common import protocol as proto

    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    env = {
        **{k: v for k, v in os.environ.items() if k != "PYTHONPATH"},
        "PYTHONPATH": str(extracted / "usr/lib/python3/dist-packages"),
    }
    process = subprocess.Popen(
        [
            sys.executable,
            str(extracted / "usr/bin/mydock-agent"),
            "run",
            "--demo",
            "--port",
            str(port),
        ],
        env=env,
        cwd=extracted,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
    )

    async def hello() -> dict:
        for _ in range(100):
            try:
                async with connect(
                    f"ws://127.0.0.1:{port}/ws",
                    additional_headers={"Authorization": f"Bearer {DEMO_TOKEN}"},
                    open_timeout=1,
                    proxy=None,
                ) as ws:
                    return proto.loads(await asyncio.wait_for(ws.recv(), 3))
            except OSError:
                await asyncio.sleep(0.1)
        raise AssertionError("l'agent du paquet ne répond pas")

    try:
        message = asyncio.run(hello())
    finally:
        process.terminate()
        try:
            _, error = process.communicate(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            _, error = process.communicate()
    assert message["t"] == "hello"
    assert message["proto"] == proto.PROTOCOL_VERSION
    assert "Traceback" not in error


def test_the_packaged_doctor_and_setup_help_run(extracted, tmp_path):
    for args in (["setup", "--help"], ["doctor", "--help"]):
        result = packaged_python(extracted, str(extracted / "usr/bin/mydock-agent"), *args)
        assert result.returncode == 0, result.stderr


def test_the_wake_on_lan_script_finds_its_library_inside_the_package(extracted):
    script = extracted / "usr/lib/mydock-agent/enable-wol.sh"
    assert '. "$HERE/lib.sh"' in script.read_text()
    assert "../lib.sh" not in script.read_text()
    result = subprocess.run(
        ["bash", str(script), "--help"], capture_output=True, text=True, check=False
    )
    assert result.returncode == 0, result.stderr
    assert "Wake-on-LAN" in result.stdout
    if shutil.which("shellcheck"):
        lint = subprocess.run(
            ["shellcheck", "-x", "-P", "SCRIPTDIR", str(script)],
            capture_output=True,
            text=True,
            check=False,
        )
        assert lint.returncode == 0, lint.stdout


def test_no_secret_is_packaged(extracted):
    pattern = re.compile(r"[A-Za-z0-9_\-]{43}")
    for path in extracted.rglob("*"):
        if not path.is_file() or path.is_symlink() or path.suffix == ".gz":
            continue
        for line in path.read_text(errors="ignore").splitlines():
            for match in pattern.findall(line):
                # Les seuls mots de 43 caractères sont des identifiants Python, jamais un jeton
                # (un jeton mêle chiffres et lettres sans séparateur lisible).
                assert not (any(c.isdigit() for c in match) and any(c.isupper() for c in match)), (
                    f"{path}: {match}"
                )
