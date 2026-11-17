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

    [media]                     # Divertissement : le Kiosque (partages Samba)
    smb_host = "mafreebox.freebox.fr"
    smb_user = ""               # vide = accès sans identifiant
    smb_password = ""
    pc_audio = true             # le son des vidéos, de la TV et des jeux sort sur le PC (le Pi n'a pas d'enceintes)
    audio_advance_ms = 150      # de combien le son des vidéos est avancé, pour rattraper le temps du trajet jusqu'au PC

    [freetv]                    # Free TV : tv.free.fr dans une fenêtre de Chromium sur l'écran du dock
    user_agent = "auto"         # "auto" : un Chromium de bureau x86_64 de la même version ; "" : l'identité d'origine de Chromium
    scale = 1.5                 # la taille de la page (1 = taille normale ; 1,5 ≈ 2 × plus gros qu'avant la 1.9.1) : se règle aussi dans Réglages
    corner = "top-left"         # le coin où l'appui long (1,5 s) ramène le dock : top-left, top-right, bottom-left, bottom-right
    fullscreen = true           # false : fenêtre maximisée (avec sa barre de titre), si le clavier à l'écran reste caché derrière Free TV

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
import logging
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
DEFAULT_SMB_HOST = "mafreebox.freebox.fr"
DEFAULT_FREETV_USER_AGENT = "auto"  # voir freetv_playback.compute_user_agent
DEFAULT_FREETV_SCALE = (
    1.5  # un écran de 7 pouces en 1024 × 600 : 1 rend le texte de Free TV trop petit pour être lu
)
LEGACY_FREETV_SCALE = 0.7  # l'ancien réglage par défaut (1.9.0), écrit dans dock.toml à la première sauvegarde : remplacé
DEFAULT_FREETV_CORNER = "top-left"
FREETV_CORNERS = ("top-left", "top-right", "bottom-left", "bottom-right")
MIN_FREETV_SCALE, MAX_FREETV_SCALE = 0.25, 3.0
FREETV_SCALE_STEP = 0.25
MAX_USER_AGENT_LENGTH = 300
DEFAULT_BUTTON_GPIO = 17  # broche physique 11 (la masse est sur la broche 9)
DEFAULT_AUDIO_ADVANCE_MS = 150  # le trajet du son du Pi jusqu'au PC (capture, réseau, lecteur du PC) : un peu plus d'un dixième de seconde
MAX_AUDIO_ADVANCE_MS = 1000
GPIO_PINS = range(2, 28)  # les GPIO 2 à 27 de l'en-tête de 40 broches
LOG_LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR")
#: Les clés d'avant l'étape n (Freetv, la liste M3U de la Freebox), ignorées : { (section, clé) : ce qui les remplace }.
OBSOLETE_KEYS = {
    (
        "media",
        "playlist_url",
    ): "Free TV (tv.free.fr) remplace Freetv, la liste des chaînes de la Freebox n'est plus lue",
}
log = logging.getLogger(__name__)
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
    smb_host: str = DEFAULT_SMB_HOST
    smb_user: str = ""
    smb_password: str = field(default="", repr=False)  # un secret : jamais dans un affichage
    roms_dir: str = ""  # vide : le dossier « roms » de l'état du dock
    button_gpio: int = DEFAULT_BUTTON_GPIO
    #: Le PC associé (reconnu à son adresse IP) envoie des jeux sans le code de l'écran ; un autre appareil le recopie.
    upload_without_code: bool = True
    #: Le son du lecteur (VLC, MAME) sort sur le PC : le Pi n'a pas d'enceintes. Faux : il reste sur le Pi (HDMI, prise jack).
    pc_audio: bool = True
    #: Les vidéos avancent leur son de ce temps (ms) : le trajet jusqu'au PC le retarde, l'image ne doit pas arriver avant lui.
    audio_advance_ms: int = DEFAULT_AUDIO_ADVANCE_MS
    #: Free TV : l'identité (« user agent ») de Chromium — « auto » (un Chromium de bureau x86_64 de la même version), vide
    #: (celle d'origine) ou une chaîne ({major} y est remplacé par la version de Chromium) — et le zoom de la page.
    freetv_user_agent: str = DEFAULT_FREETV_USER_AGENT
    freetv_scale: float = DEFAULT_FREETV_SCALE
    #: Le coin de l'écran où un appui long ramène le dock pendant Free TV (un toucher simple navigue dans la page).
    freetv_corner: str = DEFAULT_FREETV_CORNER
    #: Free TV en plein écran ; faux : une fenêtre maximisée (le clavier à l'écran se montre alors au-dessus d'elle, voir freetv_playback.py).
    freetv_fullscreen: bool = True
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
        freetv = _section(raw, "freetv")
        _note_obsolete_keys(raw)
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
            raw_smb_host = media.get("smb_host", DEFAULT_SMB_HOST)
            smb_host = validate_host(raw_smb_host) if raw_smb_host else ""
            smb_user = clean_smb_user(media.get("smb_user", ""))
            smb_password = clean_smb_password(media.get("smb_password", ""))
            pc_audio = clean_flag(media.get("pc_audio", True), "pc_audio")
            audio_advance_ms = clean_audio_advance(
                media.get("audio_advance_ms", DEFAULT_AUDIO_ADVANCE_MS)
            )
        except (ValueError, ValidationFailure) as exc:
            raise ConfigError(f"[media] : {exc}") from None
        try:
            freetv_user_agent = clean_user_agent(
                freetv.get("user_agent", DEFAULT_FREETV_USER_AGENT)
            )
            freetv_scale = clean_freetv_scale(freetv.get("scale", DEFAULT_FREETV_SCALE))
            if (
                freetv_scale == LEGACY_FREETV_SCALE
            ):  # 0,7 rendait le texte illisible : le nouveau réglage par défaut le remplace
                _note_legacy_scale()
                freetv_scale = DEFAULT_FREETV_SCALE
            freetv_corner = clean_freetv_corner(freetv.get("corner", DEFAULT_FREETV_CORNER))
            freetv_fullscreen = clean_flag(freetv.get("fullscreen", True), "fullscreen")
        except ValidationFailure as exc:
            raise ConfigError(f"[freetv] : {exc}") from None
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
            smb_host=smb_host,
            smb_user=smb_user,
            smb_password=smb_password,
            roms_dir=roms_dir,
            button_gpio=button_gpio,
            upload_without_code=bool(retro.get("upload_without_code", True)),
            pc_audio=pc_audio,
            audio_advance_ms=audio_advance_ms,
            freetv_user_agent=freetv_user_agent,
            freetv_scale=freetv_scale,
            freetv_corner=freetv_corner,
            freetv_fullscreen=freetv_fullscreen,
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
        server, pc, paths, portal, media, retro, freetv = (
            raw.setdefault(name, {})
            for name in ("server", "pc", "paths", "portal", "media", "retrogame", "freetv")
        )
        for (
            section,
            key,
        ) in OBSOLETE_KEYS:  # une clé d'avant Free TV : elle ne revient pas dans le fichier
            if isinstance(raw.get(section), dict):
                raw[section].pop(key, None)
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
        media["smb_host"] = self.smb_host
        media["smb_user"] = self.smb_user
        media["smb_password"] = self.smb_password
        media["pc_audio"] = self.pc_audio
        media["audio_advance_ms"] = self.audio_advance_ms
        freetv["user_agent"] = self.freetv_user_agent
        freetv["scale"] = self.freetv_scale
        freetv["corner"] = self.freetv_corner
        freetv["fullscreen"] = self.freetv_fullscreen
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
        smb_host: object = None,
        smb_user: object = None,
        smb_password: object = None,
    ) -> None:
        """Divertissement : le serveur de fichiers du Kiosque et son identifiant (tout est validé avant d'être
        gardé ; un champ absent (None) ne change pas)."""
        host = self.smb_host
        if smb_host is not None:
            host = validate_host(smb_host) if str(smb_host).strip() else ""
        user = clean_smb_user(smb_user) if smb_user is not None else self.smb_user
        password = (
            clean_smb_password(smb_password) if smb_password is not None else self.smb_password
        )
        self.smb_host, self.smb_user, self.smb_password = host, user, password
        self.save()

    def set_sound(self, *, pc_audio: object = None, audio_advance_ms: object = None) -> None:
        """Le son du lecteur : sur le PC ou sur le Pi, et l'avance du son des vidéos (validés avant d'être gardés ; None ne change rien)."""
        flag = (
            clean_flag(pc_audio, "Le choix « son sur le PC »")
            if pc_audio is not None
            else self.pc_audio
        )
        advance = (
            clean_audio_advance(audio_advance_ms)
            if audio_advance_ms is not None
            else self.audio_advance_ms
        )
        self.pc_audio, self.audio_advance_ms = flag, advance
        self.save()

    def set_freetv(self, *, scale: object = None) -> None:
        """Free TV : la taille de la page (validée avant d'être gardée ; None ne change rien)."""
        if scale is not None:
            self.freetv_scale = clean_freetv_scale(scale)
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


