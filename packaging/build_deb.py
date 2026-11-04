#!/usr/bin/env python3
"""Construit les paquets Debian de MyDock : le dock du Raspberry Pi et l'agent du PC.

    python3 packaging/build_deb.py                  # les deux, dans build/
    python3 packaging/build_deb.py --target pi      # build/mydock_<version>_all.deb
    python3 packaging/build_deb.py --target agent   # build/mydock-agent_<version>_all.deb

Le paquet du Pi (`mydock`) est **le seul à installer à la main** : il embarque celui du PC
(`mydock-agent`), que le Pi sert sur le réseau local à tout PC qui ouvre sa page d'accueil.

Les deux sont du Python pur (« Architecture: all ») : ils s'appuient sur les paquets python3-* de la
distribution (fastapi, uvicorn, websockets, psutil, pydantic, tomli-w), sans environnement virtuel
ni accès réseau à l'installation. Pas besoin de debhelper ni de fakeroot : seulement `dpkg-deb`,
présent sur toute machine Debian ou Ubuntu.

Les dépendances viennent des pyproject.toml (common, agent, dock/server) : une seule source de
vérité, pour que les paquets et l'environnement de développement ne divergent pas.
"""

from __future__ import annotations

import argparse
import email.utils
import gzip
import hashlib
import io
import os
import re
import shutil
import subprocess
import sys
import tempfile
import tomllib
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCES = ROOT / "packaging" / "deb"  # l'agent
PI_SOURCES = ROOT / "packaging" / "deb-pi"  # le dock
PACKAGE = "mydock-agent"
PI_PACKAGE = "mydock"
PYTHON_DIST = "usr/lib/python3/dist-packages"

# Paquet PyPI -> paquet Debian. Une dépendance absente d'ici fait échouer la construction : il
# faut alors décider consciemment quel paquet de la distribution la fournit.
DEBIAN_NAMES = {
    "dbus-fast": "python3-dbus-fast",
    "fastapi": "python3-fastapi",
    "psutil": "python3-psutil",
    "pydantic": "python3-pydantic",
    "tomli-w": "python3-tomli-w",
    "uvicorn": "python3-uvicorn",
    "websockets": "python3-websockets",
}
# Paquets internes (installés par ce même .deb) et dépendances système sans équivalent PyPI.
INTERNAL = {"mydock-common"}
SYSTEM_DEPENDS = ["systemd"]
PI_SYSTEM_DEPENDS = [
    "systemd",
    "adduser",
    "curl",
    "procps",
    "labwc",
    "chromium | chromium-browser",
    "wlr-randr",
    "swayidle",
    "fonts-noto-core",
    "fonts-noto-color-emoji",
    "libgl1-mesa-dri",
    "libegl1",
    "libgles2",
    "avahi-daemon",
    "libnss-mdns",
]

PYTHON_PACKAGES = (ROOT / "common" / "mydock_common", ROOT / "agent" / "mydock_agent")
PI_PYTHON_PACKAGES = (ROOT / "common" / "mydock_common", ROOT / "dock" / "server" / "mydock_dock")
EXCLUDED_FILES = {"testing.py"}  # aides de test : ne font pas partie du produit
CONFFILES = ["/etc/ufw/applications.d/mydock-agent"]
WEB_DIST = ROOT / "dock" / "web" / "dist"


class BuildError(Exception):
    pass


# --- métadonnées ------------------------------------------------------------------------------


def _version_of(init_file: Path) -> str:
    text = init_file.read_text()
    match = re.search(r'^__version__\s*=\s*"([^"]+)"', text, re.MULTILINE)
    if not match:
        raise BuildError(f"__version__ introuvable dans {init_file.relative_to(ROOT)}")
    return match.group(1)


def package_version() -> str:
    """La version de l'agent (celle de son code)."""
    return _version_of(ROOT / "agent" / "mydock_agent" / "__init__.py")


def dock_version() -> str:
    """La version du dock (celle de son code)."""
    return _version_of(ROOT / "dock" / "server" / "mydock_dock" / "__init__.py")


def parse_changelog(text: str, package: str = PACKAGE) -> tuple[str, int]:
    """Version et date (epoch) de la première entrée du changelog au format Debian."""
    head = re.match(
        rf"^{re.escape(package)} \(([^)\s]+)\) \S+; urgency=\w+\s*$", text.splitlines()[0]
    )
    trailer = re.search(r"^ -- .+ <[^>]+>  (.+)$", text, re.MULTILINE)
    if not head or not trailer:
        raise BuildError("le changelog n'est pas au format Debian")
    parsed = email.utils.parsedate_to_datetime(trailer.group(1))
    return head.group(1), int(parsed.timestamp())


