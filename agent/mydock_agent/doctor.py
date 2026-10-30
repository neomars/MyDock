"""`mydock-agent doctor` : diagnostic lisible de l'installation, en français.

Il ne modifie rien et n'affiche jamais le jeton. Code de sortie 1 s'il y a au moins un ✘.
"""

from __future__ import annotations

import asyncio
import shutil
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

import psutil

from mydock_agent import __version__, firewall
from mydock_agent.access import local_lan_networks
from mydock_agent.collectors import gpu as gpu_module
from mydock_agent.collectors import music as music_module
from mydock_agent.collectors.host import list_interfaces
from mydock_agent.collectors.system import Sensors, cpu_temperature, disk_temperature
from mydock_agent.config import AgentConfig, default_config_path, load_config
from mydock_agent.session import GRAPHICAL_VARS, detect_session, user_manager_environment
from mydock_common.auth import bearer_headers
from mydock_common.config import ConfigError
from mydock_common.protocol import PROTOCOL_VERSION, loads
from mydock_common.report import FAIL, INFO, OK, WARN, Report

LOOPBACK_BINDS = ("127.0.0.1", "localhost")


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
    if config.bind_host in LOOPBACK_BINDS:
        report.add(
            WARN,
            f"bind_host = {config.bind_host} : l'agent n'accepte que les connexions de ce PC, le "
            'Pi ne pourra jamais le joindre. Mets bind_host = "0.0.0.0" (ou l\'adresse du PC sur '
            "le réseau) dans ce fichier, puis : systemctl --user restart mydock-agent",
        )
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


def check_firewall(
    report: Report,
    config: AgentConfig,
    *,
    ufw_conf: Path = firewall.UFW_CONF,
    service_active: Callable[[str], bool] = firewall.service_active,
) -> None:
    """Un pare-feu actif est la première cause d'un PC « hors ligne » alors que l'agent tourne."""
    report.section("Pare-feu")
    port = config.port
    shown = False
    ufw = firewall.ufw_state(ufw_conf)
    if ufw:
        shown = True
        report.add(
            WARN,
            f"ufw est actif. Sans règle pour le port {port}, le Pi ne joint pas l'agent et le "
            "dock affiche « PC hors ligne ». Ses règles ne se lisent qu'avec sudo ; vérifie :",
        )
        report.add(INFO, f"    sudo ufw status | grep {port}")
        report.add(INFO, "S'il n'y a aucune ligne, autorise le Pi :")
        for source in firewall.sources(config):
            report.add(INFO, f"    {firewall.ufw_allow_command(source, port)}")
    elif ufw is False:
        shown = True
        report.add(OK, "ufw est installé mais inactif : il ne bloque rien")
    if service_active("firewalld"):
        shown = True
        report.add(
            WARN,
            f"firewalld est actif : ouvre le port {port} si ce n'est pas déjà fait :",
        )
        report.add(INFO, f"    sudo firewall-cmd --permanent --add-port={port}/tcp")
        report.add(INFO, "    sudo firewall-cmd --reload")
    if not shown:
        report.add(INFO, "Aucun pare-feu courant (ufw, firewalld) détecté : rien à ouvrir")


SERVICE_PROPERTIES = (
    "LoadState",
    "ActiveState",
    "SubState",
    "UnitFileState",
    "FragmentPath",
    "ConditionResult",
)


def read_service() -> dict[str, str] | None:
    """Propriétés du service systemd utilisateur, ou None si systemd est injoignable d'ici."""
    if not shutil.which("systemctl"):
        return None
    command = ["systemctl", "--user", "show", "mydock-agent.service"]
    for name in SERVICE_PROPERTIES:
        command += ["-p", name]
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=10, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    return dict(line.split("=", 1) for line in result.stdout.splitlines() if "=" in line)


def check_service(report: Report, read: Callable[[], dict[str, str] | None] = read_service) -> None:
    """Le service est-il installé, d'où vient son unité, tourne-t-il ?"""
    report.section("Service systemd")
    props = read()
    if props is None:
        report.add(INFO, "systemd utilisateur non joignable depuis ce terminal")
        return
    if props.get("LoadState") == "not-found":
        report.add(
            WARN,
            "Service mydock-agent introuvable : installe le paquet "
            "(sudo apt install ./mydock-agent_*.deb) ou lance l'agent à la main (mydock-agent run).",
        )
        return
    path = props.get("FragmentPath", "")
    if path.startswith(("/usr/", "/lib/", "/etc/systemd/")):
        report.add(OK, f"Unité fournie par le système : {path}")
    elif path:
        report.add(
            WARN,
            f"L'unité de ton dossier personnel ({path}) masque celle du paquet : "
            "relance « mydock-agent setup » pour la remplacer.",
        )
    active = props.get("ActiveState", "")
    if active == "active":
        report.add(OK, f"Service actif ({props.get('SubState', '?')})")
    elif active == "failed":
        report.add(FAIL, "Service en échec : journalctl --user -u mydock-agent -e")
    elif props.get("ConditionResult") == "no":
        report.add(
            WARN,
            "Service non démarré : pas encore de configuration. Lance : "
            "mydock-agent setup --pi ADRESSE_IP_DU_PI",
        )
    else:
        report.add(
            WARN,
            f"Service {active or 'inconnu'} : systemctl --user start mydock-agent "
            "(journal : journalctl --user -u mydock-agent -e)",
        )
    if props.get("UnitFileState") in ("masked", "disabled"):
        report.add(WARN, f"Service {props['UnitFileState']} pour ton utilisateur.")


