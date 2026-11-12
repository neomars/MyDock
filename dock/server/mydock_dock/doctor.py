"""`mydock-dock doctor` : diagnostic du dock, à lancer sur le Pi. N'affiche jamais le jeton."""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlsplit

from mydock_common.config import ConfigError
from mydock_common.report import FAIL, INFO, OK, WARN, Report
from mydock_dock import __version__, player_session, playerlog, screensaver
from mydock_dock.agent_link import (
    UNREACHABLE_ERRNOS,
    LinkFailure,
    LinkSettings,
    LinkState,
    probe,
    resolve_ipv4_all,
)
from mydock_dock.clients import ClientRegistry
from mydock_dock.config import DockConfig, default_config_path
from mydock_dock.freebox import ChannelList, FreeboxError
from mydock_dock.markets.diagnose import PROBES
from mydock_dock.markets.errors import ProviderError
from mydock_dock.markets.factory import build_providers
from mydock_dock.markets.http import Fetcher
from mydock_dock.markets.model import Quote
from mydock_dock.netinfo import local_address_towards, local_ipv4_addresses
from mydock_dock.samba import SambaBrowser, SambaError
from mydock_dock.store import StateStore

TCP_TIMEOUT = 3.0


def _check_web_files(report: Report, web_dir: Path, entry: str | None) -> None:
    """Chaque page de l'interface est un fichier à part : un seul manquant et son bouton ouvre une page vide."""
    if entry is None:
        report.add(
            INFO, "index.html ne désigne pas de script d'entrée : interface non vérifiée plus loin."
        )
        return
    entry_file = web_dir / entry.lstrip("/")
    if not (entry_file.is_file() and os.access(entry_file, os.R_OK)):
        report.add(
            FAIL,
            f"index.html demande {entry}, absent ou illisible dans {web_dir} : l'interface est incomplète "
            "(réinstalle le paquet mydock).",
        )
        return
    missing = missing_assets(web_dir)
    if missing:
        shown = ", ".join(missing[:4]) + (" …" if len(missing) > 4 else "")
        report.add(
            FAIL,
            f"L'interface réclame {len(missing)} fichier(s) absent(s) ou illisible(s) : {shown}. "
            "Les pages concernées resteraient vides (réinstalle le paquet mydock).",
        )
    else:
        report.add(
            OK, f"Interface complète : {entry_file.name} et tous les fichiers qu'elle réclame"
        )


def _check_web_served(
    report: Report,
    config: DockConfig,
    entry: str | None,
    get: Callable[[str], tuple[int, bytes] | None],
) -> None:
    """Ce que le dock sert vraiment, par HTTP, est-il l'interface du dossier ?"""
    base = f"http://127.0.0.1:{config.port}"
    index = get(f"{base}/")
    if index is None:
        report.add(
            INFO,
            "Le dock ne répond pas sur 127.0.0.1 : interface non testée en HTTP (voir « Services »).",
        )
        return
    status, body = index
    served = entry_of(body.decode(errors="ignore")) if status == 200 else None
    if served is None:
        report.add(
            WARN,
            f"Le dock répond {status} à la page d'accueil, sans interface : journalctl -u mydock-dock -e",
        )
    elif entry is not None and served != entry:
        report.add(
            WARN,
            f"Le dock sert {served} alors que le dossier contient {entry} : "
            "sudo systemctl restart mydock-dock (un écran resté ouvert se recharge alors tout seul).",
        )
    else:
        script = get(f"{base}{served}")
        if script is not None and script[0] == 200:
            report.add(OK, f"Le dock sert l'interface en HTTP ({served})")
        else:
            report.add(
                FAIL,
                f"Le dock sert la page d'accueil mais pas {served} (statut {script[0] if script else 'aucune réponse'}).",
            )


