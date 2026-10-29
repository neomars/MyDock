"""Ligne de commande de l'agent.

mydock-agent run [--demo]          démarre l'agent (commande par défaut)
mydock-agent init --allow IP_DU_PI crée ou met à jour la configuration (jeton généré)
mydock-agent show-token            affiche le jeton, pour l'appairage avec le Pi
mydock-agent doctor                diagnostic de l'installation
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
from mydock_agent.builtin_actions import register_builtin
from mydock_agent.collectors.host import HostProvider
from mydock_agent.config import (
    DEFAULT_PORT,
    AgentConfig,
    default_config_path,
    init_config,
    load_config,
)
from mydock_agent.demo import DEMO_TOKEN, DemoHostProvider
from mydock_agent.doctor import run_doctor
from mydock_agent.hub import Hub
from mydock_agent.server import create_app
from mydock_agent.session import detect_session
from mydock_common.actions import ActionRegistry
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
    hub = Hub([DemoHostProvider() if args.demo else HostProvider()])
    registry = ActionRegistry()
    register_builtin(registry, demo=args.demo)
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


def cmd_show_token(args: argparse.Namespace) -> int:
    try:
        config = load_config(args.config)
    except ConfigError as exc:
        print(f"Erreur de configuration : {exc}", file=sys.stderr)
        return 2
    print(config.token)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    command = args.command or "run"
    if args.command is None:
        args = parser.parse_args(["run", *(argv if argv is not None else sys.argv[1:])])
    if command == "run":
        return cmd_run(args)
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
