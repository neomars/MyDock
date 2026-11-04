"""Ligne de commande de l'agent.

mydock-agent setup --pi IP_DU_PI   première configuration : jeton, service, pare-feu, association au dock
mydock-agent pair --pi IP_DU_PI    associe ce PC (déjà configuré) à un dock, avec le code de son écran
mydock-agent run [--demo]          démarre l'agent (commande par défaut ; le service s'en charge)
mydock-agent init --allow IP_DU_PI crée ou met à jour la configuration seule (jeton généré)
mydock-agent show-token            affiche le jeton, pour l'appairage avec le Pi
mydock-agent doctor                diagnostic de l'installation
mydock-agent enable-wol            prépare le PC à être allumé à distance (Wake-on-LAN)
"""

from __future__ import annotations

import argparse
import ipaddress
import logging
import os
import sys
from pathlib import Path

import uvicorn

from mydock_agent import __version__
from mydock_agent.access import AccessGate
from mydock_agent.config import (
    DEFAULT_PORT,
    AgentConfig,
    default_config_path,
    init_config,
    load_config,
)
from mydock_agent.demo import DEMO_TOKEN
from mydock_agent.doctor import run_doctor
from mydock_agent.hub import Hub
from mydock_agent.pairing import PORTAL_PORT
from mydock_agent.runtime import build_runtime
from mydock_agent.server import create_app
from mydock_agent.session import detect_session
from mydock_agent.setup import run_pair, run_setup
from mydock_common.config import ConfigError
from mydock_common.protocol import MAX_MESSAGE_BYTES

log = logging.getLogger("mydock_agent")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="mydock-agent",
        description=(
            "Agent MyDock : expose l'état du PC et une liste blanche d'actions au dock tactile."
        ),
    )
    parser.add_argument("--version", action="version", version=f"mydock-agent {__version__}")
    sub = parser.add_subparsers(dest="command")

    run = sub.add_parser("run", help="démarre l'agent (commande par défaut)")
    run.add_argument(
        "--config",
        type=Path,
        help="fichier de configuration (défaut : ~/.config/mydock/agent.toml)",
    )
    run.add_argument(
        "--demo", action="store_true", help="fausses données, écoute en local seulement"
    )
    run.add_argument("--host", help="adresse d'écoute (remplace bind_host)")
    run.add_argument("--port", type=int, help="port d'écoute (remplace port)")

    init = sub.add_parser("init", help="crée ou met à jour la configuration (idempotent)")
    init.add_argument("--config", type=Path)
    init.add_argument(
        "--allow",
        action="append",
        default=[],
        metavar="IP_OU_RESEAU",
        help="client autorisé (répétable) ; idéalement l'IP du Pi",
    )
    init.add_argument("--port", type=int)
    init.add_argument("--bind", help="adresse d'écoute IPv4")
    init.add_argument("--rotate-token", action="store_true", help="génère un nouveau jeton")

    setup = sub.add_parser(
        "setup",
        help="première configuration : jeton, service, pare-feu et consignes pour le Pi",
        description=(
            "Crée la configuration (jeton généré, droits 600), démarre le service systemd "
            "utilisateur, dit quoi faire pour le pare-feu et ce qu'il faut saisir sur le Pi. "
            "Idempotent : peut être relancé sans changer le jeton."
        ),
    )
    setup.add_argument("--config", type=Path)
    setup.add_argument(
        "--pi",
        action="append",
        default=[],
        metavar="IP_OU_RESEAU",
        help="client autorisé (répétable) : l'adresse IP du Pi",
    )
    setup.add_argument("--port", type=int)
    setup.add_argument("--bind", help="adresse d'écoute IPv4 (défaut : 0.0.0.0)")
    setup.add_argument("--rotate-token", action="store_true", help="génère un nouveau jeton")
    setup.add_argument(
        "--open-firewall",
        action="store_true",
        help="si ufw est actif, autorise le Pi (sudo ufw allow ; demande ton mot de passe)",
    )
    setup.add_argument("--no-service", action="store_true", help="ne touche pas au service systemd")
    setup.add_argument(
        "--code",
        metavar="CODE",
        help="code d'association affiché sur l'écran du dock (sinon il est demandé)",
    )
    setup.add_argument(
        "--portal-port",
        type=int,
        default=PORTAL_PORT,
        metavar="PORT",
        help=f"port du portail du dock (défaut : {PORTAL_PORT})",
    )
    setup.add_argument(
        "--no-pair",
        action="store_true",
        help="n'associe pas ce PC au dock (saisie à la main dans les Réglages du dock)",
    )

    pair = sub.add_parser(
        "pair",
        help="associe ce PC, déjà configuré, à un dock avec le code affiché sur son écran",
        description=(
            "Présente au portail du dock le code lu sur son écran : le dock reçoit alors l'adresse "
            "de ce PC et son jeton, rien n'est à recopier. Ne touche ni au service ni au pare-feu."
        ),
    )
    pair.add_argument("--config", type=Path)
    pair.add_argument("--pi", required=True, metavar="ADRESSE", help="l'adresse IP du Pi")
    pair.add_argument(
        "--code", metavar="CODE", help="code affiché sur l'écran du dock (sinon il est demandé)"
    )
    pair.add_argument(
        "--portal-port",
        type=int,
        default=PORTAL_PORT,
        metavar="PORT",
        help=f"port du portail du dock (défaut : {PORTAL_PORT})",
    )

    # Les options de enable-wol (--wifi, --dry-run, --help) sont celles du script : voir main().
    sub.add_parser(
        "enable-wol",
        help="prépare le PC à être allumé à distance (Wake-on-LAN) ; options : --wifi, --dry-run",
        add_help=False,
    )

    token = sub.add_parser("show-token", help="affiche le jeton pour l'appairage")
    token.add_argument("--config", type=Path)

    doctor = sub.add_parser("doctor", help="diagnostic de l'installation")
    doctor.add_argument("--config", type=Path)
    doctor.add_argument(
        "--cpu",
        type=float,
        default=3.0,
        metavar="SECONDES",
        help="durée de la mesure du CPU de l'agent en marche (défaut : 3)",
    )
    return parser


