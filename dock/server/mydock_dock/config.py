"""Configuration du serveur dock : /etc/mydock/dock.toml (droits 600).

    [server]
    listen_host = "127.0.0.1"   # le dock n'écoute qu'en local : seul le Chromium du Pi s'y connecte
    port = 8080

    [pc]
    host = "ubuntu.local"       # nom mDNS ou adresse IPv4 du PC
    port = 8765
    token = "…"                 # le jeton de l'agent
    wol_mac = ""                # vide = adresse apprise automatiquement

    [portal]                    # la page web du Pi sur le réseau local : le PC y télécharge son paquet
    enabled = true              # et s'associe au dock (jamais l'interface de pilotage)
    listen_host = "0.0.0.0"
    port = 80
    clients_dir = "/usr/share/mydock/clients"

    [paths]
    state_dir = "/var/lib/mydock"
    web_dir = "/usr/share/mydock/web"

    [markets]
    finnhub_key = ""            # facultatif : clé gratuite finnhub.io, cotation de secours des actions

Les clés d'API des fournisseurs de bourse vont dans la section [markets] de ce fichier (droits 600),
jamais dans le dépôt, jamais dans un journal, jamais à l'écran. Yahoo Finance et Binance n'en
demandent pas.
"""

from __future__ import annotations

import copy
import ipaddress
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from mydock_common.auth import is_valid_token_format
from mydock_common.config import ConfigError, load_toml, write_toml_private
from mydock_dock.validation import ValidationFailure, normalize_mac, validate_host, validate_port

DEFAULT_PORT = 8080
DEFAULT_PORTAL_PORT = 80
DEFAULT_AGENT_PORT = 8765
DEFAULT_STATE_DIR = Path("/var/lib/mydock")
DEFAULT_CLIENTS_DIR = Path("/usr/share/mydock/clients")
LOG_LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR")
API_KEY_RE = re.compile(r"^[A-Za-z0-9_.~-]{8,128}$")

HEADER = (
    "MyDock - serveur dock (Raspberry Pi).\n"
    "Fichier privé (droits 600) : il contient le jeton de l'agent et, si tu en ajoutes, des clés d'API.\n"
    "Ne le publie jamais."
)


def default_config_path() -> Path:
    override = os.environ.get("MYDOCK_CONFIG")
    return Path(override) if override else Path("/etc/mydock/dock.toml")


