"""Le paquet .deb du Pi (« mydock ») : le seul à installer à la main, il embarque celui du PC.

Ce qui est vérifié ici : métadonnées et dépendances (déduites des pyproject.toml), contenu et droits,
le paquet du PC embarqué (identique à celui construit seul, reconnu par le portail), les scripts de
maintenance (syntaxe, shellcheck, comportement avec de faux `adduser`/`install`/`rm`…), l'unité
systemd, et surtout que le dock **extrait du paquet** démarre sans le dépôt et sert, sur son portail,
le paquet du PC.
Ce qui reste à valider sur un vrai Pi : `apt install` avec les dépendances de Raspberry Pi OS
(Debian 13), le port 80 sous l'utilisateur « mydock », le kiosque dans labwc et l'écran lui-même.
"""

from __future__ import annotations

import hashlib
import importlib.util
import os
import re
import shutil
import socket
import stat
import subprocess
import sys
import time
import tomllib
import urllib.request
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SOURCES = ROOT / "packaging" / "deb-pi"


def load_builder():
    spec = importlib.util.spec_from_file_location(
        "build_deb_pi", ROOT / "packaging" / "build_deb.py"
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules["build_deb_pi"] = module
    spec.loader.exec_module(module)
    return module


builder = load_builder()
DIST = "usr/lib/python3/dist-packages"


def dpkg(*args: str | Path) -> str:
    result = subprocess.run(
        ["dpkg-deb", *map(str, args)], capture_output=True, text=True, check=True
    )
    return result.stdout


def field(deb: Path, name: str) -> str:
    return dpkg("-f", deb, name).strip()


def mode(path: Path) -> int:
    return stat.S_IMODE(path.lstat().st_mode)


pytestmark = [
    pytest.mark.skipif(shutil.which("dpkg-deb") is None, reason="dpkg-deb absent"),
    pytest.mark.skipif(
        not (ROOT / "dock/web/dist/index.html").is_file(), reason="interface non compilée"
    ),
]


@pytest.fixture(scope="session")
def pi_deb(tmp_path_factory) -> Path:
    return builder.build_pi(tmp_path_factory.mktemp("pi-deb-output"))


@pytest.fixture(scope="session")
def extracted(pi_deb, tmp_path_factory) -> Path:
    root = tmp_path_factory.mktemp("pi-deb-root")
    dpkg("-x", pi_deb, root)
    dpkg("-e", pi_deb, root / "DEBIAN")
    return root


@pytest.fixture(scope="session")
def agent_deb(tmp_path_factory) -> Path:
    return builder.build(tmp_path_factory.mktemp("agent-deb-output"))


# --- métadonnées ---------------------------------------------------------------------------------


def test_the_package_is_named_after_the_code_version(pi_deb):
    from mydock_dock import __version__

    assert pi_deb.name == f"mydock_{__version__}_all.deb"
    assert field(pi_deb, "Package") == "mydock"
    assert field(pi_deb, "Version") == __version__
    assert field(pi_deb, "Architecture") == "all"


def test_the_three_python_packages_and_the_two_debs_share_one_version():
    from mydock_agent import __version__ as agent
    from mydock_common import __version__ as common
    from mydock_dock import __version__ as dock

    assert agent == common == dock  # le PC et le dock s'entendent : un seul numéro à retenir
    assert builder.package_version() == agent
    assert builder.dock_version() == dock


def test_dependencies_come_from_the_pyproject_files(pi_deb):
    depends = field(pi_deb, "Depends").split(", ")
    assert "python3 (>= 3.11)" in depends
    for project in ("common", "dock/server"):
        data = tomllib.loads((ROOT / project / "pyproject.toml").read_text())["project"]
        for requirement in data["dependencies"]:
            name, bounds = builder._lower_bounds(requirement)
            if name == "mydock-common":
                continue
            wanted = f"python3-{name} (>= {bounds[0]})" if bounds else f"python3-{name}"
            assert wanted in depends, requirement
    # Le dock n'a pas besoin de D-Bus : c'est l'affaire de l'agent, sur le PC.
    assert not any(dependency.startswith("python3-dbus") for dependency in depends)
    assert "<<" not in field(pi_deb, "Depends")
    assert "<" not in field(pi_deb, "Depends")


@pytest.mark.parametrize(
    "dependency",
    [
        "systemd",
        "adduser",
        "curl",
        "labwc",
        "chromium | chromium-browser",
        "wlr-randr",
        "swayidle",
        "fonts-noto-core",
        "fonts-noto-color-emoji",
        "avahi-daemon",
        "libnss-mdns",
    ],
)
def test_what_the_kiosk_and_the_portal_need_is_installed_with_the_package(pi_deb, dependency):
    assert dependency in field(pi_deb, "Depends").split(", ")


def test_optional_features_are_recommended_not_required(pi_deb):
    recommends = field(pi_deb, "Recommends")
    assert "wlopm" in recommends  # éteindre l'écran sans wlr-randr
    assert "wlopm" not in field(pi_deb, "Depends")


def test_the_entertainment_programs_are_recommended(pi_deb):
    """Divertissement : sans eux le reste du dock marche, et l'écran dit ce qui manque."""
    recommends = field(pi_deb, "Recommends").split(", ")
    for wanted in (
        "vlc-bin",
        "vlc-plugin-base",
        "vlc-plugin-video-output",
        "vlc-plugin-samba",
        "smbclient",
        "xwayland",
    ):
        assert wanted in recommends, wanted
    assert "wlrctl | xdotool" in recommends  # changer de fenêtre : l'un ou l'autre
    depends = field(pi_deb, "Depends")
    assert "vlc" not in depends
    assert "ffmpeg" in depends.split(", ")  # le pont vers VLC pour les chaînes


def test_the_description_tells_where_to_go_next(pi_deb):
    description = field(pi_deb, "Description")
    assert "http://ADRESSE_DU_PI/" in description


def test_the_changelog_must_match_the_code_version(monkeypatch, tmp_path):
    monkeypatch.setattr(builder, "dock_version", lambda: "9.9.9")
    with pytest.raises(builder.BuildError, match="changelog"):
        builder.build_pi(tmp_path)


def test_a_missing_interface_stops_the_build(monkeypatch, tmp_path):
    monkeypatch.setattr(builder, "WEB_DIST", tmp_path / "absent")
    with pytest.raises(builder.BuildError, match="interface compilée"):
        builder.build_pi(tmp_path / "out")


def test_the_installed_size_is_declared(pi_deb):
    assert int(field(pi_deb, "Installed-Size")) > 500  # l'interface et le paquet du PC pèsent


# --- contenu -------------------------------------------------------------------------------------

LISTING = re.compile(r"^\S+\s+\S+\s+\d+\s+\S+\s+\S+\s+\.(?P<path>\S*?)(?: -> \S+)?$")


def packaged_files(deb: Path) -> dict[str, str]:
    files = {}
    for line in dpkg("-c", deb).splitlines():
        match = LISTING.match(line)
        assert match, f"ligne illisible : {line!r}"
        files[match["path"].rstrip("/") or "/"] = line
    return files


EXPECTED = (
    "/usr/bin/mydock-dock",
    f"/{DIST}/mydock_dock/__main__.py",
    f"/{DIST}/mydock_dock/portal.py",
    f"/{DIST}/mydock_dock/pairing.py",
    f"/{DIST}/mydock_dock/clients.py",
    f"/{DIST}/mydock_dock/markets/service.py",
    f"/{DIST}/mydock_common/protocol.py",
    "/usr/lib/systemd/system/mydock-dock.service",
    "/usr/lib/mydock/setup",
    "/usr/lib/mydock/lib.sh",
    "/usr/lib/mydock/kiosk-browser.sh",
    "/usr/lib/mydock/screen-sleep.sh",
    "/usr/lib/mydock/player-session.sh",
    "/usr/lib/mydock/mydock-kiosk.service",
    "/usr/lib/mydock/labwc/autostart",
    "/usr/share/mydock/web/index.html",
    "/usr/share/doc/mydock/README.Debian",
    "/usr/share/doc/mydock/copyright",
    "/usr/share/doc/mydock/changelog.gz",
)

EXECUTABLES = (
    "/usr/bin/mydock-dock",
    "/usr/lib/mydock/setup",
    "/usr/lib/mydock/kiosk-browser.sh",
    "/usr/lib/mydock/screen-sleep.sh",
    "/usr/lib/mydock/player-session.sh",
)


@pytest.mark.parametrize("path", EXPECTED)
def test_every_expected_file_is_packaged(pi_deb, path):
    assert path in packaged_files(pi_deb)


def test_nothing_unwanted_is_packaged(pi_deb):
    for path in packaged_files(pi_deb):
        assert "__pycache__" not in path
        assert not path.endswith(".pyc")
        assert "/tests/" not in path
        assert not path.endswith("testing.py")  # aides de test
        assert not path.endswith(".toml")  # aucune configuration, donc aucun jeton
        assert not path.endswith(".map") or "/web/" in path
        assert "/node_modules/" not in path


def test_every_file_belongs_to_root_with_sane_permissions(extracted, pi_deb):
    assert all(" root/root " in line for line in packaged_files(pi_deb).values())
    for path in extracted.rglob("*"):
        if "DEBIAN" in path.relative_to(extracted).parts or path.is_symlink():
            continue
        relative = "/" + str(path.relative_to(extracted))
        if path.is_dir() or relative in EXECUTABLES:
            assert mode(path) == 0o755, relative
        else:
            assert mode(path) == 0o644, relative


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


def test_the_compiled_interface_is_the_one_of_the_repository(extracted):
    dist = ROOT / "dock/web/dist"
    packaged = extracted / "usr/share/mydock/web"
    expected = sorted(str(p.relative_to(dist)) for p in dist.rglob("*") if p.is_file())
    found = sorted(str(p.relative_to(packaged)) for p in packaged.rglob("*") if p.is_file())
    assert found == expected
    for name in expected:
        assert (packaged / name).read_bytes() == (dist / name).read_bytes(), name


def test_the_kiosk_scripts_are_the_ones_of_the_repository(extracted):
    lib = extracted / "usr/lib/mydock"
    for name in (
        "kiosk-browser.sh",
        "screen-sleep.sh",
        "player-session.sh",
        "mydock-kiosk.service",
    ):
        assert (lib / name).read_bytes() == (ROOT / "deploy/pi" / name).read_bytes(), name
    assert (lib / "setup").read_bytes() == (ROOT / "deploy/pi/setup.sh").read_bytes()
    assert (lib / "lib.sh").read_bytes() == (ROOT / "deploy/lib.sh").read_bytes()
    assert "@LIB@/kiosk-browser.sh" in (lib / "labwc/autostart").read_text()
    assert "@LIB@/player-session.sh" in (lib / "labwc/autostart").read_text()


def test_the_build_is_reproducible(tmp_path):
    first = builder.build_pi(tmp_path / "a")
    second = builder.build_pi(tmp_path / "b")
    assert first.read_bytes() == second.read_bytes()


def test_older_builds_of_the_same_package_do_not_pile_up(tmp_path):
    """« apt install ./build/mydock_*_all.deb » ne doit jamais désigner deux versions."""
    out = tmp_path / "out"
    out.mkdir()
    (out / "mydock_0.0.1_all.deb").write_bytes(b"ancien")
    (out / "mydock-agent_0.0.1_all.deb").write_bytes(b"ancien")
    (out / "notes.txt").write_text("à garder")

    pi = builder.build_pi(out)
    assert [p.name for p in out.glob("mydock_*_all.deb")] == [
        pi.name
    ]  # l'ancien paquet du Pi a disparu
    assert (
        out / "mydock-agent_0.0.1_all.deb"
    ).exists()  # celui du PC n'est pas l'affaire de cette construction
    assert (out / "notes.txt").exists()  # et rien d'étranger n'est touché

    agent = builder.build(out)
    assert [p.name for p in out.glob("mydock-agent_*_all.deb")] == [agent.name]
    assert [p.name for p in out.glob("mydock_*_all.deb")] == [pi.name]


def test_no_secret_is_packaged(extracted):
    pattern = re.compile(r"[A-Za-z0-9_\-]{43}")
    for path in extracted.rglob("*"):
        if not path.is_file() or path.is_symlink() or path.suffix in (".gz", ".deb"):
            continue
        for line in path.read_text(errors="ignore").splitlines():
            line = re.sub(
                r"[\w.\-]+\.mov", "", line
            )  # noms publics des films d'Apple (aerial.py), pas des jetons
            line = re.sub(
                r"[0-9A-Fa-f]{64}", "", line
            )  # empreinte SHA-256 publique de la racine d'Apple (apple_ca.py) : 64 chiffres hexadécimaux, jamais un jeton (43 caractères)
            for match in pattern.findall(line):
                # Les seuls mots de 43 caractères sont des identifiants, jamais un jeton
                # (un jeton mêle chiffres et lettres sans séparateur lisible).
                assert not (any(c.isdigit() for c in match) and any(c.isupper() for c in match)), (
                    f"{path}: {match}"
                )


# --- le paquet du PC, embarqué -----------------------------------------------------------------


def test_the_pc_package_travels_inside_and_is_the_one_built_alone(extracted, agent_deb):
    inside = sorted((extracted / "usr/share/mydock/clients").iterdir())
    assert [p.name for p in inside] == [agent_deb.name]
    assert inside[0].read_bytes() == agent_deb.read_bytes()  # pas une copie « presque » pareille


def test_the_pc_package_inside_is_what_the_portal_recognises(extracted):
    from mydock_dock import __version__
    from mydock_dock.clients import ClientRegistry

    [client] = ClientRegistry(extracted / "usr/share/mydock/clients").scan()
    assert client.file == f"mydock-agent_{__version__}_all.deb"
    assert client.version == __version__
    assert client.size > 10_000
    assert field(extracted / "usr/share/mydock/clients" / client.file, "Package") == "mydock-agent"
    assert field(extracted / "usr/share/mydock/clients" / client.file, "Version") == __version__


# --- l'unité systemd -----------------------------------------------------------------------------


def test_the_service_runs_the_installed_program_with_the_config_of_the_package(extracted):
    unit = (extracted / "usr/lib/systemd/system/mydock-dock.service").read_text()
    assert "ExecStart=/usr/bin/mydock-dock run --config /etc/mydock/dock.toml" in unit
    assert "User=mydock\n" in unit
    assert "ReadWritePaths=/var/lib/mydock /etc/mydock" in unit
    assert "WantedBy=multi-user.target" in unit
    for hardening in (
        "NoNewPrivileges=yes",
        "ProtectSystem=strict",
        "ProtectHome=yes",
        "PrivateTmp=yes",
        "RestrictSUIDSGID=yes",
    ):
        assert hardening in unit
    assert not re.findall(r"@[A-Z_]+@", unit)


def test_the_service_keeps_one_privilege_only_to_listen_on_port_80(extracted):
    unit = (extracted / "usr/lib/systemd/system/mydock-dock.service").read_text()
    assert "CapabilityBoundingSet=CAP_NET_BIND_SERVICE\n" in unit
    assert "AmbientCapabilities=CAP_NET_BIND_SERVICE\n" in unit
    assert unit.count("Capabilit") == 2  # pas d'autre capacité, nulle part


# --- scripts du paquet ---------------------------------------------------------------------------

SCRIPTS = ("postinst", "prerm", "postrm")


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


def test_the_launcher_is_python_and_clean():
    launcher = SOURCES / "mydock-dock"
    assert launcher.read_text().startswith("#!/usr/bin/python3\n")
    subprocess.run([sys.executable, "-m", "py_compile", str(launcher)], check=True)


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
    """Faux adduser, install, getent, rm…, et faux script de mise en place : on observe ce que les
    scripts du paquet déclenchent, sans toucher au système ni à /etc."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    log = tmp_path / "log"
    for name in ("py3compile", "py3clean", "adduser", "install", "userdel", "rm"):
        stub(bin_dir, name, f'echo "{name} $*" >> "$LOG"')
    lib = tmp_path / "lib"
    lib.mkdir()
    stub(lib, "setup", 'echo "setup $*" >> "$LOG"')

    def users(*present: str) -> None:
        names = " ".join(present)
        stub(bin_dir, "getent", f'for n in {names}; do [ "$2" = "$n" ] && exit 0; done; exit 2')

    users()  # personne n'existe encore

    def script(name: str) -> Path:
        text = (SOURCES / name).read_text().replace("/usr/lib/mydock", str(lib))
        copy = tmp_path / name
        copy.write_text(text)
        return copy

    return {"bin": bin_dir, "log": log, "script": script, "users": users, "lib": lib}


def calls(system) -> list[str]:
    return system["log"].read_text().splitlines() if system["log"].exists() else []


def test_a_first_install_creates_the_service_user_the_folders_and_runs_the_setup(fake_system):
    result = run_script(
        fake_system["script"]("postinst"),
        "configure",
        path=fake_system["bin"],
        log=fake_system["log"],
    )
    assert result.returncode == 0, result.stderr
    log = calls(fake_system)
    assert log[0] == "py3compile -p mydock"
    assert log[1].startswith("adduser --system --group --home /var/lib/mydock --no-create-home")
    assert log[1].endswith("mydock")
    assert "--shell /usr/sbin/nologin" in log[1]  # un compte qui ne se connecte jamais
    assert log[2] == "install -d -m 750 -o mydock -g mydock /etc/mydock /var/lib/mydock"
    assert log[3] == "setup --kiosk auto"
    assert len(log) == 4


def test_an_upgrade_keeps_the_existing_user_and_reruns_the_setup(fake_system):
    fake_system["users"]("mydock")
    result = run_script(
        fake_system["script"]("postinst"),
        "configure",
        "1.0.0",
        path=fake_system["bin"],
        log=fake_system["log"],
    )
    assert result.returncode == 0
    log = calls(fake_system)
    assert not any(line.startswith("adduser") for line in log)
    assert log[-1] == "setup --kiosk auto"  # la mise en place suit l'installation, à chaque fois


def test_postinst_never_fails_the_installation_and_says_how_to_retry(fake_system):
    stub(fake_system["lib"], "setup", "exit 1")
    stub(fake_system["bin"], "adduser", "exit 1")
    stub(fake_system["bin"], "py3compile", "exit 1")
    stub(fake_system["bin"], "install", "exit 1")
    result = run_script(
        fake_system["script"]("postinst"),
        "configure",
        path=fake_system["bin"],
        log=fake_system["log"],
    )
    assert result.returncode == 0
    assert "n'a pas abouti" in result.stderr
    assert "Relance : sudo " in result.stderr  # le chemin de /usr/lib/mydock/setup, redirigé ici
    assert result.stderr.strip().endswith("/setup")


def test_postinst_ignores_the_other_actions(fake_system):
    result = run_script(
        fake_system["script"]("postinst"),
        "abort-upgrade",
        path=fake_system["bin"],
        log=fake_system["log"],
    )
    assert result.returncode == 0
    assert calls(fake_system) == []


def test_removal_stops_the_services_and_the_kiosk_but_an_upgrade_does_not(fake_system):
    script = fake_system["script"]("prerm")
    run_script(script, "remove", path=fake_system["bin"], log=fake_system["log"])
    assert calls(fake_system) == ["setup --remove", "py3clean -p mydock"]
    fake_system["log"].unlink()
    run_script(script, "upgrade", "1.2.0", path=fake_system["bin"], log=fake_system["log"])
    assert calls(fake_system) == ["py3clean -p mydock"]  # le kiosque survit à une mise à jour


def test_remove_keeps_the_configuration_and_purge_erases_it(fake_system):
    fake_system["users"]("mydock", "mydock-kiosk")
    script = fake_system["script"]("postrm")
    run_script(script, "remove", path=fake_system["bin"], log=fake_system["log"])
    assert calls(fake_system) == []  # « apt remove » garde le jeton
    run_script(script, "purge", path=fake_system["bin"], log=fake_system["log"])
    log = calls(fake_system)
    assert log[0] == "rm -rf /etc/mydock /etc/mydock-kiosk /var/lib/mydock /var/lib/mydock-kiosk"
    assert "userdel mydock" in log
    assert "userdel mydock-kiosk" in log


def test_purge_does_not_fail_when_the_users_are_already_gone(fake_system):
    result = run_script(
        fake_system["script"]("postrm"), "purge", path=fake_system["bin"], log=fake_system["log"]
    )
    assert result.returncode == 0
    assert not any(line.startswith("userdel") for line in calls(fake_system))


# --- le dock extrait du paquet démarre sans le dépôt, et sert le paquet du PC -------------------------


def packaged_env(extracted: Path) -> dict[str, str]:
    return {
        **{k: v for k, v in os.environ.items() if k not in ("PYTHONPATH", "MYDOCK_CONFIG")},
        "PYTHONPATH": str(extracted / DIST),
    }


def packaged_python(extracted: Path, *args: str | Path, timeout: int = 60):
    return subprocess.run(
        [sys.executable, *map(str, args)],
        capture_output=True,
        text=True,
        env=packaged_env(extracted),
        cwd=extracted,
        timeout=timeout,
        check=False,
    )


def free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def test_the_packaged_modules_are_the_ones_that_get_imported(extracted):
    result = packaged_python(
        extracted,
        "-c",
        "import mydock_dock, mydock_common; print(mydock_dock.__file__); print(mydock_common.__file__)",
    )
    assert result.returncode == 0, result.stderr
    for line in result.stdout.splitlines():
        assert line.startswith(str(extracted)), line  # pas le dépôt : le test serait sans valeur


def test_every_packaged_module_imports_on_its_own(extracted):
    modules = sorted(
        ".".join(p.relative_to(extracted / DIST).with_suffix("").parts).removesuffix(".__init__")
        for p in (extracted / DIST).rglob("*.py")
        if p.name != "__main__.py"
    )
    code = "import importlib\n" + "".join(f"importlib.import_module({m!r})\n" for m in modules)
    result = packaged_python(extracted, "-c", code)
    assert result.returncode == 0, result.stderr


def test_the_installed_command_reports_its_version(extracted):
    from mydock_dock import __version__

    result = packaged_python(extracted, extracted / "usr/bin/mydock-dock", "--version")
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == f"mydock-dock {__version__}"


def http_get(url: str, *, timeout: float = 3.0) -> tuple[int, bytes]:
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(url, timeout=timeout) as response:
        return response.status, response.read()


def wait_for(url: str, process: subprocess.Popen, *, tries: int = 150) -> None:
    for _ in range(tries):
        if process.poll() is not None:
            raise AssertionError(
                "le dock du paquet s'est arrêté : " + (process.stderr.read() or "")
            )
        try:
            http_get(url, timeout=1.0)
            return
        except OSError:
            time.sleep(0.1)
    raise AssertionError(f"{url} ne répond pas")


def test_the_packaged_dock_serves_its_interface_and_the_pc_package(extracted, agent_deb, tmp_path):
    """Le paquet fait ce qu'il promet : une fois installé, le Pi sert l'interface sur le port local et,
    sur le portail, la page d'installation et le paquet du PC (octet pour octet)."""
    config = tmp_path / "dock.toml"
    server_port, portal_port = free_port(), free_port()
    init = packaged_python(
        extracted,
        extracted / "usr/bin/mydock-dock",
        "init",
        "--config", config,
        "--state-dir", tmp_path / "state",
        "--web-dir", extracted / "usr/share/mydock/web",
        "--clients-dir", extracted / "usr/share/mydock/clients",
        "--port", server_port,
        "--portal-port", portal_port,
    )  # fmt: skip
    assert init.returncode == 0, init.stderr
    # Le portail écoute le réseau local en vrai (0.0.0.0) : le test, lui, reste sur la boucle locale.
    config.write_text(
        config.read_text().replace('listen_host = "0.0.0.0"', 'listen_host = "127.0.0.1"')
    )

    process = subprocess.Popen(
        [sys.executable, str(extracted / "usr/bin/mydock-dock"), "run", "--config", str(config)],
        env=packaged_env(extracted),
        cwd=extracted,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        wait_for(f"http://127.0.0.1:{portal_port}/api/portal", process)
        # Chaque fichier de l'interface est servi, octet pour octet : une page de moins et un onglet reste vide.
        web = extracted / "usr/share/mydock/web"
        for file in sorted(p for p in web.rglob("*") if p.is_file()):
            relative = file.relative_to(web).as_posix()
            got, body = http_get(f"http://127.0.0.1:{server_port}/{relative}")
            assert got == 200, relative
            assert body == file.read_bytes(), relative

        status, page = http_get(f"http://127.0.0.1:{server_port}/")
        assert status == 200
        assert b'<div id="app">' in page or b'id="app"' in page  # l'interface compilée, embarquée

        status, state = http_get(f"http://127.0.0.1:{portal_port}/api/portal")
        assert status == 200
        assert b'"app":"mydock"' in state.replace(b" ", b"")

        status, home = http_get(f"http://127.0.0.1:{portal_port}/")
        assert status == 200
        assert agent_deb.name.encode() in home
        assert b"mydock-agent setup --pi" in home  # la commande d'association est sur la page

        status, body = http_get(f"http://127.0.0.1:{portal_port}/clients/{agent_deb.name}")
        assert status == 200
        assert body == agent_deb.read_bytes()
    finally:
        process.terminate()
        try:
            _, error = process.communicate(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            _, error = process.communicate()
    assert "Traceback" not in error


def test_the_packaged_setup_script_finds_its_library_inside_the_package(extracted, tmp_path):
    lib = extracted / "usr/lib/mydock"
    wrapper = tmp_path / "mydock-dock"
    wrapper.write_text(
        f'#!/bin/sh\nPYTHONPATH="{extracted / DIST}" exec "{sys.executable}" '
        f'"{extracted / "usr/bin/mydock-dock"}" "$@"\n'
    )
    wrapper.chmod(0o755)
    prefix = tmp_path / "root"
    env = {
        **{k: v for k, v in os.environ.items() if k not in ("PYTHONPATH", "MYDOCK_CONFIG")},
        "MYDOCK_DOCK_BIN": str(wrapper),
        "NO_COLOR": "1",
    }
    result = subprocess.run(
        ["bash", str(lib / "setup"), "--prefix", str(prefix), "--kiosk", "systemd"],
        capture_output=True,
        text=True,
        env=env,
        cwd=tmp_path,
        timeout=120,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert mode(prefix / "etc/mydock/dock.toml") == 0o600
    assert (prefix / "etc/mydock-kiosk/labwc/autostart").is_file()
    assert (
        "/usr/lib/mydock/kiosk-browser.sh &"
        in (prefix / "etc/mydock-kiosk/labwc/autostart").read_text()
    )


def test_the_packaged_doctor_runs_without_a_pc(extracted, tmp_path):
    config = tmp_path / "dock.toml"
    init = packaged_python(
        extracted,
        extracted / "usr/bin/mydock-dock",
        "init",
        "--config", config,
        "--state-dir", tmp_path / "state",
        "--web-dir", extracted / "usr/share/mydock/web",
        "--clients-dir", extracted / "usr/share/mydock/clients",
    )  # fmt: skip
    assert init.returncode == 0, init.stderr
    result = packaged_python(
        extracted, extracted / "usr/bin/mydock-dock", "doctor", "--config", config, "--offline"
    )
    assert result.returncode in (0, 1), result.stdout + result.stderr  # un dock neuf a des réserves
    assert "Traceback" not in result.stderr
    assert "Portail (installation du PC)" in result.stdout
    assert "Interface complète" in result.stdout  # l'interface du paquet ne manque d'aucune page
