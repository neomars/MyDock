"""Ligne de commande du serveur dock.

mydock-dock run                 démarre le serveur (commande par défaut)
mydock-dock init --pc ubuntu.local --token-stdin    crée ou met à jour la configuration
mydock-dock doctor              diagnostic de l'installation
"""

from __future__ import annotations

import argparse
import getpass
import logging
import os
import sys
from pathlib import Path

import uvicorn

from mydock_common.config import ConfigError
from mydock_common.protocol import MAX_MESSAGE_BYTES
from mydock_dock import __version__
from mydock_dock.app import DockServer, create_app
from mydock_dock.config import DockConfig, default_config_path, init_config
from mydock_dock.doctor import run_doctor
from mydock_dock.validation import ValidationFailure

log = logging.getLogger("mydock_dock")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="mydock-dock",
        description="Serveur local du dock MyDock (Raspberry Pi).",
    )
    parser.add_argument("--version", action="version", version=f"mydock-dock {__version__}")
    sub = parser.add_subparsers(dest="command")

    run = sub.add_parser("run", help="démarre le serveur (commande par défaut)")
    run.add_argument("--config", type=Path, help="défaut : $MYDOCK_CONFIG ou /etc/mydock/dock.toml")
    run.add_argument("--port", type=int, help="remplace server.port")
    run.add_argument("--web-dir", type=Path, help="remplace paths.web_dir")
    run.add_argument("--state-dir", type=Path, help="remplace paths.state_dir")
    run.add_argument(
        "--demo-markets",
        action="store_true",
        help="cotations de démonstration, sans réseau (captures d'écran et tests)",
    )

    init = sub.add_parser("init", help="crée ou met à jour la configuration (idempotent)")
    init.add_argument("--config", type=Path)
    init.add_argument("--pc", metavar="HOTE", help="nom (.local) ou adresse IPv4 du PC")
    init.add_argument("--pc-port", type=int)
    init.add_argument("--port", type=int, help="port du dock")
    init.add_argument("--state-dir", type=Path)
    init.add_argument("--web-dir", type=Path)
    secret = init.add_mutually_exclusive_group()
    secret.add_argument(
        "--token-stdin", action="store_true", help="lit le jeton sur l'entrée standard"
    )
    secret.add_argument("--token-env", metavar="VARIABLE", help="lit le jeton dans cette variable")
    secret.add_argument(
        "--ask-token", action="store_true", help="demande le jeton (saisie masquée)"
    )

    doctor = sub.add_parser("doctor", help="diagnostic de l'installation")
    doctor.add_argument("--config", type=Path)
    doctor.add_argument(
        "--offline",
        action="store_true",
        help="ne teste pas les fournisseurs de cotations sur Internet",
    )
    return parser


def cmd_run(args: argparse.Namespace) -> int:
    try:
        config = DockConfig.load(args.config)
    except ConfigError as exc:
        print(f"Erreur de configuration : {exc}", file=sys.stderr)
        return 2
    if args.port:
        config.port = args.port
    if args.web_dir:
        config.web_dir = args.web_dir
    if args.state_dir:
        config.state_dir = args.state_dir
    if args.demo_markets:
        config.demo_markets = True
    logging.basicConfig(level=config.log_level, format="%(levelname)s %(name)s: %(message)s")

    server = DockServer(config)
    log.info("MyDock dock %s - http://%s:%d", __version__, config.listen_host, config.port)
    log.info(
        "PC visé : %s:%d (%s)",
        config.agent_host or "non configuré",
        config.agent_port,
        "jeton présent" if config.token else "jeton absent",
    )
    uvicorn.Server(
        uvicorn.Config(
            create_app(server),
            host=config.listen_host,
            port=config.port,
            log_config=None,
            log_level=config.log_level.lower(),
            access_log=False,
            server_header=False,
            date_header=False,
            ws_max_size=MAX_MESSAGE_BYTES,
            ws_ping_interval=20,
            ws_ping_timeout=20,
        )
    ).run()
    return 0


def _read_token(args: argparse.Namespace) -> str | None:
    if args.token_stdin:
        return sys.stdin.readline().strip() or None
    if args.token_env:
        return os.environ.get(args.token_env, "").strip() or None
    if args.ask_token:
        return getpass.getpass("Jeton de l'agent (saisie masquée) : ").strip() or None
    return None


def cmd_init(args: argparse.Namespace) -> int:
    try:
        config = init_config(
            args.config,
            pc_host=args.pc,
            pc_port=args.pc_port,
            token=_read_token(args),
            port=args.port,
            state_dir=args.state_dir,
            web_dir=args.web_dir,
        )
    except (ConfigError, ValidationFailure) as exc:
        print(f"Erreur : {exc}", file=sys.stderr)
        return 2
    print(f"Configuration : {config.path} (droits 600)")
    print(f"PC visé : {config.agent_host or 'non configuré'}:{config.agent_port}")
    print("Jeton : " + ("présent" if config.token else "absent"))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command is None:
        args = parser.parse_args(["run", *(argv if argv is not None else sys.argv[1:])])
    if args.command == "run":
        return cmd_run(args)
    if args.command == "init":
        return cmd_init(args)
    if args.command == "doctor":
        return run_doctor(args.config or default_config_path(), offline=args.offline)
    parser.error(f"commande inconnue : {args.command}")
    return 2


if __name__ == "__main__":
    sys.exit(main())
