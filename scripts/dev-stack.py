#!/usr/bin/env python3
"""Pile de développement : agent en mode démo + serveur dock servant l'interface compilée.

    ./scripts/dev-stack.py                      # http://127.0.0.1:8080
    ./scripts/dev-stack.py --dock-port 8099 --agent-port 18765

    ./scripts/dev-stack.py --unpaired           # un dock neuf : il affiche un code d'association
    ./scripts/dev-stack.py --state-dir /tmp/x   # l'état du dock (réglages, vidéos de l'économiseur) dans ce dossier

Le portail du dock (la page d'installation du PC) écoute sur un port libre de 127.0.0.1 et propose
le paquet de l'agent construit à la volée (si dpkg-deb est là). La pile affiche son adresse.

Tout vit dans un dossier temporaire (aucune écriture dans /etc ni dans ~/.config). Les tests
de bout en bout pilotent la pile par signaux, pour simuler les pannes réelles :
    SIGUSR1  « PC éteint » : l'agent s'arrête et plus rien ne répond sur son port (le dock
             attend en vain, comme devant une machine éteinte : état « hors ligne »)
    SIGHUP   « agent arrêté seul » : le port refuse la connexion (PC allumé, agent absent)
    SIGUSR2  tout revient : l'agent est relancé
Le Wake-on-LAN est aiguillé vers 127.0.0.1:9 (ou --wol-target) : aucun paquet ne sort sur ton réseau.
"""

from __future__ import annotations

import argparse
import os
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
from pathlib import Path

from mydock_agent.demo import DEMO_TOKEN

ROOT = Path(__file__).resolve().parent.parent


def free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def build_client_package(directory: Path) -> bool:
    """Construit le paquet de l'agent dans `directory` : ce que le portail propose au PC."""
    result = subprocess.run(
        [sys.executable, str(ROOT / "packaging" / "build_deb.py"), "--target", "agent",
         "--output", str(directory)],
        capture_output=True, check=False,
    )  # fmt: skip
    return result.returncode == 0


def wait_http(url: str, timeout: float = 20.0) -> bool:
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with opener.open(url, timeout=1):
                return True
        except OSError:
            time.sleep(0.1)
    return False


class Blackhole(threading.Thread):
    """Accepte les connexions et ne répond jamais : un PC éteint vu depuis le réseau."""

    def __init__(self, port: int):
        super().__init__(daemon=True)
        self.sock = socket.socket()
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind(("127.0.0.1", port))
        self.sock.listen(16)
        self.sock.settimeout(0.2)
        self.held: list[socket.socket] = []
        self.stopping = False

    def run(self) -> None:
        while not self.stopping:
            try:
                conn, _addr = self.sock.accept()
            except TimeoutError:
                continue
            except OSError:
                break
            self.held.append(conn)

    def close(self) -> None:
        self.stopping = True
        for conn in self.held:
            conn.close()
        self.sock.close()
        self.join(timeout=2)


