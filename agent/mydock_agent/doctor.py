"""`mydock-agent doctor` : diagnostic lisible de l'installation, en français.

Il ne modifie rien et n'affiche jamais le jeton. Code de sortie 1 s'il y a au moins un ✘.
"""

from __future__ import annotations

import asyncio
import shutil
import sys
from pathlib import Path

import psutil

from mydock_agent import __version__
from mydock_agent.access import local_lan_networks
from mydock_agent.collectors.host import list_interfaces
from mydock_agent.config import AgentConfig, default_config_path, load_config
from mydock_agent.session import GRAPHICAL_VARS, detect_session, user_manager_environment
from mydock_common.auth import bearer_headers
from mydock_common.config import ConfigError
from mydock_common.protocol import PROTOCOL_VERSION, loads
from mydock_common.report import FAIL, INFO, OK, WARN, Report


def check_python(report: Report) -> None:
    version = ".".join(map(str, sys.version_info[:3]))
    report.add(OK, f"Python {version}, agent {__version__}, protocole {PROTOCOL_VERSION}")


def check_config(report: Report, path: Path) -> AgentConfig | None:
    report.section(f"Configuration ({path})")
    try:
        config = load_config(path)
    except ConfigError as exc:
        report.add(FAIL, str(exc))
        return None
    report.add(OK, "Fichier lisible, en droits 600, jeton valide (non affiché)")
    report.add(INFO, f"Écoute sur {config.bind_host}:{config.port}")
    if not config.allowed_clients:
        lan = ", ".join(str(n) for n in local_lan_networks()) or "aucun réseau détecté"
        report.add(
            WARN,
            f"allowed_clients est vide : seuls les réseaux locaux de ce PC sont acceptés ({lan}). "
            "Mets l'adresse IP du Pi pour limiter l'accès à lui seul.",
        )
    else:
        wide = [n for n in config.allowed_clients if n.num_addresses > 256]
        names = ", ".join(str(n) for n in config.allowed_clients)
        if wide:
            report.add(WARN, f"Clients autorisés : {names} (réseau large : préfère l'IP du Pi)")
        else:
            report.add(OK, f"Clients autorisés : {names}")
    return config


def check_session(report: Report) -> None:
    report.section("Session graphique")
    session = detect_session()
    if session.type == "wayland":
        desktop = session.desktop or "bureau inconnu"
        report.add(OK, f"Wayland ({desktop}), WAYLAND_DISPLAY={session.wayland_display}")
    elif session.type == "x11":
        report.add(OK, f"X11 ({session.desktop or 'bureau inconnu'}), DISPLAY={session.display}")
    else:
        report.add(
            WARN,
            f"Session « {session.type} » : lance le diagnostic depuis un terminal de ta session "
            "graphique (et non en SSH) pour qu'il voie l'écran.",
        )
    manager = user_manager_environment()
    if manager is None:
        report.add(INFO, "systemd utilisateur non joignable depuis ce terminal")
        return
    missing = [v for v in ("WAYLAND_DISPLAY", "DISPLAY") if v not in manager]
    if len(missing) == 2:
        report.add(
            WARN,
            "Ni WAYLAND_DISPLAY ni DISPLAY dans l'environnement de systemd --user : le lancement "
            "d'applications échouera tant que la session n'a pas publié son environnement.",
        )
    else:
        known = [v for v in GRAPHICAL_VARS if v in manager]
        report.add(OK, "Environnement graphique visible par systemd --user : " + ", ".join(known))