def curl_check(address: str, port: int) -> str:
    """Commande à lancer depuis le Pi : affiche le code HTTP renvoyé par l'agent (404 = joint)."""
    return f"curl -sS -m 5 -o /dev/null -w '%{{http_code}}\\n' http://{address}:{port}/"


def check_from_pi(report: Report, config: AgentConfig) -> None:
    """La seule vraie preuve que le Pi joint ce PC est un essai depuis le Pi : on le prépare."""
    report.section("Vérifier depuis le Pi")
    reachable = [
        (itf, address) for itf in list_interfaces() if itf["up"] for address in itf["ipv4"]
    ]
    if not reachable:
        report.add(
            WARN,
            "Ce PC n'a aucune adresse IPv4 sur un réseau actif : le Pi ne peut pas le joindre.",
        )
        return
    report.add(INFO, "Sur le Pi, lance cette commande (une par adresse possible du PC) :")
    for itf, address in reachable:
        route = " [route par défaut]" if itf["default"] else ""
        report.add(INFO, f"    {curl_check(address, config.port)}   # {itf['name']}{route}")
    report.add(INFO, "404 = le port répond et l'agent est joint (le résultat voulu)")
    report.add(INFO, "403 = l'agent est joint mais refuse l'adresse du Pi (voir allowed_clients)")
    report.add(INFO, "« Connection timed out » = pare-feu du PC, ou Wi-Fi qui isole les appareils")
    report.add(INFO, "« Connection refused » = mauvaise adresse ou port, ou agent arrêté")


def check_sensors(report: Report, sensors: Sensors | None = None) -> None:
    """Ce que l'agent saura mesurer sur ce PC : températures, batterie, cœurs."""
    report.section("Capteurs")
    sensors = sensors or Sensors()
    cores = len(sensors.cpu_percent() or [])
    report.add(OK if cores else WARN, f"Processeur : {cores} cœur(s) vus")
    temps = sensors.temperatures()
    cpu = cpu_temperature(temps)
    if cpu is None:
        report.add(INFO, "Température du processeur : aucun capteur lisible (rien ne s'affichera)")
    else:
        report.add(OK, f"Température du processeur : {cpu:.0f} °C")
    ssd = disk_temperature(temps)
    if ssd is not None:
        report.add(OK, f"Température du SSD : {ssd:.0f} °C")
    battery = sensors.battery()
    if battery is None:
        report.add(INFO, "Pas de batterie (PC fixe)")
    else:
        source = "sur secteur" if battery["plugged"] else "sur batterie"
        report.add(OK, f"Batterie : {battery['percent']} % ({source})")


def check_gpus(report: Report, read: Callable[[], list[dict]] = gpu_module.read_gpus) -> None:
    """Cartes graphiques, lues sans réveiller une carte NVIDIA endormie."""
    report.section("Cartes graphiques")
    gpus = read()
    if not gpus:
        report.add(INFO, "Aucune carte graphique détectée dans /sys/bus/pci")
        return
    for card in gpus:
        if card["state"] == "suspended":
            report.add(
                OK, f"{card['name']} : en veille (économie d'énergie) ; le dock ne la réveille pas"
            )
        elif card.get("note"):
            report.add(WARN, f"{card['name']} : {card['note']}")
        elif card["util"] is not None:
            report.add(OK, f"{card['name']} : active, charge {card['util']} %")
        else:
            freq = f", {card['freq_mhz']} MHz" if card["freq_mhz"] else ""
            report.add(OK, f"{card['name']} : active{freq}")


async def _list_players(factory: Callable[[], music_module.Bus]) -> list[tuple[str, str]]:
    bus = factory()
    await bus.connect()
    try:
        (names,) = await bus.call(
            music_module.DBUS_NAME, "/org/freedesktop/DBus", music_module.DBUS_NAME, "ListNames"
        )
        players = []
        for name in names:
            if not name.startswith(music_module.MPRIS_PREFIX):
                continue
            try:
                (root,) = await bus.call(
                    name,
                    music_module.OBJECT_PATH,
                    music_module.PROPS_IFACE,
                    "GetAll",
                    "s",
                    [music_module.ROOT_IFACE],
                )
                players.append(
                    (name.removeprefix(music_module.MPRIS_PREFIX), str(root.get("Identity", "")))
                )
            except music_module.BusError:
                players.append((name.removeprefix(music_module.MPRIS_PREFIX), ""))
        return players
    finally:
        await bus.disconnect()


def check_music(
    report: Report, factory: Callable[[], music_module.Bus] = music_module.DbusFastBus
) -> None:
    """Le bus de session D-Bus et les lecteurs MPRIS ouverts en ce moment."""
    report.section("Musique (MPRIS)")
    try:
        players = asyncio.run(_list_players(factory))
    except (music_module.BusError, OSError) as exc:
        report.add(
            WARN,
            f"{exc} Lance le diagnostic depuis un terminal de ta session graphique : l'agent a "
            "besoin du bus de session D-Bus pour voir les lecteurs.",
        )
        return
    if not players:
        report.add(INFO, "Bus de session joignable ; aucun lecteur ouvert pour l'instant")
        return
    report.add(OK, "Bus de session joignable ; lecteurs ouverts :")
    for player_id, identity in players:
        report.add(INFO, f"    {player_id}" + (f"  ({identity})" if identity else ""))


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
    if config is not None:
        check_firewall(report, config)
    check_service(report)
    check_tools(report)
    check_sensors(report)
    check_gpus(report)
    check_music(report)
    if config is not None:
        check_running(report, config, cpu_seconds)
        check_from_pi(report, config)
    print(report.render())
    return 1 if report.failed else 0


__all__ = ["run_doctor"]