class Stack:
    def __init__(self, args: argparse.Namespace):
        self.args = args
        self.tmp = Path(tempfile.mkdtemp(prefix="mydock-dev-"))
        self.agent: subprocess.Popen[bytes] | None = None
        self.dock: subprocess.Popen[bytes] | None = None
        self.blackhole: Blackhole | None = None
        self.pending: set[str] = set()
        self.portal_port = args.portal_port or free_port()
        self.clients = Path(args.clients_dir) if args.clients_dir else self.tmp / "clients"

    def start_agent(self) -> None:
        if self.agent and self.agent.poll() is None:
            return
        self.agent = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "mydock_agent",
                "run",
                "--demo",
                "--port",
                str(self.args.agent_port),
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.STDOUT,
        )

    def set_state(self, state: str) -> None:
        """state : « off » (PC éteint), « agent_down » (agent arrêté seul) ou « on »."""
        self.stop_agent()
        if self.blackhole:
            self.blackhole.close()
            self.blackhole = None
        if state == "off":
            self.blackhole = Blackhole(self.args.agent_port)
            self.blackhole.start()
        elif state == "on":
            self.start_agent()

    def stop_agent(self) -> None:
        if self.agent and self.agent.poll() is None:
            self.agent.terminate()
            try:
                self.agent.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.agent.kill()
        self.agent = None

    def start_dock(self) -> None:
        env = {**os.environ, "MYDOCK_DEMO_TOKEN": DEMO_TOKEN}
        config = self.tmp / "dock.toml"
        if not self.args.clients_dir:
            self.clients.mkdir(parents=True, exist_ok=True)
            build_client_package(self.clients)
        command = [
            sys.executable, "-m", "mydock_dock", "init", "--config", str(config),
            "--state-dir", str(self.args.state_dir or self.tmp / "state"),
            "--web-dir", str(self.args.web_dir),
            "--clients-dir", str(self.clients), "--portal-port", str(self.portal_port),
        ]  # fmt: skip
        if not self.args.unpaired:  # un dock neuf n'a ni PC ni jeton : il montre un code
            command += [
                "--pc", "127.0.0.1", "--pc-port", str(self.args.agent_port),
                "--token-env", "MYDOCK_DEMO_TOKEN",
            ]  # fmt: skip
        subprocess.run(command, check=True, env=env, stdout=subprocess.DEVNULL)
        # Le portail écoute le réseau local sur un vrai Pi ; ici, jamais plus loin que cette machine.
        config.write_text(
            config.read_text().replace('listen_host = "0.0.0.0"', 'listen_host = "127.0.0.1"')
        )
        env["MYDOCK_WOL_TARGETS"] = self.args.wol_target
        self.dock = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "mydock_dock",
                "run",
                "--config",
                str(config),
                "--port",
                str(self.args.dock_port),
                "--demo-markets",  # des cotations calculées : aucun accès à Internet
            ],
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.STDOUT,
        )

    def close(self) -> None:
        self.stop_agent()
        if self.blackhole:
            self.blackhole.close()
        if self.dock and self.dock.poll() is None:
            self.dock.terminate()
            try:
                self.dock.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.dock.kill()
        shutil.rmtree(self.tmp, ignore_errors=True)


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawTextHelpFormatter
    )
    parser.add_argument("--dock-port", type=int, default=8080)
    parser.add_argument("--agent-port", type=int, default=8765)
    parser.add_argument(
        "--wol-target", default="127.0.0.1:9", help="où partent les paquets de réveil (hôte:port)"
    )
    parser.add_argument("--web-dir", type=Path, default=ROOT / "dock" / "web" / "dist")
    parser.add_argument(
        "--portal-port", type=int, default=0, help="port du portail (défaut : un port libre)"
    )
    parser.add_argument(
        "--clients-dir",
        type=Path,
        help="paquets proposés au PC (défaut : celui de l'agent, construit)",
    )
    parser.add_argument(
        "--unpaired",
        action="store_true",
        help="dock neuf : aucun PC, un code d'association s'affiche",
    )
    parser.add_argument(
        "--state-dir",
        type=Path,
        help="dossier d'état du dock : réglages, vidéos de l'économiseur (défaut : un dossier temporaire de la pile)",
    )
    args = parser.parse_args()

    stack = Stack(args)
    running = True

    def on_stop(_signum: int, _frame: object) -> None:
        nonlocal running
        running = False

    def queue(state: str):
        def handler(_signum: int, _frame: object) -> None:
            stack.pending.add(state)

        return handler

    signal.signal(signal.SIGINT, on_stop)
    signal.signal(signal.SIGTERM, on_stop)
    signal.signal(signal.SIGUSR1, queue("off"))
    signal.signal(signal.SIGHUP, queue("agent_down"))
    signal.signal(signal.SIGUSR2, queue("on"))

    try:
        stack.start_agent()
        stack.start_dock()
        url = f"http://127.0.0.1:{args.dock_port}"
        if not wait_http(f"{url}/api/health"):
            print("Le serveur dock n'a pas démarré.", file=sys.stderr)
            return 1
        portal = f"http://127.0.0.1:{stack.portal_port}"
        if wait_http(f"{portal}/api/portal", timeout=5.0):
            print(f"MYDOCK_PORTAL_READY {portal}", flush=True)
        print(f"MYDOCK_STACK_READY {url}", flush=True)
        while running:
            while stack.pending:
                stack.set_state(stack.pending.pop())
            if stack.dock and stack.dock.poll() is not None:
                print("Le serveur dock s'est arrêté.", file=sys.stderr)
                return 1
            time.sleep(0.1)
    finally:
        stack.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