def _lower_bounds(requirement: str) -> tuple[str, list[str]]:
    """« fastapi>=0.115,<1 » -> ("fastapi", ["0.115"]). Les bornes hautes sont volontairement
    ignorées : une distribution qui met une dépendance à jour ne doit pas faire retirer le paquet."""
    match = re.match(r"^\s*([A-Za-z0-9_.-]+)\s*(.*)$", requirement)
    if not match:
        raise BuildError(f"dépendance illisible : {requirement!r}")
    name = re.sub(r"[-_.]+", "-", match.group(1)).lower()
    bounds = [
        spec.strip()[2:].strip()
        for spec in match.group(2).split(",")
        if spec.strip().startswith(">=")
    ]
    return name, bounds


def runtime_dependencies(
    projects: tuple[str, ...] = ("common", "agent"),
    system: list[str] | None = None,
) -> list[str]:
    """Champ Depends du paquet, déduit des pyproject.toml."""
    requirements: list[str] = []
    python_bound = ""
    for project in projects:
        data = tomllib.loads((ROOT / project / "pyproject.toml").read_text())["project"]
        requirements += data["dependencies"]
        python_bound = max(python_bound, data["requires-python"].lstrip(">="))
    depends = {f"python3 (>= {python_bound})"}
    for requirement in requirements:
        name, bounds = _lower_bounds(requirement)
        if name in INTERNAL:
            continue
        if name not in DEBIAN_NAMES:
            raise BuildError(
                f"Dépendance « {name} » sans paquet Debian connu : ajoute-la à DEBIAN_NAMES "
                "dans packaging/build_deb.py (apt-cache search python3-<nom>)."
            )
        debian = DEBIAN_NAMES[name]
        depends.add(f"{debian} (>= {bounds[0]})" if bounds else debian)
    return sorted(depends) + (SYSTEM_DEPENDS if system is None else system)


# --- assemblage -------------------------------------------------------------------------------


def _write(path: Path, content: bytes | str, mode: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content.encode() if isinstance(content, str) else content)
    path.chmod(mode)


def _copy(source: Path, destination: Path, mode: int) -> None:
    _write(destination, source.read_bytes(), mode)


def _copy_python_package(package: Path, root: Path) -> None:
    for source in sorted(package.rglob("*")):
        relative = source.relative_to(package.parent)
        if "__pycache__" in relative.parts or source.name in EXCLUDED_FILES:
            continue
        if source.is_dir():
            continue
        if source.suffix != ".py":
            raise BuildError(f"Fichier inattendu dans un paquet Python : {relative}")
        _copy(source, root / PYTHON_DIST / relative, 0o644)


def _wol_script() -> str:
    """Script de réveil à distance du dépôt, adapté à son emplacement dans le paquet."""
    text = (ROOT / "deploy" / "pc" / "enable-wol.sh").read_text()
    old = '. "$HERE/../lib.sh"'
    if old not in text:
        raise BuildError("enable-wol.sh ne source plus ../lib.sh : adapte packaging/build_deb.py")
    return text.replace(old, '. "$HERE/lib.sh"').replace(
        "# shellcheck source=deploy/lib.sh", "# shellcheck source=lib.sh"
    )


def _doc(root: Path, package: str, sources: Path) -> None:
    doc = root / "usr/share/doc" / package
    _copy(sources / "README.Debian", doc / "README.Debian", 0o644)
    _copy(sources / "copyright", doc / "copyright", 0o644)
    packed = io.BytesIO()
    with gzip.GzipFile(fileobj=packed, mode="wb", mtime=0, compresslevel=9) as handle:
        handle.write((sources / "changelog").read_bytes())
    _write(doc / "changelog.gz", packed.getvalue(), 0o644)


def stage(root: Path) -> None:
    """Écrit l'arborescence du paquet de l'agent (hors DEBIAN) dans `root`."""
    for package in PYTHON_PACKAGES:
        _copy_python_package(package, root)

    _copy(SOURCES / "mydock-agent", root / "usr/bin/mydock-agent", 0o755)
    _copy(
        SOURCES / "mydock-agent.service", root / "usr/lib/systemd/user/mydock-agent.service", 0o644
    )
    # Activé pour tous les utilisateurs ; chacun le met en route avec « mydock-agent setup ».
    wants = root / "usr/lib/systemd/user/graphical-session.target.wants"
    wants.mkdir(parents=True, exist_ok=True)
    (wants / "mydock-agent.service").symlink_to("../mydock-agent.service")

    _copy(SOURCES / "mydock-agent.ufw", root / "etc/ufw/applications.d/mydock-agent", 0o644)

    lib = root / "usr/lib/mydock-agent"
    _copy(SOURCES / "restart-user-agents", lib / "restart-user-agents", 0o755)
    _write(lib / "enable-wol.sh", _wol_script(), 0o755)
    _copy(ROOT / "deploy" / "lib.sh", lib / "lib.sh", 0o644)

    _doc(root, PACKAGE, SOURCES)


