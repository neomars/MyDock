"""`mydock-dock doctor` : diagnostic du dock, à lancer sur le Pi. N'affiche jamais le jeton."""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
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

from mydock_common.config import ConfigError
from mydock_common.report import FAIL, INFO, OK, WARN, Report
from mydock_dock import __version__
from mydock_dock.agent_link import (
    UNREACHABLE_ERRNOS,
    LinkFailure,
    LinkSettings,
    LinkState,
    probe,
    resolve_ipv4_all,
)
from mydock_dock.config import DockConfig, default_config_path
from mydock_dock.markets.errors import ProviderError
from mydock_dock.markets.factory import build_providers
from mydock_dock.markets.http import Fetcher
from mydock_dock.markets.model import Instrument, Quote
from mydock_dock.netinfo import local_address_towards, local_ipv4_addresses
from mydock_dock.store import StateStore

TCP_TIMEOUT = 3.0


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


def check_web(report: Report, config: DockConfig) -> None:
    report.section("Interface web")
    web_dir = config.web_dir
    if web_dir is None:
        report.add(FAIL, "paths.web_dir n'est pas défini dans la configuration.")
    elif (web_dir / "index.html").is_file():
        assets = list((web_dir / "assets").glob("*")) if (web_dir / "assets").is_dir() else []
        report.add(OK, f"{web_dir} : index.html et {len(assets)} fichier(s) d'assets")
    else:
        report.add(FAIL, f"{web_dir}/index.html introuvable : compile l'interface (make web).")
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


MARKETS_PROBES = {
    "binance": Instrument("crypto", "BTC"),
    "yahoo": Instrument("stock", "AAPL"),
    "finnhub": Instrument("stock", "AAPL"),
}
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
        asyncio.run(check_markets(report, config, online=not offline, fetcher=markets_fetcher))
        check_running(report, config)
    print(report.render())
    return 1 if report.failed else 0