def check_config(report: Report, path: Path) -> DockConfig | None:
    report.section(f"Configuration ({path})")
    try:
        config = DockConfig.load(path)
    except ConfigError as exc:
        report.add(FAIL, str(exc))
        return None
    report.add(OK, "Fichier lisible, en droits 600 (jeton non affiché)")
    report.add(INFO, f"Le dock écoute sur {config.listen_host}:{config.port}")
    if not config.agent_host:
        report.add(
            WARN, "Aucun PC configuré (pc.host) : à saisir dans Réglages ou dans le fichier."
        )
    else:
        report.add(INFO, f"PC visé : {config.agent_host}:{config.agent_port}")
    if not config.token:
        report.add(
            WARN, "Aucun jeton configuré (pc.token) : il vient de l'installation de l'agent."
        )
    return config


# Les noms de fichiers que l'interface compilée réclame : « import("./Music-B9amGNjk.js") », la liste des
# dépendances « assets/Music-BM5q27xV.css », les polices « url(./inter-latin-wght-normal-Dx4kXJAl.woff2) ».
ASSET_REFERENCE = re.compile(r"(?:\./|assets/)([\w.\-]+\.(?:js|css|woff2?))")


def entry_of(html: str) -> str | None:
    """Le script d'entrée d'une page index.html (« /assets/index-D5XASdAc.js »), ou None.

    La page compilée écrit « ./assets/… » : le chemin est ramené à la forme absolue, celle qu'on demande au dock.
    """
    tag = re.search(r"<script\b[^>]*\btype=[\"']module[\"'][^>]*>", html, re.IGNORECASE)
    src = re.search(r"\bsrc=[\"']([^\"']+)[\"']", tag.group(0), re.IGNORECASE) if tag else None
    return urlsplit(urljoin("http://dock.invalid/", src.group(1))).path if src else None


def missing_assets(web_dir: Path) -> list[str]:
    """Les fichiers que l'interface demande et que le dossier n'a pas (ou que ce compte ne peut pas lire)."""
    assets = web_dir / "assets"
    missing: set[str] = set()
    for bundle in sorted([*assets.glob("*.js"), *assets.glob("*.css")]):
        for name in ASSET_REFERENCE.findall(bundle.read_text(errors="ignore")):
            target = assets / name
            if not (target.is_file() and os.access(target, os.R_OK)):
                missing.add(name)
    return sorted(missing)


def _http_get(url: str, timeout: float = 2.0) -> tuple[int, bytes] | None:
    """(statut, corps) ou None si rien ne répond : jamais de proxy, c'est le Pi lui-même."""
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(url, timeout=timeout) as response:
            return response.status, response.read(1_000_000)
    except urllib.error.HTTPError as exc:
        return exc.code, b""
    except (OSError, urllib.error.URLError, ValueError):
        return None


def check_web(
    report: Report,
    config: DockConfig,
    *,
    get: Callable[[str], tuple[int, bytes] | None] = _http_get,
) -> None:
    report.section("Interface web")
    web_dir = config.web_dir
    if web_dir is None:
        report.add(FAIL, "paths.web_dir n'est pas défini dans la configuration.")
    elif not (web_dir / "index.html").is_file():
        report.add(FAIL, f"{web_dir}/index.html introuvable : compile l'interface (make web).")
    else:
        assets = list((web_dir / "assets").glob("*")) if (web_dir / "assets").is_dir() else []
        report.add(OK, f"{web_dir} : index.html et {len(assets)} fichier(s) d'assets")
        entry = entry_of((web_dir / "index.html").read_text(errors="ignore"))
        _check_web_files(report, web_dir, entry)
        _check_web_served(report, config, entry, get)
    state_dir = config.state_dir
    if state_dir.is_dir() and os.access(state_dir, os.W_OK):
        report.add(OK, f"Dossier d'état modifiable : {state_dir}")
    else:
        report.add(WARN, f"Dossier d'état absent ou non modifiable : {state_dir}")


@dataclass(frozen=True)
class TcpResult:
    outcome: str  # open | refused | silent | unreachable | error
    ms: float
    detail: str = ""


