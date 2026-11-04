"""`mydock-agent setup` : première configuration en une commande, depuis le terminal de l'utilisateur.

Le paquet .deb installe le programme et le service, mais il ne peut pas deviner pour quel
utilisateur ni pour quel Pi : cette commande crée la configuration (jeton en droits 600), démarre
le service, s'occupe du pare-feu, puis **associe ce PC au dock** avec le code affiché sur l'écran du
Pi : le dock se configure tout seul, il n'y a rien à recopier (voir pairing.py). Si l'association
n'est pas possible, elle dit quoi saisir sur le Pi à la main.
"""

from __future__ import annotations

import asyncio
import ipaddress
import os
import shutil
import socket
import subprocess
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from mydock_agent import firewall
from mydock_agent.collectors.host import list_interfaces
from mydock_agent.config import AgentConfig, default_config_path, init_config
from mydock_agent.doctor import _handshake, curl_check
from mydock_agent.pairing import (
    PORTAL_PORT,
    PairingError,
    fetch_state,
    pair_with_dock,
    portal_url,
)
from mydock_common.config import ConfigError

UNIT = "mydock-agent.service"


class SetupError(Exception):
    """Une étape ne peut pas continuer ; le message dit quoi faire."""


class Systemd:
    """Appels à `systemctl --user`. Remplacé dans les tests."""

    def run(self, *args: str, timeout: float = 30.0) -> subprocess.CompletedProcess[str] | None:
        """Résultat de la commande, ou None si systemd utilisateur est injoignable d'ici."""
        if not shutil.which("systemctl"):
            return None
        try:
            return subprocess.run(
                ["systemctl", "--user", *args],
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            return None

    def reachable(self) -> bool:
        result = self.run("show-environment")
        return result is not None and result.returncode == 0


@dataclass(frozen=True)
class PcAddress:
    interface: str
    address: str
    default_route: bool


# --- ancienne installation (script deploy/pc/install-agent.sh, environnement Python dédié) -----


def looks_like_legacy_unit(text: str) -> bool:
    """Unité écrite par l'ancien installateur : elle lance l'agent depuis un venv dans le home."""
    exec_lines = [line for line in text.splitlines() if line.startswith("ExecStart=")]
    return bool(exec_lines) and all("/usr/bin/mydock-agent" not in line for line in exec_lines)


def migrate_legacy_unit(systemd: Systemd, config_home: Path) -> bool:
    """Retire l'unité utilisateur de l'ancien installateur : elle masquerait celle du paquet."""
    unit = config_home / "systemd" / "user" / UNIT
    try:
        text = unit.read_text()
    except OSError:
        return False
    if "mydock" not in text.lower() or not looks_like_legacy_unit(text):
        return False
    systemd.run("disable", "--now", UNIT)  # libère le port avant de relancer
    unit.unlink(missing_ok=True)
    systemd.run("daemon-reload")
    return True


# --- adresses ---------------------------------------------------------------------------------


def pc_addresses() -> list[PcAddress]:
    """Adresses IPv4 de ce PC sur ses réseaux actifs, la route par défaut en premier."""
    found = [
        PcAddress(itf["name"], address, bool(itf["default"]))
        for itf in list_interfaces()
        if itf["up"]
        for address in itf["ipv4"]
    ]
    return sorted(found, key=lambda a: not a.default_route)


def resolve_pi(value: str) -> str:
    """Adresse ou réseau IPv4 tels quels ; un nom est résolu en adresse (le pare-feu veut une IP)."""
    try:
        ipaddress.ip_network(value, strict=False)
        return value
    except ValueError:
        pass
    try:
        infos = socket.getaddrinfo(value, None, family=socket.AF_INET, type=socket.SOCK_STREAM)
    except OSError:
        raise SetupError(
            f"Impossible de résoudre « {value} » (appareil éteint ou hors réseau ?). "
            "Donne l'adresse IP du Pi à la place."
        ) from None
    return str(infos[0][4][0])


# --- l'étape elle-même ------------------------------------------------------------------------


def wait_for_agent(config: AgentConfig, timeout: float = 12.0) -> bool:
    host = "127.0.0.1" if config.bind_host in ("0.0.0.0", "") else config.bind_host
    deadline = time.monotonic() + timeout
    while True:
        if asyncio.run(_handshake(host, config.port, config.token)) == "ok":
            return True
        if time.monotonic() >= deadline:
            return False
        time.sleep(0.5)


def run_setup(
    *,
    config_path: Path | None = None,
    pi: list[str] | None = None,
    port: int | None = None,
    bind: str | None = None,
    rotate_token: bool = False,
    open_firewall: bool = False,
    start_service: bool = True,
    code: str | None = None,
    portal_port: int = PORTAL_PORT,
    pair: bool = True,
    systemd: Systemd | None = None,
    out: Callable[[str], None] = print,
    is_root: bool | None = None,
    wait: Callable[[AgentConfig], bool] = wait_for_agent,
    fetch: Callable[..., dict | None] = fetch_state,
    pair_with: Callable[..., object] = pair_with_dock,
    ask: Callable[[str], str] = input,
    interactive: bool | None = None,
) -> int:
    systemd = systemd or Systemd()
    path = config_path or default_config_path()
    if (os.geteuid() == 0) if is_root is None else is_root:
        out(
            "Lance cette commande avec ton utilisateur, sans sudo : l'agent vit dans ta session "
            "graphique et sa configuration est dans ton dossier personnel."
        )
        return 2

    try:
        allow = [resolve_pi(value) for value in pi or []]
        config, created = init_config(
            path, allow=allow, port=port, bind=bind, rotate_token=rotate_token
        )
    except (ConfigError, SetupError) as exc:
        out(f"Erreur : {exc}")
        return 2

    out("MyDock - configuration de l'agent\n")
    out(f"✔ Configuration : {path} (droits 600)")
    out("✔ Jeton : " + ("nouveau jeton généré" if created else "jeton existant conservé"))
    if config.allowed_clients:
        out("✔ Clients autorisés : " + ", ".join(str(n) for n in config.allowed_clients))
    else:
        out(
            "⚠ Aucun Pi donné : seuls les réseaux locaux de ce PC sont acceptés. Pour n'autoriser "
            "que le Pi : mydock-agent setup --pi ADRESSE_IP_DU_PI"
        )

    failed = False
    if start_service:
        failed = not _start_service(systemd, config, path.parent.parent, wait, out)

    _firewall_step(config, open_firewall, out)
    paired = False
    pi_host = next((value for value in allow if "/" not in value), None)
    if pair and pi_host is not None:
        if failed or not start_service:
            out("")
            out(
                "· Association au dock ignorée : l'agent ne tourne pas encore. Relance cette commande."
            )
        else:
            use_ask = (sys.stdin.isatty() if interactive is None else interactive) and code is None
            paired = _pair_step(
                config,
                path,
                pi_host,
                code=code,
                portal_port=portal_port,
                systemd=systemd,
                wait=wait,
                out=out,
                fetch=fetch,
                pair_with=pair_with,
                ask=ask if use_ask else None,
            )
    elif pair and allow:
        out("")
        out(
            "· Un réseau est autorisé, pas une adresse : donne l'adresse du Pi pour associer le dock."
        )
    _pi_instructions(config, created, paired, out)
    return 1 if failed else 0


def _start_service(
    systemd: Systemd,
    config: AgentConfig,
    config_home: Path,
    wait: Callable[[AgentConfig], bool],
    out: Callable[[str], None],
) -> bool:
    out("")
    if not systemd.reachable():
        out(
            "⚠ systemd utilisateur est injoignable depuis ce terminal. Ouvre un terminal dans ta "
            "session graphique (pas en SSH) et relance cette commande ; en attendant, "
            "« mydock-agent run » démarre l'agent à la main."
        )
        return False
    if migrate_legacy_unit(systemd, config_home):
        out(
            "✔ Ancienne installation (environnement Python dans ton dossier personnel) remplacée "
            "par celle du paquet. Tu peux supprimer ~/.local/share/mydock si elle existe."
        )
    shown = systemd.run("cat", UNIT)
    if shown is None or shown.returncode != 0:
        out(
            "⚠ Le service systemd mydock-agent est introuvable : installe le paquet .deb "
            "(sudo apt install ./mydock-agent_*.deb). En attendant : mydock-agent run"
        )
        return False
    restarted = systemd.run("restart", UNIT)
    if restarted is None or restarted.returncode != 0:
        detail = (restarted.stderr.strip() if restarted else "") or "échec du démarrage"
        out(f"✘ Le service n'a pas démarré : {detail}")
        out("  Journal : journalctl --user -u mydock-agent -e")
        return False
    if wait(config):
        out(f"✔ Service démarré : l'agent répond sur le port {config.port} et accepte le jeton")
        return True
    out("⚠ Le service est lancé mais l'agent ne répond pas encore.")
    out("  Journal : journalctl --user -u mydock-agent -e")
    return False


def _firewall_step(config: AgentConfig, open_firewall: bool, out: Callable[[str], None]) -> None:
    out("")
    state = firewall.ufw_state()
    if state is not True:
        out(
            "✔ Pare-feu : ufw n'est pas actif, rien à ouvrir."
            if state is False
            else "· Pare-feu : ufw absent."
        )
        return
    commands = [firewall.ufw_allow_command(s, config.port) for s in firewall.sources(config)]
    if not open_firewall:
        out("⚠ Pare-feu : ufw est actif. Autorise le Pi (ou relance avec --open-firewall) :")
        for command in commands:
            out(f"    {command}")
        return
    for source in firewall.sources(config):
        result = subprocess.run(
            ["sudo", *firewall.ufw_allow_args(source, config.port)], check=False
        )  # interactif : sudo peut demander le mot de passe
        mark = "✔" if result.returncode == 0 else "✘"
        out(f"{mark} ufw : {source} → port {config.port}")


def _authorize_pi(config: AgentConfig, path: Path, addresses: list[str]) -> AgentConfig | None:
    """Ajoute à allowed_clients les adresses sous lesquelles le Pi se présentera ; None si rien à changer.

    Le dock les annonce (il sait quelle interface il utilise pour joindre ce PC) : c'est ce qui
    rattrape un Pi branché en Ethernet et en Wi-Fi quand on a donné l'autre adresse. Seules des
    adresses privées sont retenues, trois au plus.
    """
    if not config.allowed_clients:
        return None  # « réseaux locaux de ce PC » : toute adresse locale passe déjà
    valid: list[ipaddress.IPv4Address] = []
    for value in addresses:
        try:
            address = ipaddress.IPv4Address(value)
        except ValueError:
            continue
        if address.is_private and not address.is_unspecified and address not in valid:
            valid.append(address)
    missing = [
        str(address)
        for address in valid[:3]
        if not any(address in network for network in config.allowed_clients)
    ]
    if not missing:
        return None
    allow = [str(network) for network in config.allowed_clients] + missing
    updated, _created = init_config(path, allow=allow)
    return updated


def _restart(
    systemd: Systemd, config: AgentConfig, wait: Callable[[AgentConfig], bool], out
) -> None:
    result = systemd.run("restart", UNIT)
    if result is None or result.returncode != 0 or not wait(config):
        out("⚠ L'agent n'a pas redémarré comme prévu : journalctl --user -u mydock-agent -e")


def _pair_step(
    config: AgentConfig,
    path: Path,
    pi_host: str,
    *,
    code: str | None,
    portal_port: int,
    systemd: Systemd,
    wait: Callable[[AgentConfig], bool],
    out: Callable[[str], None],
    fetch: Callable[..., dict | None],
    pair_with: Callable[..., object],
    ask: Callable[[str], str] | None,
) -> bool:
    """Présente le code au dock. Vrai si ce PC est maintenant associé."""
    out("")
    state = fetch(pi_host, portal_port)
    if state is None:
        out(
            f"⚠ Le portail du dock ne répond pas ({portal_url(pi_host, portal_port)}) : "
            "association impossible pour l'instant. Vérifie l'adresse du Pi (et --portal-port si "
            "le dock n'utilise pas le port 80), puis relance cette commande."
        )
        return False
    out(f"✔ Dock trouvé : {state.get('hostname', pi_host)} (version {state.get('version', '?')})")
    if not state.get("pairing"):
        out(
            "· Aucun code n'est affiché sur le dock. S'il a déjà un PC : Réglages → « Associer un "
            "PC » sur son écran."
        )
    wrong = 0
    corrected = False
    current = code
    while True:
        if current is None:
            if ask is None:
                out(
                    "· Pour associer ce PC : relance avec le code de l'écran du dock : "
                    f"mydock-agent setup --pi {pi_host} --code CODE"
                )
                return False
            current = ask("Code affiché sur l'écran du dock (vide pour passer) : ").strip()
            if not current:
                out("· Association ignorée (relance avec --code quand tu as le code).")
                return False
        try:
            result = pair_with(
                pi_host,
                portal_port,
                code=current,
                token=config.token,
                agent_port=config.port,
                name=socket.gethostname(),
            )
        except PairingError as exc:
            if exc.code == "bad_code":
                wrong += 1
                out("✘ Code incorrect.")
                if ask is None or wrong >= 3:
                    return False
                current = None
                continue
            if exc.code in ("unreachable", "refused") and not corrected:
                updated = _authorize_pi(config, path, exc.pi_ips)
                if updated is not None:
                    corrected = True
                    config = updated
                    out(
                        "· Le dock se présente sous l'adresse " + ", ".join(exc.pi_ips) + " : "
                        "elle est ajoutée aux clients autorisés."
                    )
                    _restart(systemd, config, wait, out)
                    continue
            out(f"✘ {exc.message}")
            if exc.code in ("unreachable", "refused"):
                source = ", ".join(exc.pi_ips) or "ADRESSE_DU_PI"
                out(
                    "  Le dock doit pouvoir joindre ce PC sur le port "
                    f"{config.port} : sudo ufw allow from {source} to any port {config.port} proto tcp "
                    "(ou : mydock-agent setup --pi … --open-firewall)."
                )
            return False
        updated = _authorize_pi(config, path, list(getattr(result, "pi_ips", [])))
        if updated is not None:
            config = updated
            _restart(systemd, config, wait, out)
        hostname = getattr(result, "dock_hostname", "") or pi_host
        out(
            f"✔ PC associé au dock « {hostname} » : il se connecte à ce PC tout seul, "
            "rien à saisir sur le Pi."
        )
        return True


def _pi_instructions(
    config: AgentConfig, created: bool, paired: bool, out: Callable[[str], None]
) -> None:
    if paired:
        out("")
        out("Vérifier : mydock-agent doctor")
        return
    addresses = pc_addresses()
    out("")
    if not addresses:
        out("⚠ Ce PC n'a aucune adresse IPv4 sur un réseau actif : branche-le puis relance.")
        return
    main = addresses[0].address
    out("À saisir à la main sur le dock (Réglages → PC piloté) :")
    out(f"    Adresse : {main}        (l'adresse IP, pas un nom .local : voir ci-dessous)")
    out(f"    Port    : {config.port}")
    if created:
        out("    Jeton   : affiché ci-dessous, une seule fois")
    else:
        out("    Jeton   : mydock-agent show-token")
    for other in addresses[1:]:
        out(f"    (autre adresse de ce PC : {other.address}, {other.interface})")
    out("")
    out("Vérifier la liaison depuis le Pi (404 = l'agent est joint) :")
    out(f"    {curl_check(main, config.port)}")
    out("")
    out(
        "Préfère l'adresse IP au nom « <nom>.local » : la résolution mDNS échoue souvent depuis "
        "un Pi. Réserve cette adresse dans ta box (bail DHCP) pour qu'elle ne change pas."
    )
    if created:
        out("")
        out("Jeton (ne le publie jamais) :")
        out(f"    {config.token}")