def clean_flag(value: object, name: str) -> bool:
    if not isinstance(value, bool):
        raise ValidationFailure(f"{name} doit être vrai ou faux.")
    return value


def clean_audio_advance(value: object) -> int:
    """L'avance du son des vidéos, en millisecondes : un entier de 0 à 1000."""
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or not 0 <= value <= MAX_AUDIO_ADVANCE_MS
    ):
        raise ValidationFailure(
            f"L'avance du son doit être un nombre entier de millisecondes, de 0 à {MAX_AUDIO_ADVANCE_MS}."
        )
    return value


def clean_user_agent(value: object) -> str:
    """L'identité de Chromium pour Free TV : « auto », vide (celle d'origine) ou une ligne imprimable (300 caractères au plus)."""
    if value is None:
        return DEFAULT_FREETV_USER_AGENT
    if (
        not isinstance(value, str)
        or len(value.strip()) > MAX_USER_AGENT_LENGTH
        or any(ord(c) < 32 or ord(c) == 127 for c in value)
    ):
        raise ValidationFailure(
            f"user_agent doit être « auto », vide ou une ligne de {MAX_USER_AGENT_LENGTH} caractères au plus."
        )
    return value.strip()


def clean_freetv_scale(value: object) -> float:
    """Le zoom de la page de Free TV : un nombre de 0,25 à 3."""
    if (
        isinstance(value, bool)
        or not isinstance(value, int | float)
        or value != value
        or not MIN_FREETV_SCALE <= value <= MAX_FREETV_SCALE
    ):
        raise ValidationFailure(
            f"scale doit être un nombre de {MIN_FREETV_SCALE} à {MAX_FREETV_SCALE}."
        )
    return float(value)