async def tcp_probe(ip: str, port: int, timeout: float = TCP_TIMEOUT) -> TcpResult:
    """Ouvre puis referme une connexion TCP : le port répond-il, refuse-t-il, ou se tait-il ?"""
    started = time.perf_counter()

    def elapsed() -> float:
        return (time.perf_counter() - started) * 1000

    try:
        _reader, writer = await asyncio.wait_for(asyncio.open_connection(ip, port), timeout)
    except ConnectionRefusedError:
        return TcpResult("refused", elapsed())
    except TimeoutError:
        return TcpResult("silent", elapsed())
    except OSError as exc:
        kind = "unreachable" if exc.errno in UNREACHABLE_ERRNOS else "error"
        return TcpResult(kind, elapsed(), exc.strerror or exc.__class__.__name__)
    writer.close()
    with contextlib.suppress(OSError):
        await writer.wait_closed()
    return TcpResult("open", elapsed())


async def ping(ip: str, timeout: float = 4.0) -> bool | None:
    """Le PC répond-il au ping ? None si ce Pi n'a pas la commande `ping`."""
    exe = shutil.which("ping")
    if exe is None:
        return None
    try:
        process = await asyncio.create_subprocess_exec(
            exe,
            "-c",
            "1",
            "-W",
            "2",
            ip,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except OSError:
        return None
    try:
        return await asyncio.wait_for(process.wait(), timeout) == 0
    except TimeoutError:
        process.kill()
        await process.wait()
        return False


async def _explain_address(report: Report, ip: str, port: int) -> bool:
    """Teste une adresse du PC et dit, en français, ce que le résultat signifie."""
    target = f"{ip}:{port}"
    result = await tcp_probe(ip, port)
    if result.outcome == "open":
        report.add(OK, f"{target} : le port répond ({result.ms:.0f} ms)")
        return True
    if result.outcome == "refused":
        report.add(
            FAIL,
            f"{target} : connexion refusée. Le PC répond, mais l'agent MyDock n'écoute pas sur ce "
            "port (agent arrêté ? session fermée ? mauvais port ? agent limité à une autre "
            "adresse ?). Sur le PC : systemctl --user status mydock-agent",
        )
        return False
    if result.outcome == "error":
        report.add(FAIL, f"{target} : connexion impossible ({result.detail}).")
        return False
    source = local_address_towards(ip) or "ADRESSE_DU_PI"
    alive = await ping(ip)
    if alive:
        report.add(
            FAIL,
            f"{target} : aucune réponse sur le port, alors que le PC répond au ping. Un pare-feu "
            f"du PC bloque sans doute le port {port}. Sur le PC :",
        )
        report.add(INFO, f"    sudo ufw allow from {source} to any port {port} proto tcp")
    elif alive is False:
        report.add(
            FAIL,
            f"{target} : aucune réponse, ni au ping. PC éteint ou en veille, hors du réseau, ou "
            "box/routeur qui isole les appareils Wi-Fi entre eux (« isolation des clients »).",
        )
    else:
        report.add(
            FAIL,
            f"{target} : aucune réponse (délai dépassé). Installe `ping` (paquet iputils-ping) "
            "pour savoir si le PC est allumé.",
        )
    return False


async def _check_pc(report: Report, config: DockConfig) -> None:
    report.section("Liaison avec le PC")
    if not (config.agent_host and config.token):
        report.add(WARN, "Adresse ou jeton manquant : rien à tester.")
        return
    host, port = config.agent_host, config.agent_port
    pi_addresses = local_ipv4_addresses()
    report.add(INFO, f"Adresses de ce Pi : {', '.join(pi_addresses) or 'aucune détectée'}")
    started = time.perf_counter()
    try:
        addresses = await resolve_ipv4_all(host, port)
    except LinkFailure as failure:
        report.add(FAIL, failure.detail)
        return
    if addresses == [host]:
        report.add(OK, f"Adresse du PC : {host}")
    else:
        elapsed = (time.perf_counter() - started) * 1000
        report.add(OK, f"{host} → {', '.join(addresses)} ({elapsed:.0f} ms)")
        if len(addresses) > 1:
            report.add(INFO, "Plusieurs adresses : le dock les essaie dans l'ordre jusqu'à succès.")
    reachable = [ip for ip in addresses if await _explain_address(report, ip, port)]
    if not reachable:
        report.add(INFO, "Test du jeton ignoré : aucune adresse du PC ne répond sur ce port.")
        return
    state, detail, agent = await probe(LinkSettings(reachable[0], port, config.token))
    if state == LinkState.CONNECTED:
        version = (agent or {}).get("version", "?")
        report.add(OK, f"Agent joignable et jeton accepté (agent {version}, dock {__version__})")
    else:
        report.add(FAIL, detail)


def check_wol(report: Report, config: DockConfig) -> None:
    report.section("Wake-on-LAN")
    pc = StateStore(config.state_dir).pc
    mac, source, kind = config.wol_mac, "saisie manuelle", None
    if not mac and pc.get("wol"):
        mac, source, kind = pc["wol"]["mac"], "apprise automatiquement", pc["wol"].get("kind")
    if not mac:
        report.add(WARN, "Adresse MAC du PC inconnue : connecte le dock au PC allumé une fois.")
        return
    report.add(OK, f"Adresse MAC mémorisée : {mac} ({source})")
    if kind == "wifi":
        report.add(
            WARN,
            "Cette adresse est celle du Wi-Fi : réveiller un PC éteint par Wi-Fi ne fonctionne "
            "en général pas. Branche le PC en Ethernet pour que ça marche.",
        )


def sleep_script_running() -> bool:
    """Le script de veille (lancé avec le kiosque) tourne-t-il ? Il se reconnaît à son nom."""
    if not shutil.which("pgrep"):
        return False
    try:
        result = subprocess.run(
            ["pgrep", "-f", "deploy/pi/screen-sleep.sh"],
            capture_output=True,
            text=True,
            timeout=3,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return result.returncode == 0


def check_saver(report: Report, config: DockConfig) -> None:
    """Économiseur d'écran : le réglage choisi, et les vidéos que le Pi peut montrer."""
    report.section("Économiseur d'écran")
    ui = StateStore(config.state_dir).ui
    after, content = ui["saver_after"], ui["saver_content"]
    contents = {"all": "scènes et vidéos", "scenes": "scènes animées", "videos": "vidéos du Pi"}
    if after == 0:
        report.add(
            INFO, "Économiseur : jamais (Réglages → Économiseur d'écran pour choisir un délai)"
        )
    else:
        report.add(
            OK,
            f"Économiseur : après {after // 60} min sans toucher, contenu : {contents.get(content, content)}",
        )
    sources = ui["saver_sources"]
    wants_download = ui["saver_aerial"] and (ui["saver_apple"] or sources)
    report.add(
        INFO,
        "Téléchargement automatique : "
        + ("suspendu" if not ui["saver_aerial"] else "actif")
        + f" ; vues aériennes d'Apple : {'oui' if ui['saver_apple'] else 'non'} ; "
        + f"{len(sources)} adresse(s) saisie(s) dans Réglages",
    )
    if wants_download:
        missing = [tool for tool in ("ffmpeg", "ffprobe") if not shutil.which(tool)]
        if missing:
            report.add(
                WARN,
                f"{', '.join(missing)} absent : aucun film ne peut être préparé (sudo apt install ffmpeg)",
            )
        else:
            report.add(OK, "ffmpeg et ffprobe présents (conversion des films téléchargés)")
    directory = config.saver_dir
    found = screensaver.videos(directory)
    if not found:
        report.add(
            INFO,
            f"Aucune vidéo dans {directory} : l'économiseur montre ses scènes animées. Les films se "
            "téléchargent seuls (Réglages → Économiseur d'écran → Vidéos et adresses) ; "
            "sinon : sudo mydock-dock screensaver add ADRESSE_HTTPS_OU_FICHIER",
        )
        return
    total = sum(v.size for v in found)
    report.add(OK, f"{len(found)} vidéo(s) dans {directory} ({screensaver.human_size(total)})")
    if not os.access(directory, os.R_OK | os.X_OK):
        report.add(
            WARN,
            f"{directory} n'est pas lisible par le service du dock (sudo chmod 755 {directory})",
        )
    for video in found:
        if video.size > 300 * 1024**2:
            report.add(
                WARN,
                f"{video.name} pèse {screensaver.human_size(video.size)} : lourde pour le Pi ; la convertir en "
                "1024×600 H.264 (voir le README) la rend fluide",
            )
    if after > 0 and content == "scenes":
        report.add(INFO, "Le contenu est réglé sur « scènes » : les vidéos ne sont pas montrées")


def check_screen(
    report: Report,
    config: DockConfig,
    *,
    which: Callable[[str], str | None] = shutil.which,
    running: Callable[[], bool] = sleep_script_running,
) -> None:
    """Veille de l'écran : le délai choisi, et ce qui doit exister sur le Pi pour que le rétroéclairage s'éteigne."""
    report.section("Écran du dock (veille)")
    seconds = StateStore(config.state_dir).ui["screen_sleep"]
    if seconds == 0:
        report.add(INFO, "Veille : jamais (Réglages → Écran du dock pour choisir un délai)")
    else:
        report.add(
            OK, f"Veille : après {seconds // 60} min sans toucher (Réglages → Écran du dock)"
        )
    asked = seconds > 0
    swayidle = which("swayidle")
    if swayidle:
        report.add(OK, "swayidle présent (surveille les touchers)")
    else:
        report.add(
            WARN if asked else INFO,
            "swayidle absent : l'interface noircit l'écran mais le rétroéclairage reste allumé "
            "(sudo apt install swayidle)",
        )
    if which("wlopm"):
        report.add(OK, "wlopm présent (éteint et rallume l'écran)")
    elif which("wlr-randr"):
        report.add(INFO, "wlopm absent : wlr-randr servira à éteindre l'écran")
    else:
        report.add(
            WARN if asked else INFO,
            "Ni wlopm ni wlr-randr : impossible d'éteindre l'écran (sudo apt install wlr-randr)",
        )
    if asked and swayidle:
        if running():
            report.add(OK, "Le script de veille tourne (lancé avec le kiosque)")
        else:
            report.add(
                WARN,
                "Le script de veille ne tourne pas : il démarre avec le kiosque (redémarre le Pi ; "
                "en cas d'échec : journalctl -u mydock-kiosk -e)",
            )


def check_portal(report: Report, config: DockConfig, *, port: int | None = None) -> None:
    """Le portail (page d'installation du PC) : ouvert ? joignable ? un paquet à proposer ?"""
    report.section("Portail (installation du PC)")
    if not config.portal_enabled:
        report.add(
            INFO, "Portail désactivé ([portal] enabled = false) : aucun PC ne peut s'associer"
        )
        return
    port = port or config.portal_port
    packages = ClientRegistry(config.clients_dir).scan()
    if packages:
        shown = ", ".join(f"{p.file} ({p.size // 1024} Ko)" for p in packages)
        report.add(OK, f"Paquet proposé au PC : {shown}")
    else:
        report.add(
            WARN,
            f"Aucun paquet du PC dans {config.clients_dir} : le portail n'a rien à télécharger. "
            "Réinstalle le paquet mydock du Pi (il embarque celui du PC).",
        )
    url = f"http://127.0.0.1:{port}/api/portal"
    try:
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(url, timeout=2) as response:
            state = json.load(response)
    except (OSError, urllib.error.URLError, ValueError):
        report.add(
            WARN,
            f"Le portail ne répond pas sur le port {port} : journalctl -u mydock-dock -e "
            "(port déjà pris, ou droit d'ouvrir un port inférieur à 1024 manquant).",
        )
        return
    report.add(OK, f"Le portail répond sur le port {port} (dock {state.get('version', '?')})")
    suffix = "" if port == 80 else f":{port}"
    for ip in local_ipv4_addresses():
        report.add(INFO, f"    Depuis le PC : http://{ip}{suffix}")
    if state.get("paired"):
        link = "connecté" if state.get("connected") else "hors ligne pour l'instant"
        report.add(OK, f"Un PC est associé ({link})")
    else:
        report.add(INFO, "Aucun PC associé : l'écran du dock montre le code à taper sur le PC")


MARKETS_PROBES = PROBES
MARKETS_TIMEOUT = 8.0


async def check_markets(
    report: Report,
    config: DockConfig,
    *,
    online: bool = True,
    fetcher: Fetcher | None = None,
    timeout: float = MARKETS_TIMEOUT,
) -> None:
    """La bourse : ce qui est suivi, les clés présentes (jamais affichées), et chaque fournisseur."""
    report.section("Bourse")
    markets = StateStore(config.state_dir).markets
    report.add(
        INFO,
        f"{len(markets['watchlist'])} valeur(s) suivie(s), cryptos cotées en {markets['currency']}, "
        f"mises à jour toutes les {markets['refresh']} s au plus vif",
    )
    cache = config.state_dir / "markets-cache.json"
    if cache.is_file():
        age = max(0, int(time.time() - cache.stat().st_mtime))
        report.add(INFO, f"Dernières cotations gardées : il y a {age // 60} min")
    report.add(
        INFO,
        "Clé Finnhub : configurée (non affichée)"
        if config.finnhub_key
        else "Clé Finnhub : aucune (facultative : Yahoo Finance et Binance n'en demandent pas)",
    )
    if not online:
        report.add(INFO, "Test des fournisseurs ignoré (--offline)")
        return
    providers = build_providers(finnhub_key=config.finnhub_key, fetcher=fetcher)

    async def probe_one(provider: Any) -> tuple[Any, float, Quote | ProviderError | str]:
        instrument = MARKETS_PROBES[provider.id]
        started = time.monotonic()
        try:
            async with asyncio.timeout(timeout):
                results = await provider.quotes([instrument], markets["currency"])
            outcome: Quote | ProviderError | str = results[instrument.id]
        except ProviderError as exc:
            outcome = exc
        except TimeoutError:
            outcome = "ne répond pas (délai dépassé)"
        return provider, time.monotonic() - started, outcome

    answers = await asyncio.gather(*(probe_one(p) for p in providers))
    failures = 0
    for provider, seconds, outcome in answers:
        instrument = MARKETS_PROBES[provider.id]
        if isinstance(outcome, Quote):
            price = f"{outcome.price:g}" + (f" {outcome.currency}" if outcome.currency else "")
            report.add(
                OK,
                f"{provider.label} répond ({instrument.symbol} : {price}, {seconds * 1000:.0f} ms)",
            )
            continue
        failures += 1
        message = outcome.message if isinstance(outcome, ProviderError) else outcome
        report.add(WARN, f"{provider.label} : {message}")
    if failures == len(providers):
        report.add(
            WARN,
            "Aucun fournisseur ne répond : le Pi a-t-il accès à Internet ? Essaie "
            "`curl -sI https://query2.finance.yahoo.com` et vérifie le DNS ; la page Bourse "
            "montre alors les dernières cotations gardées, avec leur âge.",
        )
    elif failures:
        report.add(
            INFO,
            "Un fournisseur en panne est remplacé par le suivant (Yahoo Finance prend les cryptos de Binance).",
        )


def player_session_running() -> bool:
    """L'assistant de lecture (lancé avec le kiosque) tourne-t-il ? Il se reconnaît à sa ligne de commande."""
    if not shutil.which("pgrep"):
        return False
    try:
        result = subprocess.run(
            ["pgrep", "-f", "mydock-dock player-session"],
            capture_output=True,
            text=True,
            timeout=3,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return result.returncode == 0


def check_media(
    report: Report,
    config: DockConfig,
    *,
    which: Callable[[str], str | None] = shutil.which,
    running: Callable[[], bool] = player_session_running,
    live555: Callable[[], bool] = player_session.has_live555,
    flags: Callable[[], list[str]] = player_session.read_flags,
    plugin: Callable[[str], bool] = player_session.has_vlc_plugin,
    journal: Callable[[], Path | None] = playerlog.existing_path,
    failures: Callable[[], list[str]] = playerlog.recent_failures,
) -> None:
    """Divertissement : VLC et ce qu'il faut autour, et les réglages (jamais le mot de passe)."""
    report.section("Divertissement (Freetv, Kiosque)")
    vlc = which("vlc") or which("cvlc")
    if vlc:
        report.add(OK, f"VLC présent ({vlc})")
        if live555():
            report.add(OK, "VLC lit lui-même les chaînes de la Freebox (module RTSP présent)")
        elif which("ffmpeg"):
            report.add(
                INFO,
                "VLC n'a pas le module RTSP : les chaînes passent par ffmpeg (il recopie le flux vers VLC, sans le décoder)",
            )
        else:
            report.add(
                WARN,
                "Ni module RTSP dans VLC ni ffmpeg : les chaînes ne pourront pas être lues (sudo apt install ffmpeg)",
            )
    else:
        report.add(
            WARN,
            "VLC est absent : le Divertissement ne lira rien (sudo apt install vlc-bin vlc-plugin-base vlc-plugin-video-output)",
        )
    if vlc:
        for name, why, package in (
            ("smb", "lire les fichiers du Samba (Kiosque)", "vlc-plugin-samba"),
            ("avcodec", "décoder les vidéos", "vlc-plugin-base"),
        ):
            if plugin(name):
                report.add(OK, f"VLC sait {why}")
            else:
                report.add(WARN, f"VLC ne sait pas {why} : sudo apt install {package}")
        if not any(plugin(name) for name in ("xcb_x11", "gl", "wl_shm", "xcb_xv")):
            report.add(
                WARN,
                "VLC n'a aucun module d'affichage : sudo apt install vlc-plugin-video-output",
            )
    switcher = "wlrctl" if which("wlrctl") else "xdotool" if which("xdotool") else None
    if switcher:
        report.add(OK, f"{switcher} présent (passer de la vidéo au dock)")
    else:
        report.add(
            WARN,
            "Ni wlrctl ni xdotool : impossible de passer de la vidéo au dock (sudo apt install wlrctl, ou xdotool)",
        )
    if which("swayidle"):
        report.add(OK, "swayidle présent (un toucher pendant la vidéo ramène le dock)")
    else:
        report.add(
            WARN,
            "swayidle est absent : un toucher ne ramènera pas le dock (sudo apt install swayidle)",
        )
    if not (which("Xwayland") or which("xwayland")):
        report.add(
            INFO,
            "XWayland est absent : VLC y affiche sa vidéo sur ce Pi (sudo apt install xwayland)",
        )
    if which("smbclient"):
        report.add(OK, "smbclient présent (parcourir les partages Samba)")
    else:
        report.add(
            WARN, "smbclient est absent : le Kiosque ne parcourra rien (sudo apt install smbclient)"
        )
    extra = flags()
    if extra:
        report.add(
            INFO, f"Options de VLC ajoutées ({player_session.FLAGS_FILE}) : {' '.join(extra)}"
        )
    if running():
        report.add(OK, "L'assistant de lecture tourne dans la session de l'écran")
    else:
        report.add(
            INFO,
            "L'assistant de lecture ne tourne pas : il démarre avec l'écran du dock (après une installation ou une mise à jour : sudo reboot)",
        )
    log_path = journal()
    if log_path is not None:
        report.add(
            INFO,
            f"Journal de la lecture : {log_path} (à lire avec : sudo cat {log_path}) ; "
            f"celui du serveur : {playerlog.LOG_DIR / playerlog.DOCK_LOG_NAME}",
        )
    else:
        report.add(
            INFO,
            f"Journal de la lecture : {playerlog.LOG_DIR / playerlog.LOG_NAME} (créé à la première lecture ; "
            "absent ? sudo /usr/lib/mydock/setup crée le dossier)",
        )
    for row in failures():
        report.add(WARN, f"Lecture ratée, d'après le journal : {row}")
    report.add(INFO, f"Liste des chaînes : {config.playlist_url}")
    if config.smb_host:
        user = f"identifiant « {config.smb_user} »" if config.smb_user else "accès sans identifiant"
        password = (
            "mot de passe configuré (non affiché)" if config.smb_password else "sans mot de passe"
        )
        report.add(INFO, f"Partages Samba : {config.smb_host} ({user}, {password})")
    else:
        report.add(INFO, "Partages Samba : aucun serveur (Réglages → Divertissement)")


async def check_media_network(report: Report, config: DockConfig, *, online: bool = True) -> None:
    """Divertissement : la Freebox répond-elle (liste des chaînes, partages Samba) ?"""
    if not online:
        report.add(INFO, "Test de la Freebox ignoré (--offline)")
        return
    try:
        channels, _ = await ChannelList(lambda: config.playlist_url).get()
        report.add(OK, f"La Freebox donne sa liste : {len(channels)} chaîne(s)")
    except FreeboxError as exc:
        report.add(
            WARN,
            f"Liste des chaînes : {exc} (la Freebox est-elle allumée, et le Pi sur le même réseau ?)",
        )
    if config.smb_host and shutil.which("smbclient"):
        browser = SambaBrowser(
            lambda: config.smb_host, lambda: config.smb_user, lambda: config.smb_password
        )
        try:
            shares = await browser.shares()
            names = ", ".join(shares[:6]) + ("…" if len(shares) > 6 else "")
            report.add(OK, f"{config.smb_host} répond : {len(shares)} partage(s) ({names})")
        except SambaError as exc:
            report.add(WARN, f"Partages Samba : {exc}")


def check_running(report: Report, config: DockConfig) -> None:
    report.section("Services")
    url = f"http://127.0.0.1:{config.port}/api/health"
    try:
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(url, timeout=2) as response:
            health = json.load(response)
        report.add(OK, f"Le serveur dock répond (état de la liaison : {health.get('link')})")
    except (OSError, urllib.error.URLError, ValueError):
        report.add(WARN, "Le serveur dock ne répond pas (sudo systemctl status mydock-dock).")
    if shutil.which("systemctl"):
        for unit in ("mydock-dock", "mydock-kiosk"):
            result = subprocess.run(
                ["systemctl", "is-active", unit], capture_output=True, text=True, check=False
            )
            state = result.stdout.strip() or "inconnu"
            report.add(OK if state == "active" else INFO, f"{unit} : {state}")
    for tool, why in (
        ("chromium", "navigateur du kiosque"),
        ("labwc", "compositeur Wayland du kiosque"),
        ("wlr-randr", "veille de l'écran (étape f)"),
    ):
        path = shutil.which(tool) or (
            shutil.which("chromium-browser") if tool == "chromium" else None
        )
        report.add(OK if path else INFO, f"{tool} {'présent' if path else 'absent'} ({why})")


def run_doctor(
    config_path: Path | None = None,
    *,
    offline: bool = False,
    markets_fetcher: Fetcher | None = None,
) -> int:
    path = Path(config_path) if config_path else default_config_path()
    report = Report()
    report.section(
        f"MyDock - diagnostic du dock (version {__version__}, Python {sys.version.split()[0]})"
    )
    config = check_config(report, path)
    if config is not None:
        check_web(report, config)
        asyncio.run(_check_pc(report, config))
        check_wol(report, config)
        check_screen(report, config)
        check_saver(report, config)
        check_portal(report, config)
        check_media(report, config)
        asyncio.run(check_media_network(report, config, online=not offline))
        asyncio.run(check_markets(report, config, online=not offline, fetcher=markets_fetcher))
        check_running(report, config)
    print(report.render())
    return 1 if report.failed else 0
