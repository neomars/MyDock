"""`mydock-dock doctor` : diagnostic du dock, à lancer sur le Pi. N'affiche jamais le jeton."""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

from mydock_common.config import ConfigError
from mydock_common.report import FAIL, INFO, OK, WARN, Report
from mydock_dock import __version__
from mydock_dock.agent_link import LinkFailure, LinkSettings, LinkState, probe, resolve_ipv4
from mydock_dock.config import DockConfig, default_config_path
from mydock_dock.store import StateStore


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


async def _check_pc(report: Report, config: DockConfig) -> None:
    report.section("Liaison avec le PC")
    if not (config.agent_host and config.token):
        report.add(WARN, "Adresse ou jeton manquant : rien à tester.")
        return
    started = time.perf_counter()
    try:
        ip = await resolve_ipv4(config.agent_host, config.agent_port)
        report.add(
            OK, f"{config.agent_host} → {ip} ({(time.perf_counter() - started) * 1000:.0f} ms)"
        )
    except LinkFailure as failure:
        report.add(FAIL, failure.detail)
        return
    state, detail, agent = await probe(
        LinkSettings(config.agent_host, config.agent_port, config.token)
    )
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


def run_doctor(config_path: Path | None = None) -> int:
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
        check_running(report, config)
    print(report.render())
    return 1 if report.failed else 0
