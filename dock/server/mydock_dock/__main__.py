"""Ligne de commande du serveur dock.

mydock-dock run                 démarre le serveur (commande par défaut)
mydock-dock init --pc ubuntu.local --token-stdin    crée ou met à jour la configuration
mydock-dock doctor              diagnostic de l'installation
mydock-dock player-session      l'assistant de lecture de l'écran (VLC), lancé par labwc
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
from mydock_dock import __version__, screensaver
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
    init.add_argument("--portal-port", type=int, help="port du portail (page d'installation du PC)")
    init.add_argument("--no-portal", action="store_true", help="désactive le portail")
    init.add_argument("--clients-dir", type=Path, help="dossier des paquets proposés au PC")
    init.add_argument("--state-dir", type=Path)
    init.add_argument("--web-dir", type=Path)
    init.add_argument(
        "--playlist-url", help="Divertissement : la liste M3U des chaînes de la Freebox"
    )
    init.add_argument(
        "--smb-host", help="Divertissement : le serveur de fichiers Samba (la Freebox)"
    )
    secret = init.add_mutually_exclusive_group()
    secret.add_argument(
        "--token-stdin", action="store_true", help="lit le jeton sur l'entrée standard"
    )
    secret.add_argument("--token-env", metavar="VARIABLE", help="lit le jeton dans cette variable")
    secret.add_argument(
        "--ask-token", action="store_true", help="demande le jeton (saisie masquée)"
    )

    saver = sub.add_parser(
        "screensaver",
        help="les vidéos de l'économiseur d'écran (list, add, remove)",
        description=(
            "L'économiseur d'écran a ses propres scènes animées ; les vidéos sont celles que tu ajoutes ici. "
            "Elles vont dans le dossier d'état du dock : lance « add » et « remove » avec sudo."
        ),
    )
    saver.add_argument("--config", type=Path)
    saver_sub = saver.add_subparsers(dest="saver_command", required=True)
    saver_sub.add_parser("list", help="liste les vidéos")
    add = saver_sub.add_parser("add", help="ajoute une vidéo (adresse https ou fichier du disque)")
    add.add_argument("source", help="adresse https d'une vidéo MP4 ou WebM, ou chemin d'un fichier")
    add.add_argument(
        "--name", help="nom du fichier dans le dossier (par défaut : celui de la source)"
    )
    remove = saver_sub.add_parser("remove", help="retire une vidéo")
    remove.add_argument("name")

    player = sub.add_parser(
        "player-session",
        help="l'assistant de lecture de l'écran du dock (lancé par labwc, pilote VLC)",
        description="Tourne dans la session graphique du Pi : démarre VLC en plein écran quand le dock le demande.",
    )
    player.add_argument(
        "--url", default=None, help="adresse du dock (ws://127.0.0.1:8080/ws/player)"
    )
    player.add_argument("--demo", action="store_true", help="lecture simulée, sans VLC (essais)")
    player.add_argument("-v", "--verbose", action="store_true")

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


def cmd_screensaver(args: argparse.Namespace) -> int:
    try:
        directory = DockConfig.load(args.config).saver_dir
    except ConfigError as exc:
        print(f"Erreur de configuration : {exc}", file=sys.stderr)
        return 2
    try:
        if args.saver_command == "list":
            found = screensaver.videos(directory)
            if not found:
                print(f"Aucune vidéo dans {directory} : l'économiseur montre ses scènes animées.")
            for video in found:
                print(f"{video.name}  ({screensaver.human_size(video.size)})")
            return 0
        if args.saver_command == "add":
            if args.source.startswith(("http://", "https://")):
                saved = screensaver.add_url(directory, args.source, args.name)
            else:
                saved = screensaver.add_file(directory, Path(args.source).expanduser(), args.name)
            print(
                f"Ajoutée : {saved.name} ({screensaver.human_size(saved.stat().st_size)}), dans {directory}."
            )
            print("L'économiseur la montrera au prochain passage (aucun redémarrage).")
            return 0
        screensaver.remove(directory, args.name)
        print(f"Retirée : {args.name}.")
        return 0
    except screensaver.SaverError as exc:
        print(f"Erreur : {exc}", file=sys.stderr)
        return 1


def cmd_init(args: argparse.Namespace) -> int:
    try:
        config = init_config(
            args.config,
            pc_host=args.pc,
            pc_port=args.pc_port,
            token=_read_token(args),
            port=args.port,
            portal_port=args.portal_port,
            portal_enabled=False if args.no_portal else None,
            clients_dir=args.clients_dir,
            state_dir=args.state_dir,
            web_dir=args.web_dir,
            playlist_url=args.playlist_url,
            smb_host=args.smb_host,
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
    if args.command == "screensaver":
        return cmd_screensaver(args)
    if args.command == "player-session":
        from mydock_dock import player_session

        forwarded = ["--url", args.url] if args.url else []
        forwarded += ["--demo"] if args.demo else []
        forwarded += ["-v"] if args.verbose else []
        return player_session.main(forwarded)
    if args.command == "doctor":
        return run_doctor(args.config or default_config_path(), offline=args.offline)
    parser.error(f"commande inconnue : {args.command}")
    return 2


if __name__ == "__main__":
    sys.exit(main())