def setup_logging(level: str) -> None:
    logging.basicConfig(level=level, format="%(levelname)s %(name)s: %(message)s")


def cmd_run(args: argparse.Namespace) -> int:
    if args.demo:
        config = AgentConfig(
            token=os.environ.get("MYDOCK_DEMO_TOKEN") or DEMO_TOKEN,
            bind_host="127.0.0.1",
            port=DEFAULT_PORT,
            allowed_clients=(ipaddress.IPv4Network("127.0.0.0/8"),),
            log_level="INFO",
        )
    else:
        try:
            config = load_config(args.config)
        except ConfigError as exc:
            print(f"Erreur de configuration : {exc}", file=sys.stderr)
            return 2
    setup_logging(config.log_level)

    host = args.host or config.bind_host
    port = args.port or config.port
    providers, registry = build_runtime(demo=args.demo)
    hub = Hub(providers)
    gate = AccessGate(config.token, config.allowed_clients)
    app = create_app(hub=hub, registry=registry, gate=gate, demo=args.demo)

    session = detect_session()
    allowed = ", ".join(str(n) for n in config.allowed_clients) or "réseaux locaux de ce PC"
    log.info("MyDock agent %s - session %s (%s)", __version__, session.type, session.desktop or "?")
    log.info("Écoute sur %s:%d - clients autorisés : %s", host, port, allowed)
    if args.demo:
        log.warning(
            "MODE DÉMO : fausses données, jeton de développement public, écoute locale seulement"
        )

    server = uvicorn.Server(
        uvicorn.Config(
            app,
            host=host,
            port=port,
            log_config=None,
            log_level=config.log_level.lower(),
            access_log=False,
            server_header=False,
            date_header=False,
            ws_max_size=MAX_MESSAGE_BYTES,
            ws_ping_interval=10,
            ws_ping_timeout=10,
        )
    )
    server.run()
    return 0


def cmd_init(args: argparse.Namespace) -> int:
    path = args.config or default_config_path()
    try:
        config, created = init_config(
            path, allow=args.allow, port=args.port, bind=args.bind, rotate_token=args.rotate_token
        )
    except ConfigError as exc:
        print(f"Erreur de configuration : {exc}", file=sys.stderr)
        return 2
    print(f"Configuration : {path} (droits 600)")
    print("Jeton : " + ("nouveau jeton généré" if created else "jeton existant conservé"))
    allowed = ", ".join(str(n) for n in config.allowed_clients) or "réseaux locaux de ce PC"
    print(f"Écoute : {config.bind_host}:{config.port} - clients autorisés : {allowed}")
    return 0


def cmd_setup(args: argparse.Namespace) -> int:
    return run_setup(
        config_path=args.config,
        pi=args.pi,
        port=args.port,
        bind=args.bind,
        rotate_token=args.rotate_token,
        open_firewall=args.open_firewall,
        start_service=not args.no_service,
        code=args.code,
        portal_port=args.portal_port,
        pair=not args.no_pair,
    )


def cmd_pair(args: argparse.Namespace) -> int:
    return run_pair(
        config_path=args.config, pi=args.pi, code=args.code, portal_port=args.portal_port
    )


def find_wol_script() -> Path | None:
    """Le script du paquet .deb, ou celui du dépôt quand on lance l'agent depuis les sources."""
    candidates = (
        Path("/usr/lib/mydock-agent/enable-wol.sh"),
        Path(__file__).resolve().parents[2] / "deploy" / "pc" / "enable-wol.sh",
    )
    return next((c for c in candidates if c.is_file()), None)


def cmd_enable_wol(rest: list[str]) -> int:
    script = find_wol_script()
    if script is None:
        print("Erreur : script enable-wol.sh introuvable (réinstalle le paquet).", file=sys.stderr)
        return 2
    os.execv("/bin/bash", ["bash", str(script), *rest])
    return 0  # inatteignable : execv remplace le processus (sert seulement quand il est simulé)


def cmd_show_token(args: argparse.Namespace) -> int:
    try:
        config = load_config(args.config)
    except ConfigError as exc:
        print(f"Erreur de configuration : {exc}", file=sys.stderr)
        return 2
    print(config.token)
    return 0


def main(argv: list[str] | None = None) -> int:
    raw = list(sys.argv[1:] if argv is None else argv)
    if raw[:1] == ["enable-wol"]:
        return cmd_enable_wol(raw[1:])
    parser = build_parser()
    args = parser.parse_args(argv)
    command = args.command or "run"
    if args.command is None:
        args = parser.parse_args(["run", *(argv if argv is not None else sys.argv[1:])])
    if command == "run":
        return cmd_run(args)
    if command == "setup":
        return cmd_setup(args)
    if command == "pair":
        return cmd_pair(args)
    if command == "init":
        return cmd_init(args)
    if command == "show-token":
        return cmd_show_token(args)
    if command == "doctor":
        return run_doctor(args.config, cpu_seconds=args.cpu)
    parser.error(f"commande inconnue : {command}")
    return 2


if __name__ == "__main__":
    sys.exit(main())
