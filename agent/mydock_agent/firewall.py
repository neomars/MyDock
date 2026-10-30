"""Pare-feu de ce PC : détection (ufw, firewalld) et commandes à donner à l'utilisateur.

Un pare-feu actif est la première cause d'un PC « hors ligne » alors que l'agent tourne. On ne
modifie rien ici : on lit ce qui est lisible sans droits particuliers et on prépare les commandes.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from mydock_agent.access import local_lan_networks
from mydock_agent.config import AgentConfig

UFW_CONF = Path("/etc/ufw/ufw.conf")
UFW_COMMENT = "MyDock agent"


def ufw_state(conf: Path = UFW_CONF) -> bool | None:
    """True : ufw est actif ; False : installé mais inactif ; None : absent ou illisible."""
    try:
        lines = conf.read_text().splitlines()
    except OSError:
        return None
    return any(line.replace(" ", "").lower() == "enabled=yes" for line in lines)


def service_active(unit: str) -> bool:
    if not shutil.which("systemctl"):
        return False
    result = subprocess.run(
        ["systemctl", "is-active", unit], capture_output=True, text=True, check=False
    )
    return result.stdout.strip() == "active"


def sources(config: AgentConfig) -> list[str]:
    """Qui autoriser dans le pare-feu : l'adresse du Pi, sinon les réseaux locaux de ce PC."""
    networks = config.allowed_clients or tuple(local_lan_networks())
    return [str(n.network_address) if n.prefixlen == 32 else str(n) for n in networks]


def ufw_allow_args(source: str, port: int) -> list[str]:
    """Arguments de `sudo ufw …` qui laissent `source` joindre l'agent."""
    return [
        "ufw",
        "allow",
        "from",
        source,
        "to",
        "any",
        "port",
        str(port),
        "proto",
        "tcp",
        "comment",
        UFW_COMMENT,
    ]


def ufw_allow_command(source: str, port: int) -> str:
    """La même commande, en texte à copier (sans le commentaire, inutile à la main)."""
    return f"sudo ufw allow from {source} to any port {port} proto tcp"