def clean_freetv_corner(value: object) -> str:
    """Le coin du retour au dock pendant Free TV."""
    if value not in FREETV_CORNERS:
        raise ValidationFailure(f"corner doit être l'un de : {', '.join(FREETV_CORNERS)}.")
    return str(value)


_NOTED: set[tuple[str, str]] = set()


def _note_legacy_scale() -> None:
    """Le zoom 0,7 (l'ancien défaut) est remplacé : dit une fois dans le journal."""
    key = ("freetv", "scale")
    if key not in _NOTED:
        _NOTED.add(key)
        log.warning(
            "dock.toml : [freetv] scale = %g (l'ancien réglage par défaut, texte trop petit) est remplacé par %g ; "
            "la taille se règle dans Réglages → Divertissement",
            LEGACY_FREETV_SCALE,
            DEFAULT_FREETV_SCALE,
        )


def _note_obsolete_keys(raw: dict[str, Any]) -> None:
    """Une clé qui ne sert plus (d'un dock.toml d'avant Free TV) est ignorée, et on le dit une fois dans le journal."""
    for (section, key), why in OBSOLETE_KEYS.items():
        found = raw.get(section)
        if isinstance(found, dict) and key in found and (section, key) not in _NOTED:
            _NOTED.add((section, key))
            log.warning(
                "dock.toml : [%s] %s n'est plus utilisée et est ignorée (%s)", section, key, why
            )


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
    if smb_host is not None:
        try:
            config.set_media(smb_host=smb_host)
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