def stage_pi(root: Path, agent_deb: Path) -> None:
    """Écrit l'arborescence du paquet du Pi (hors DEBIAN) dans `root`, avec le paquet du PC dedans."""
    if not (WEB_DIST / "index.html").is_file():
        raise BuildError(
            "L'interface compilée est absente (dock/web/dist) : elle est normalement fournie par "
            "le dépôt (git pull) ; sinon « make web » la construit."
        )
    for package in PI_PYTHON_PACKAGES:
        _copy_python_package(package, root)

    _copy(PI_SOURCES / "mydock-dock", root / "usr/bin/mydock-dock", 0o755)
    _copy(
        PI_SOURCES / "mydock-dock.service",
        root / "usr/lib/systemd/system/mydock-dock.service",
        0o644,
    )

    # L'interface compilée.
    for source in sorted(WEB_DIST.rglob("*")):
        if source.is_file():
            _copy(source, root / "usr/share/mydock/web" / source.relative_to(WEB_DIST), 0o644)

    # Le paquet du PC, servi par le portail.
    _copy(agent_deb, root / "usr/share/mydock/clients" / agent_deb.name, 0o644)

    # Kiosque, veille de l'écran et mise en place du système : lancés par le paquet lui-même.
    lib = root / "usr/lib/mydock"
    pi = ROOT / "deploy" / "pi"
    _copy(pi / "kiosk-browser.sh", lib / "kiosk-browser.sh", 0o755)
    _copy(pi / "screen-sleep.sh", lib / "screen-sleep.sh", 0o755)
    _copy(pi / "setup.sh", lib / "setup", 0o755)
    _copy(pi / "mydock-kiosk.service", lib / "mydock-kiosk.service", 0o644)
    _copy(pi / "labwc" / "autostart", lib / "labwc" / "autostart", 0o644)
    _copy(ROOT / "deploy" / "lib.sh", lib / "lib.sh", 0o644)

    _doc(root, PI_PACKAGE, PI_SOURCES)


def _all_paths(root: Path) -> list[Path]:
    return sorted(root.rglob("*"))