def check_network(report: Report) -> None:
    report.section("Réseau")
    interfaces = list_interfaces()
    if not interfaces:
        report.add(WARN, "Aucune interface physique détectée")
        return
    for itf in interfaces:
        state = "actif" if itf["up"] else "inactif"
        addresses = ", ".join(itf["ipv4"]) or "pas d'IPv4"
        route = " [route par défaut]" if itf["default"] else ""
        report.add(INFO, f"{itf['name']} ({itf['kind']}, {state}) {itf['mac']} {addresses}{route}")
    wired_up = [i for i in interfaces if i["kind"] == "ethernet" and i["up"]]
    if not wired_up:
        report.add(
            WARN,
            "Aucun câble Ethernet actif : le réveil d'un PC éteint (Wake-on-LAN) ne fonctionne "
            "en général qu'en Ethernet. En Wi-Fi, au mieux depuis la veille et selon la machine.",
        )


def check_tools(report: Report) -> None:
    report.section("Outils système")
    for name, why, required in (
        ("gio", "lancer des applications", True),
        ("systemd-run", "lancer des applications hors de l'agent", True),
        ("gtk-launch", "lancer par identifiant .desktop", False),
        ("pactl", "son (paquet pulseaudio-utils)", False),
        ("nmcli", "réglage Wake-on-LAN", False),
        ("ethtool", "lecture Wake-on-LAN", False),
    ):
        if shutil.which(name):
            report.add(OK, f"{name} ({why})")
        else:
            report.add(FAIL if required else WARN, f"{name} introuvable ({why})")


async def _handshake(host: str, port: int, token: str) -> str:
    from websockets.asyncio.client import connect
    from websockets.exceptions import InvalidStatus

    try:
        async with connect(
            f"ws://{host}:{port}/ws",
            additional_headers=bearer_headers(token),
            open_timeout=3,
            ping_interval=None,
            proxy=None,
        ) as ws:
            hello = loads(await asyncio.wait_for(ws.recv(), 3))
            if hello.get("t") == "hello" and hello.get("proto") == PROTOCOL_VERSION:
                return "ok"
            return f"réponse inattendue : {hello.get('t')}"
    except InvalidStatus as exc:
        return f"refusé (HTTP {exc.response.status_code}) : jeton ou adresse non autorisés"
    except ConnectionRefusedError:
        return "arrêté"
    except (OSError, TimeoutError) as exc:
        return f"injoignable ({exc.__class__.__name__})"


def check_running(report: Report, config: AgentConfig, cpu_seconds: float) -> None:
    report.section("Agent en marche")
    host = "127.0.0.1" if config.bind_host in ("0.0.0.0", "") else config.bind_host
    outcome = asyncio.run(_handshake(host, config.port, config.token))
    if outcome == "ok":
        report.add(OK, f"L'agent répond sur {host}:{config.port} et accepte le jeton")
    elif outcome == "arrêté":
        report.add(
            WARN,
            f"Aucun agent n'écoute sur {host}:{config.port}. "
            "Démarre-le : systemctl --user start mydock-agent  "
            "(journal : journalctl --user -u mydock-agent -e)",
        )
        return
    else:
        report.add(FAIL, f"L'agent {outcome}")
        return
    agents = [
        p
        for p in psutil.process_iter(["pid", "cmdline"])
        if p.info["cmdline"]
        and "mydock_agent" in " ".join(p.info["cmdline"])
        and "doctor" not in p.info["cmdline"]
    ]
    for proc in agents[:1]:
        usage = proc.cpu_percent(interval=cpu_seconds)
        mark = OK if usage < 2.0 else WARN
        report.add(
            mark,
            f"CPU de l'agent (pid {proc.pid}) : {usage:.1f} % sur {cpu_seconds:.0f} s "
            "(objectif : moins de 2 %)",
        )


def run_doctor(config_path: Path | None = None, *, cpu_seconds: float = 3.0) -> int:
    path = Path(config_path) if config_path else default_config_path()
    report = Report()
    report.section("MyDock - diagnostic de l'agent")
    check_python(report)
    config = check_config(report, path)
    check_session(report)
    check_network(report)
    check_tools(report)
    if config is not None:
        check_running(report, config, cpu_seconds)
    print(report.render())
    return 1 if report.failed else 0


__all__ = ["run_doctor"]
