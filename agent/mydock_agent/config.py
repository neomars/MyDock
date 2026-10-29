"""Configuration de l'agent : ~/.config/mydock/agent.toml (droits 600).

Exemple :

    token = "…43 caractères générés à l'installation…"
    bind_host = "0.0.0.0"
    port = 8765
    allowed_clients = ["192.168.1.50"]   # idéalement l'IP du Pi
"""

from __future__ import annotations

import ipaddress
import os
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from mydock_common.auth import generate_token, is_valid_token_format
from mydock_common.config import ConfigError, load_toml, write_toml_private

DEFAULT_PORT = 8765
DEFAULT_BIND = "0.0.0.0"
LOG_LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR")

HEADER = (
    "MyDock - agent (PC).\n"
    "Fichier privé (droits 600) : il contient le jeton, ne le publie jamais.\n"
    "allowed_clients : adresses IPv4 ou réseaux autorisés à se connecter, idéalement l'IP du Pi.\n"
    "Vide = seulement les réseaux locaux de ce PC."
)


def default_config_path() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / "mydock" / "agent.toml"


@dataclass(frozen=True)
class AgentConfig:
    token: str
    bind_host: str = DEFAULT_BIND
    port: int = DEFAULT_PORT
    allowed_clients: tuple[ipaddress.IPv4Network, ...] = ()
    log_level: str = "INFO"
    path: Path | None = None


def parse_networks(values: Iterable[str]) -> tuple[ipaddress.IPv4Network, ...]:
    networks: list[ipaddress.IPv4Network] = []
    for value in values:
        try:
            network = ipaddress.ip_network(str(value).strip(), strict=False)
        except ValueError:
            raise ConfigError(
                f"allowed_clients : « {value} » n'est pas une adresse ou un réseau valide "
                f"(exemples : 192.168.1.50 ou 192.168.1.0/24)."
            ) from None
        if not isinstance(network, ipaddress.IPv4Network):
            raise ConfigError(
                f"allowed_clients : « {value} » est en IPv6 ; l'agent n'écoute qu'en IPv4."
            )
        networks.append(network)
    return tuple(networks)


def _validate_bind(value: Any) -> str:
    try:
        return str(ipaddress.IPv4Address(str(value)))
    except ValueError:
        raise ConfigError(
            f"bind_host : « {value} » n'est pas une adresse IPv4 (exemple : 0.0.0.0)."
        ) from None


def _validate_port(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 65535:
        raise ConfigError(f"port : « {value} » n'est pas un port valide (1 à 65535).")
    return value


def config_from_dict(raw: dict[str, Any], path: Path | None = None) -> AgentConfig:
    token = raw.get("token")
    if not is_valid_token_format(token):
        raise ConfigError(
            "token : absent ou invalide (24 à 256 caractères parmi lettres, chiffres et . _ ~ -). "
            "Relance l'installation pour en générer un."
        )
    allowed = raw.get("allowed_clients", [])
    if not isinstance(allowed, list):
        raise ConfigError("allowed_clients doit être une liste de chaînes.")
    level = str(raw.get("log_level", "INFO")).upper()
    if level not in LOG_LEVELS:
        raise ConfigError(f"log_level : « {level} » inconnu ({', '.join(LOG_LEVELS)}).")
    return AgentConfig(
        token=token,
        bind_host=_validate_bind(raw.get("bind_host", DEFAULT_BIND)),
        port=_validate_port(raw.get("port", DEFAULT_PORT)),
        allowed_clients=parse_networks(allowed),
        log_level=level,
        path=path,
    )


def load_config(path: Path | str | None = None) -> AgentConfig:
    path = Path(path) if path else default_config_path()
    return config_from_dict(load_toml(path), path)


def init_config(
    path: Path | str | None = None,
    *,
    allow: Iterable[str] = (),
    port: int | None = None,
    bind: str | None = None,
    rotate_token: bool = False,
) -> tuple[AgentConfig, bool]:
    """Crée ou met à jour le fichier de configuration (idempotent).

    Le jeton existant est conservé, sauf si `rotate_token`. Retourne la configuration et
    un booléen : vrai si un nouveau jeton a été généré.
    """
    path = Path(path) if path else default_config_path()
    raw: dict[str, Any] = load_toml(path) if path.exists() else {}
    token_created = rotate_token or not is_valid_token_format(raw.get("token"))
    if token_created:
        raw["token"] = generate_token()
    raw.setdefault("bind_host", DEFAULT_BIND)
    raw.setdefault("port", DEFAULT_PORT)
    raw.setdefault("allowed_clients", [])
    if bind is not None:
        raw["bind_host"] = bind
    if port is not None:
        raw["port"] = port
    allow = [a.strip() for a in allow if a and a.strip()]
    if allow:
        raw["allowed_clients"] = [str(n) for n in parse_networks(allow)]
    config = config_from_dict(raw, path)  # valide avant d'écrire
    write_toml_private(path, raw, header=HEADER)
    return config, token_created