def _installed_size_kib(root: Path) -> int:
    files = (p for p in _all_paths(root) if p.is_file() and not p.is_symlink())
    return sum(-(-p.stat().st_size // 1024) for p in files) + sum(
        1 for p in _all_paths(root) if p.is_dir()
    )


def _normalize(root: Path, epoch: int) -> None:
    """Dates identiques partout (construction reproductible) et droits explicites."""
    for path in [root, *_all_paths(root)]:
        if path.is_symlink():
            os.utime(path, (epoch, epoch), follow_symlinks=False)
            continue
        if path.is_dir():
            path.chmod(0o755)
        os.utime(path, (epoch, epoch))


def write_control(
    debian: Path,
    version: str,
    installed_size: int,
    *,
    template: Path | None = None,
    depends: list[str] | None = None,
) -> None:
    control = (template or SOURCES / "control.in").read_text()
    for marker, value in (
        ("@VERSION@", version),
        ("@INSTALLED_SIZE@", str(installed_size)),
        ("@DEPENDS@", ", ".join(depends if depends is not None else runtime_dependencies())),
    ):
        control = control.replace(marker, value)
    _write(debian / "control", control, 0o644)


@dataclass(frozen=True)
class Target:
    """Ce qui distingue un paquet d'un autre ; l'assemblage, lui, est le même."""

    package: str
    sources: Path
    version: Callable[[], str]
    depends: Callable[[], list[str]]
    scripts: tuple[str, ...]
    conffiles: tuple[str, ...]


def _agent_target() -> Target:
    return Target(
        package=PACKAGE,
        sources=SOURCES,
        version=lambda: package_version(),  # résolu à l'appel : les tests peuvent le remplacer
        depends=lambda: runtime_dependencies(),
        scripts=("postinst", "prerm"),
        conffiles=tuple(CONFFILES),
    )


def _pi_target() -> Target:
    return Target(
        package=PI_PACKAGE,
        sources=PI_SOURCES,
        version=lambda: dock_version(),
        depends=lambda: runtime_dependencies(("common", "dock/server"), PI_SYSTEM_DEPENDS),
        scripts=("postinst", "prerm", "postrm"),
        conffiles=(),
    )


def _assemble(
    target: Target,
    output: Path,
    epoch: int | None,
    fill: Callable[[Path], None],
) -> Path:
    version = target.version()
    changelog_version, changelog_epoch = parse_changelog(
        (target.sources / "changelog").read_text(), target.package
    )
    if changelog_version != version:
        raise BuildError(
            f"{target.sources.relative_to(ROOT)}/changelog parle de la version {changelog_version}, "
            f"le code de la version {version} : ajoute l'entrée manquante en tête du changelog."
        )
    if shutil.which("dpkg-deb") is None:
        raise BuildError("dpkg-deb est introuvable : cette commande se lance sur Debian ou Ubuntu.")
    if epoch is None:
        epoch = int(os.environ.get("SOURCE_DATE_EPOCH", changelog_epoch))

    output.mkdir(parents=True, exist_ok=True)
    work = output / f".staging-{target.package}"
    shutil.rmtree(work, ignore_errors=True)
    root = work / f"{target.package}_{version}_all"
    root.mkdir(parents=True)
    try:
        fill(root)
        debian = root / "DEBIAN"
        write_control(
            debian,
            version,
            _installed_size_kib(root),
            template=target.sources / "control.in",
            depends=target.depends(),
        )
        if target.conffiles:
            _write(debian / "conffiles", "".join(f"{name}\n" for name in target.conffiles), 0o644)
        for script in target.scripts:
            _copy(target.sources / script, debian / script, 0o755)
        # md5sums après tout le reste du contenu, avant la normalisation des dates.
        data = [p for p in _all_paths(root) if "DEBIAN" not in p.relative_to(root).parts]
        listing = _md5sums(root, data)
        _write(debian / "md5sums", listing, 0o644)
        _normalize(root, epoch)

        deb = output / f"{target.package}_{version}_all.deb"
        deb.unlink(missing_ok=True)
        env = {**os.environ, "SOURCE_DATE_EPOCH": str(epoch)}
        subprocess.run(
            ["dpkg-deb", "--root-owner-group", "-Zxz", "--build", str(root), str(deb)],
            check=True,
            capture_output=True,
            text=True,
            env=env,
        )
    except subprocess.CalledProcessError as exc:
        raise BuildError(f"dpkg-deb a échoué :\n{exc.stderr}") from None
    finally:
        shutil.rmtree(work, ignore_errors=True)
    return deb


def build(output: Path, *, epoch: int | None = None) -> Path:
    """Construit le .deb de l'agent dans `output` et retourne son chemin."""
    return _assemble(_agent_target(), output, epoch, stage)


def build_pi(output: Path, *, epoch: int | None = None) -> Path:
    """Construit le .deb du Pi (avec celui du PC dedans) dans `output` et retourne son chemin."""
    with tempfile.TemporaryDirectory(prefix="mydock-agent-deb-") as scratch:
        agent = build(Path(scratch), epoch=epoch)
        return _assemble(_pi_target(), output, epoch, lambda root: stage_pi(root, agent))


def _md5sums(root: Path, paths: list[Path]) -> str:
    lines = [
        f"{hashlib.md5(p.read_bytes(), usedforsecurity=False).hexdigest()}  {p.relative_to(root)}"
        for p in paths
        if p.is_file() and not p.is_symlink()
    ]
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Construit les paquets .deb de MyDock.")
    parser.add_argument(
        "--output", type=Path, default=ROOT / "build", help="dossier de sortie (défaut : build/)"
    )
    parser.add_argument(
        "--target",
        choices=("all", "pi", "agent"),
        default="all",
        help="pi : le dock du Raspberry Pi (le seul à installer à la main) ; agent : le PC ; "
        "all : les deux (défaut)",
    )
    args = parser.parse_args(argv)
    try:
        built = []
        if args.target in ("all", "pi"):
            built.append(build_pi(args.output))
        if args.target in ("all", "agent"):
            built.append(build(args.output))
    except BuildError as exc:
        print(f"Erreur : {exc}", file=sys.stderr)
        return 1
    for deb in built:
        print(deb)
    pi = next((d for d in built if d.name.startswith(f"{PI_PACKAGE}_")), None)
    if pi is not None:
        print(f"Sur le Raspberry Pi : sudo apt install {pi}", file=sys.stderr)
    else:
        print(f"Sur le PC : sudo apt install {built[0]}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
