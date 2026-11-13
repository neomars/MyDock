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

    [media]                     # Divertissement : Freetv (chaînes de la Freebox) et Kiosque (partages Samba)
    playlist_url = "http://mafreebox.freebox.fr/freeboxtv/playlist.m3u"
    smb_host = "mafreebox.freebox.fr"
    smb_user = ""               # vide = accès sans identifiant
    smb_password = ""

    [retrogame]                 # Retrogame : des jeux d'arcade MAME sur l'écran du dock
    roms_dir = "/var/lib/mydock/roms"   # vide = le dossier « roms » de l'état du dock
    button_gpio = 17            # broche GPIO (numérotation BCM) du bouton qui quitte le jeu
    upload_without_code = true  # le PC associé envoie des jeux depuis la page /jeux sans code (les autres : le code de l'écran)

Les clés d'API des fournisseurs de bourse et le mot de passe du partage vont dans ce fichier (droits 600),
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
from mydock_dock.freebox import DEFAULT_PLAYLIST_URL, valid_playlist_url
from mydock_dock.validation import ValidationFailure, normalize_mac, validate_host, validate_port

DEFAULT_PORT = 8080
DEFAULT_PORTAL_PORT = 80
DEFAULT_AGENT_PORT = 8765
DEFAULT_STATE_DIR = Path("/var/lib/mydock")
DEFAULT_CLIENTS_DIR = Path("/usr/share/mydock/clients")
DEFAULT_SMB_HOST = "mafreebox.freebox.fr"
DEFAULT_BUTTON_GPIO = 17  # broche physique 11 (la masse est sur la broche 9)
GPIO_PINS = range(2, 28)  # les GPIO 2 à 27 de l'en-tête de 40 broches
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
    playlist_url: str = DEFAULT_PLAYLIST_URL
    smb_host: str = DEFAULT_SMB_HOST
    smb_user: str = ""
    smb_password: str = field(default="", repr=False)  # un secret : jamais dans un affichage
    roms_dir: str = ""  # vide : le dossier « roms » de l'état du dock
    button_gpio: int = DEFAULT_BUTTON_GPIO
    #: Le PC associé (reconnu à son adresse IP) envoie des jeux sans le code de l'écran ; un autre appareil le recopie.
    upload_without_code: bool = True
    demo_markets: bool = False  # option de la ligne de commande, jamais écrite dans le fichier
    demo_retro: bool = False  # idem : de faux jeux, de fausses manettes (essais et captures)
    _raw: dict[str, Any] = field(default_factory=dict, repr=False)

    @property
    def loopback_only(self) -> bool:
        return ipaddress.IPv4Address(self.listen_host).is_loopback

    @property
    def saver_dir(self) -> Path:
        """Le dossier des vidéos de l'économiseur d'écran (voir screensaver.py)."""
        return self.state_dir / "screensaver"

    @property
    def roms_path(self) -> Path:
        """Le dossier des ROMs de Retrogame (voir retrogame.py)."""
        return Path(self.roms_dir) if self.roms_dir else self.state_dir / "roms"

    @property
    def retro_dir(self) -> Path:
        """Ce que le dock garde pour Retrogame : le cache de la liste, les miniatures."""
        return self.state_dir / "retrogame"

    # --- lecture -------------------------------------------------------------------------

    @classmethod
    def from_dict(cls, raw: dict[str, Any], path: Path) -> DockConfig:
        server = _section(raw, "server")
        pc = _section(raw, "pc")
        paths = _section(raw, "paths")
        portal = _section(raw, "portal")
        markets = _section(raw, "markets")
        media = _section(raw, "media")
        retro = _section(raw, "retrogame")
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
        try:
            playlist_url = valid_playlist_url(media.get("playlist_url") or DEFAULT_PLAYLIST_URL)
            raw_smb_host = media.get("smb_host", DEFAULT_SMB_HOST)
            smb_host = validate_host(raw_smb_host) if raw_smb_host else ""
            smb_user = clean_smb_user(media.get("smb_user", ""))
            smb_password = clean_smb_password(media.get("smb_password", ""))
        except (ValueError, ValidationFailure) as exc:
            raise ConfigError(f"[media] : {exc}") from None
        try:
            roms_dir = clean_roms_dir(retro.get("roms_dir", ""))
            button_gpio = clean_gpio(retro.get("button_gpio", DEFAULT_BUTTON_GPIO))
        except ValidationFailure as exc:
            raise ConfigError(f"[retrogame] : {exc}") from None
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
            playlist_url=playlist_url,
            smb_host=smb_host,
            smb_user=smb_user,
            smb_password=smb_password,
            roms_dir=roms_dir,
            button_gpio=button_gpio,
            upload_without_code=bool(retro.get("upload_without_code", True)),
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
        server, pc, paths, portal, media, retro = (
            raw.setdefault(name, {})
            for name in ("server", "pc", "paths", "portal", "media", "retrogame")
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
        media["playlist_url"] = self.playlist_url
        media["smb_host"] = self.smb_host
        media["smb_user"] = self.smb_user
        media["smb_password"] = self.smb_password
        retro["roms_dir"] = self.roms_dir
        retro["button_gpio"] = self.button_gpio
        retro["upload_without_code"] = self.upload_without_code
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

    def set_media(
        self,
        *,
        playlist_url: object = None,
        smb_host: object = None,
        smb_user: object = None,
        smb_password: object = None,
    ) -> None:
        """Divertissement : l'adresse de la liste des chaînes, le serveur de fichiers et son identifiant (tout est
        validé avant d'être gardé ; un champ absent (None) ne change pas)."""
        try:
            url = (
                valid_playlist_url(playlist_url) if playlist_url is not None else self.playlist_url
            )
        except ValueError as exc:
            raise ValidationFailure(str(exc)) from None
        host = self.smb_host
        if smb_host is not None:
            host = validate_host(smb_host) if str(smb_host).strip() else ""
        user = clean_smb_user(smb_user) if smb_user is not None else self.smb_user
        password = (
            clean_smb_password(smb_password) if smb_password is not None else self.smb_password
        )
        self.playlist_url, self.smb_host, self.smb_user, self.smb_password = (
            url,
            host,
            user,
            password,
        )
        self.save()

    def set_retrogame(
        self,
        *,
        roms_dir: object = None,
        button_gpio: object = None,
        upload_without_code: object = None,
    ) -> None:
        """Retrogame : le dossier des ROMs, la broche du bouton, l'envoi sans code du PC associé (validés avant d'être gardés ; None ne change rien)."""
        folder = clean_roms_dir(roms_dir) if roms_dir is not None else self.roms_dir
        pin = clean_gpio(button_gpio) if button_gpio is not None else self.button_gpio
        if upload_without_code is not None and not isinstance(upload_without_code, bool):
            raise ValidationFailure("Le choix « envoi sans code » doit être vrai ou faux.")
        self.roms_dir, self.button_gpio = folder, pin
        if upload_without_code is not None:
            self.upload_without_code = upload_without_code
        self.save()


def clean_roms_dir(value: object) -> str:
    """Le dossier des ROMs : vide (celui du dock) ou un chemin absolu, sans « .. » ni caractère de contrôle."""
    if value is None or value == "":
        return ""
    if not isinstance(value, str):
        raise ValidationFailure("Le dossier des ROMs est invalide.")
    text = value.strip()
    if (
        not text.startswith("/")
        or len(text) > 200
        or any(ord(c) < 32 or ord(c) == 127 for c in text)
        or ".." in Path(text).parts
    ):
        raise ValidationFailure(
            "Le dossier des ROMs doit être un chemin absolu (200 caractères au plus, sans « .. »)."
        )
    return str(Path(text))


def clean_gpio(value: object) -> int:
    """La broche GPIO du bouton : un numéro BCM de l'en-tête (2 à 27)."""
    if isinstance(value, bool) or not isinstance(value, int | str):
        raise ValidationFailure("La broche du bouton est invalide (un numéro GPIO de 2 à 27).")
    try:
        pin = int(value)
    except ValueError:
        raise ValidationFailure(
            "La broche du bouton est invalide (un numéro GPIO de 2 à 27)."
        ) from None
    if pin not in GPIO_PINS:
        raise ValidationFailure("La broche du bouton doit être un numéro GPIO de 2 à 27.")
    return pin


def clean_smb_user(value: object) -> str:
    """L'identifiant du partage : vide (accès libre) ou un nom sans « % » (séparateur du mot de passe) ni caractère de contrôle."""
    if value is None or value == "":
        return ""
    if (
        not isinstance(value, str)
        or len(value.strip()) > 64
        or "%" in value
        or any(ord(c) < 32 or ord(c) == 127 for c in value)
    ):
        raise ValidationFailure(
            "L'identifiant du partage est invalide (64 caractères au plus, sans « % »)."
        )
    return value.strip()


def clean_smb_password(value: object) -> str:
    if value is None or value == "":
        return ""
    if (
        not isinstance(value, str)
        or len(value) > 128
        or any(ord(c) < 32 or ord(c) == 127 for c in value)
    ):
        raise ValidationFailure("Le mot de passe du partage est invalide (128 caractères au plus).")
    return value


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
    playlist_url: str | None = None,
    smb_host: str | None = None,
    roms_dir: str | None = None,
    button_gpio: int | None = None,
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
    if playlist_url is not None or smb_host is not None:
        try:
            config.set_media(playlist_url=playlist_url, smb_host=smb_host)
        except ValidationFailure as exc:
            raise ConfigError(f"Divertissement : {exc}") from None
    if roms_dir is not None or button_gpio is not None:
        try:
            config.set_retrogame(roms_dir=roms_dir, button_gpio=button_gpio)
        except ValidationFailure as exc:
            raise ConfigError(f"Retrogame : {exc}") from None
    if config.portal_enabled and config.portal_port == config.port:
        raise ConfigError("Le port du portail doit différer de celui de l'interface.")
    config.save()
    return config