@dataclass
class DockConfig:
    path: Path
    listen_host: str = "127.0.0.1"
    port: int = DEFAULT_PORT
    allow_remote: bool = False
    log_level: str = "INFO"
    agent_host: str = ""
    agent_port: int = DEFAULT_AGENT_PORT
    token: str = ""
    wol_mac: str = ""
    portal_enabled: bool = True
    portal_host: str = "0.0.0.0"
    portal_port: int = DEFAULT_PORTAL_PORT
    clients_dir: Path = DEFAULT_CLIENTS_DIR
    state_dir: Path = DEFAULT_STATE_DIR
    web_dir: Path | None = None
    finnhub_key: str = field(default="", repr=False)  # un secret : jamais dans un affichage
    demo_markets: bool = False  # option de la ligne de commande, jamais écrite dans le fichier
    _raw: dict[str, Any] = field(default_factory=dict, repr=False)

    @property
    def loopback_only(self) -> bool:
        return ipaddress.IPv4Address(self.listen_host).is_loopback

    @property
    def saver_dir(self) -> Path:
        """Le dossier des vidéos de l'économiseur d'écran (voir screensaver.py)."""
        return self.state_dir / "screensaver"

    # --- lecture -------------------------------------------------------------------------

    @classmethod
    def from_dict(cls, raw: dict[str, Any], path: Path) -> DockConfig:
        server = _section(raw, "server")
        pc = _section(raw, "pc")
        paths = _section(raw, "paths")
        portal = _section(raw, "portal")
        markets = _section(raw, "markets")
        try:
            listen_host = str(ipaddress.IPv4Address(str(server.get("listen_host", "127.0.0.1"))))
            port = validate_port(server.get("port", DEFAULT_PORT), "server.port")
            agent_port = validate_port(pc.get("port", DEFAULT_AGENT_PORT), "pc.port")
            agent_host = validate_host(pc["host"]) if pc.get("host") else ""
            wol_mac = normalize_mac(pc["wol_mac"]) if pc.get("wol_mac") else ""
            portal_host = str(ipaddress.IPv4Address(str(portal.get("listen_host", "0.0.0.0"))))
            portal_port = validate_port(portal.get("port", DEFAULT_PORTAL_PORT), "portal.port")
        except ipaddress.AddressValueError:
            raise ConfigError(
                "server.listen_host et portal.listen_host doivent être des adresses IPv4."
            ) from None
        except ValidationFailure as exc:
            raise ConfigError(f"Configuration invalide : {exc}") from None
        token = pc.get("token", "")
        if token and not is_valid_token_format(token):
            raise ConfigError(
                "pc.token est invalide (24 à 256 caractères : lettres, chiffres, . _ ~ -)."
            )
        finnhub_key = str(markets.get("finnhub_key", "") or "").strip()
        if finnhub_key and not API_KEY_RE.fullmatch(finnhub_key):
            raise ConfigError(
                "markets.finnhub_key est invalide (8 à 128 caractères : lettres, chiffres, . _ ~ -)."
            )
        level = str(server.get("log_level", "INFO")).upper()
        if level not in LOG_LEVELS:
            raise ConfigError(f"server.log_level : « {level} » inconnu ({', '.join(LOG_LEVELS)}).")
        config = cls(
            path=path,
            listen_host=listen_host,
            port=port,
            allow_remote=bool(server.get("allow_remote", False)),
            log_level=level,
            agent_host=agent_host,
            agent_port=agent_port,
            token=token,
            wol_mac=wol_mac,
            portal_enabled=bool(portal.get("enabled", True)),
            portal_host=portal_host,
            portal_port=portal_port,
            clients_dir=Path(portal.get("clients_dir", DEFAULT_CLIENTS_DIR)),
            state_dir=Path(paths.get("state_dir", DEFAULT_STATE_DIR)),
            web_dir=Path(paths["web_dir"]) if paths.get("web_dir") else None,
            finnhub_key=finnhub_key,
            _raw=copy.deepcopy(raw),
        )
        if not config.loopback_only and not config.allow_remote:
            raise ConfigError(
                f"server.listen_host = {listen_host} exposerait le dock sur le réseau, sans "
                "authentification. Garde 127.0.0.1 (seul le navigateur du Pi s'y connecte)."
            )
        if config.portal_enabled and config.portal_port == config.port:
            raise ConfigError(
                f"portal.port = {config.portal_port} est aussi le port de l'interface "
                "(server.port) : choisis deux ports différents."
            )
        return config

    @classmethod
    def load(cls, path: Path | str | None = None) -> DockConfig:
        path = Path(path) if path else default_config_path()
        return cls.from_dict(load_toml(path), path)

    # --- écriture ------------------------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        raw = copy.deepcopy(self._raw)
        server, pc, paths, portal = (
            raw.setdefault(name, {}) for name in ("server", "pc", "paths", "portal")
        )
        server["listen_host"] = self.listen_host
        server["port"] = self.port
        portal["enabled"] = self.portal_enabled
        portal["listen_host"] = self.portal_host
        portal["port"] = self.portal_port
        portal["clients_dir"] = str(self.clients_dir)
        pc["host"] = self.agent_host
        pc["port"] = self.agent_port
        pc["token"] = self.token
        pc["wol_mac"] = self.wol_mac
        paths["state_dir"] = str(self.state_dir)
        if self.web_dir is not None:
            paths["web_dir"] = str(self.web_dir)
        return raw

    def save(self) -> None:
        write_toml_private(self.path, self.to_dict(), header=HEADER)
        self._raw = self.to_dict()

    # --- modifications validées (utilisées par l'écran Réglages) ----------------------------

    def set_pc(self, host: object, port: object) -> None:
        # Tout valider avant de modifier quoi que ce soit : un refus ne doit rien laisser derrière.
        valid_host = validate_host(host)
        valid_port = validate_port(port, "Le port de l'agent")
        self.agent_host, self.agent_port = valid_host, valid_port
        self.save()

    def set_pairing(self, host: object, port: object, token: object) -> None:
        """Adresse, port et jeton d'un PC qui vient de s'associer : validés, puis écrits en une fois."""
        valid_host = validate_host(host)
        valid_port = validate_port(port, "Le port de l'agent")
        if not is_valid_token_format(token):
            raise ValidationFailure("Jeton invalide.")
        self.agent_host, self.agent_port, self.token = valid_host, valid_port, str(token)
        self.save()

    def set_token(self, token: object) -> None:
        if not is_valid_token_format(token):
            raise ValidationFailure(
                "Jeton invalide : 24 à 256 caractères parmi lettres, chiffres, point, tiret, "
                "souligné et tilde."
            )
        self.token = str(token)
        self.save()

    def set_wol_mac(self, mac: object) -> None:
        self.wol_mac = normalize_mac(mac) if mac else ""
        self.save()


def _section(raw: dict[str, Any], name: str) -> dict[str, Any]:
    value = raw.get(name, {})
    if not isinstance(value, dict):
        raise ConfigError(f"[{name}] doit être une section TOML.")
    return value


def init_config(
    path: Path | str | None = None,
    *,
    pc_host: str | None = None,
    pc_port: int | None = None,
    token: str | None = None,
    port: int | None = None,
    portal_port: int | None = None,
    portal_enabled: bool | None = None,
    clients_dir: Path | str | None = None,
    state_dir: Path | str | None = None,
    web_dir: Path | str | None = None,
) -> DockConfig:
    """Crée ou met à jour la configuration (idempotent) ; ne change que ce qui est fourni."""
    path = Path(path) if path else default_config_path()
    raw = load_toml(path) if path.exists() else {}
    config = DockConfig.from_dict(raw, path)
    if pc_host is not None:
        config.agent_host = validate_host(pc_host)
    if pc_port is not None:
        config.agent_port = validate_port(pc_port, "Le port de l'agent")
    if token is not None:
        if not is_valid_token_format(token):
            raise ConfigError("Jeton invalide (24 à 256 caractères : lettres, chiffres, . _ ~ -).")
        config.token = token
    if port is not None:
        config.port = validate_port(port, "Le port du dock")
    if portal_port is not None:
        config.portal_port = validate_port(portal_port, "Le port du portail")
    if portal_enabled is not None:
        config.portal_enabled = portal_enabled
    if clients_dir is not None:
        config.clients_dir = Path(clients_dir)
    if state_dir is not None:
        config.state_dir = Path(state_dir)
    if web_dir is not None:
        config.web_dir = Path(web_dir)
    if config.portal_enabled and config.portal_port == config.port:
        raise ConfigError("Le port du portail doit différer de celui de l'interface.")
    config.save()
    return config
