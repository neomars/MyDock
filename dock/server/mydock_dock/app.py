"""Serveur du dock : sert l'interface, relaie l'agent vers le navigateur, exécute les actions locales.

Le navigateur (Chromium en kiosque) ne parle qu'à ce serveur, sur 127.0.0.1 :
  - il n'a jamais le jeton de l'agent ni son adresse réseau ;
  - quand le PC est éteint, l'interface reste vivante (horloge, bourse, Wake-on-LAN) ;
  - la reconnexion à l'agent est gérée à un seul endroit (agent_link.py).

Les actions « dock.* » sont exécutées ici ; toutes les autres sont transmises telles quelles à
l'agent, qui reste seul juge (liste blanche, paramètres validés, confirmations).
"""

from __future__ import annotations

import asyncio
import contextlib
import ipaddress
import itertools
import json
import logging
import socket
import time
from collections.abc import AsyncIterator, Callable, Mapping
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Literal

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt
from starlette.staticfiles import StaticFiles
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from mydock_common import protocol as proto
from mydock_common.actions import ActionContext, ActionError, ActionRegistry, NoParams
from mydock_common.config import ConfigError
from mydock_common.outbox import Outbox
from mydock_dock import __version__, screensaver
from mydock_dock.aerial import APPLE_ID, AerialFetcher
from mydock_dock.agent_link import (
    AgentLink,
    LinkSettings,
    LinkState,
    LinkStatus,
    backoff_delay,
    probe,
)
from mydock_dock.clients import ClientRegistry
from mydock_dock.config import DockConfig
from mydock_dock.freebox import ChannelList, FreeboxError
from mydock_dock.markets.diagnose import diagnose
from mydock_dock.markets.errors import NotFound, ProviderError
from mydock_dock.markets.factory import build_providers
from mydock_dock.markets.http import Fetcher
from mydock_dock.markets.service import MarketsService, Timing
from mydock_dock.netinfo import local_address_towards, local_ipv4_addresses
from mydock_dock.pairing import Pairing, PairingRefused
from mydock_dock.player import HelperConn, PlayerError, PlayerHub
from mydock_dock.portal import Portal
from mydock_dock.samba import SambaBrowser, SambaError, clean_path, clean_share, is_video, smb_url
from mydock_dock.store import StateStore
from mydock_dock.validation import ValidationFailure, normalize_mac, validate_host, validate_port
from mydock_dock.wol import send_magic_packet

log = logging.getLogger(__name__)
media_log = logging.getLogger("mydock_dock.media")  # dock.log : Freetv, Kiosque, lectures demandées

MAX_INFLIGHT_ACTIONS = 32
#: Combien de temps le dock attend la réponse de l'agent à une action du navigateur, en secondes.
AGENT_ACT_TIMEOUT = 6.0
#: Actions de l'agent qui peuvent durer (requêtes vers Spotify, lancement de l'application puis attente de son
#: apparition) : leur préfixe -> délai. L'agent borne chacune lui-même (15 à 60 s) ; le dock attend un peu plus
#: longtemps, pour que le message précis de l'agent arrive avant un « le PC n'a pas répondu à temps ».
SLOW_AGENT_ACTS: dict[str, float] = {"spotify.": 70.0}


def agent_act_timeout(name: str) -> float:
    """Le délai d'attente de la réponse de l'agent pour cette action."""
    for prefix, timeout in SLOW_AGENT_ACTS.items():
        if name.startswith(prefix):
            return timeout
    return AGENT_ACT_TIMEOUT


#: Thèmes que le dock suit tant qu'il est connecté à l'agent, même sans navigateur qui les demande :
#: « host » (nom du PC) et « system » (l'agent garde l'historique de 60 s, la page Système l'affiche
#: aussitôt ouverte). Les autres ne sont suivis que quand une page les montre.
BASELINE_TOPICS = frozenset({"host", "system"})
#: Thèmes locaux que le navigateur doit demander (ils pèsent plus et lancent un travail du dock) :
#: « markets » n'est suivi, et les cotations ne sont redemandées, que page Bourse ouverte.
ON_DEMAND_TOPICS = ("markets",)
MAX_CONSECUTIVE_BAD_MESSAGES = 10
LOOPBACK_NAMES = {"127.0.0.1", "localhost"}
CSP = (
    "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
    "img-src 'self' data: blob:; font-src 'self'; connect-src 'self' ws://127.0.0.1:* ws://localhost:*; "
    "object-src 'none'; base-uri 'none'; frame-ancestors 'none'"
)


# --- paramètres des actions locales ---------------------------------------------------------


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class PcSetParams(_Strict):
    host: str
    port: int = Field(strict=True)


class TokenParams(_Strict):
    token: str


class PcTestParams(_Strict):
    host: str | None = None
    port: int | None = Field(default=None, strict=True)
    token: str | None = None


class WolMacParams(_Strict):
    mac: str | None = None


class UiSetParams(_Strict):
    last_page: str | None = None
    order: list[str] | None = None
    hidden: list[str] | None = None
    pinned_apps: list[str] | None = None
    screen_sleep: StrictInt | None = None
    saver_after: StrictInt | None = None
    saver_content: Literal["all", "scenes", "videos"] | None = None
    saver_aerial: StrictBool | None = None
    saver_apple: StrictBool | None = None
    saver_sources: list[str] | None = Field(default=None, max_length=20)
    tv_hd: StrictBool | None = None


class TvListParams(_Strict):
    refresh: StrictBool = False


class PlayParams(_Strict):
    """Ce qu'on peut demander de lire : une chaîne que le dock a listée, ou un fichier d'un partage. Jamais une adresse."""

    kind: Literal["tv", "file"]
    id: str | None = Field(default=None, max_length=16)
    hd: StrictBool | None = None
    share: str | None = Field(default=None, max_length=255)
    path: str | None = Field(default=None, max_length=1000)


class PlayerSeekParams(_Strict):
    delta: float = Field(strict=True, ge=-86_400, le=86_400)


class PlayerVolumeParams(_Strict):
    value: StrictInt = Field(ge=0, le=100)


class PlayerFrontParams(_Strict):
    to: Literal["video", "dock"]


class MediaSetParams(_Strict):
    playlist_url: str | None = Field(default=None, max_length=300)
    smb_host: str | None = Field(default=None, max_length=253)
    smb_user: str | None = Field(default=None, max_length=64)
    smb_password: str | None = Field(default=None, max_length=128)


class MediaBrowseParams(_Strict):
    share: str = Field(max_length=255)
    path: str = Field(default="", max_length=1000)


class SaverForgetParams(_Strict):
    """Retire une source de vidéos (« apple » ou son adresse) et efface les films qu'elle avait donnés."""

    source: str = Field(min_length=5, max_length=500)


class MarketsSetParams(_Strict):
    watchlist: list[dict[str, str]] | None = Field(default=None, max_length=40)
    currency: str | None = Field(default=None, max_length=8)
    refresh: StrictInt | None = None


class MarketsSearchParams(_Strict):
    kind: str = Field(max_length=16)
    query: str = Field(max_length=120)


class MarketsCheckParams(_Strict):
    kind: str = Field(max_length=16)
    symbol: str = Field(max_length=40)


class MarketsHistoryParams(_Strict):
    id: str = Field(max_length=48)
    range: str = Field(max_length=8)


# --- connexion navigateur -------------------------------------------------------------------


class BrowserConn:
    _ids = itertools.count(1)

    def __init__(self, peer: str):
        self.id = next(self._ids)
        self.peer = peer
        self.topics: set[str] = set()  # thèmes de l'agent demandés par ce navigateur
        self.markets = False  # la page Bourse est ouverte : les cotations se mettent à jour
        self.outbox = Outbox()


# --- Wake-on-LAN : quelle interface du PC viser ----------------------------------------------


def pick_wol_interface(interfaces: list[Mapping[str, Any]]) -> dict[str, str] | None:
    """Interface Ethernet de préférence (seule capable de réveiller un PC éteint), sinon Wi-Fi."""
    candidates = []
    for itf in interfaces:
        try:
            mac = normalize_mac(itf.get("mac"))
        except ValidationFailure:
            continue
        if itf.get("kind") in ("ethernet", "wifi") and isinstance(itf.get("name"), str):
            candidates.append({**itf, "mac": mac})
    candidates.sort(
        key=lambda i: (i["kind"] != "ethernet", not i.get("default"), not i.get("up"), i["name"])
    )
    if not candidates:
        return None
    best = candidates[0]
    return {"mac": best["mac"], "iface": best["name"], "kind": best["kind"]}


# --- le serveur -----------------------------------------------------------------------------


class DockServer:
    def __init__(
        self,
        config: DockConfig,
        *,
        link_backoff: Callable[[int], float] = backoff_delay,
        markets_fetcher: Fetcher | None = None,
        markets_timing: Timing | None = None,
    ):
        self.config = config
        self.store = StateStore(config.state_dir)
        self.browsers: set[BrowserConn] = set()
        self.markets = MarketsService(
            store=self.store,
            providers=build_providers(
                demo=config.demo_markets, finnhub_key=config.finnhub_key, fetcher=markets_fetcher
            ),
            cache_path=None if config.demo_markets else config.state_dir / "markets-cache.json",
            on_change=lambda: self.push_local("markets"),
            timing=markets_timing,
            demo=config.demo_markets,
        )
        self.channels = ChannelList(lambda: self.config.playlist_url)
        self.samba = SambaBrowser(
            lambda: self.config.smb_host,
            lambda: self.config.smb_user,
            lambda: self.config.smb_password,
        )
        self.player = PlayerHub(on_change=lambda: self.push_local("player"))
        self.link = AgentLink(
            self._link_settings,
            on_status=self._on_link_status,
            on_snapshot=self._on_agent_snapshot,
            backoff=link_backoff,
        )
        self.actions = ActionRegistry()
        self.pairing = Pairing(on_change=lambda: self.push_local("dock"))
        self.clients = ClientRegistry(config.clients_dir)
        self.portal = Portal(self)
        self._pairing_lock = asyncio.Lock()
        self._revs = {topic: 0 for topic in (*proto.DOCK_TOPICS, *ON_DEMAND_TOPICS)}
        self._link_task: asyncio.Task[None] | None = None
        self._markets_task: asyncio.Task[None] | None = None
        self._pairing_task: asyncio.Task[None] | None = None
        self._aerial_task: asyncio.Task[None] | None = None
        # Les vues aériennes se téléchargent seules ; jamais en démonstration (aucun accès à Internet attendu).
        self.aerial = AerialFetcher(
            config.saver_dir,
            lambda: bool(self.store.ui["saver_aerial"]),
            apple=lambda: bool(self.store.ui["saver_apple"]),
            sources=lambda: list(self.store.ui["saver_sources"]),
            trust_dir=config.state_dir / "trust",
        )
        self._register_actions()

    # --- cycle de vie --------------------------------------------------------------------

    def start(self) -> None:
        self.link.set_subscriptions(set(BASELINE_TOPICS))
        self._link_task = asyncio.create_task(self._run_link(), name="agent-link")
        self._markets_task = asyncio.create_task(self.markets.run(), name="markets")
        self.portal.start()
        self._pairing_task = asyncio.create_task(self._run_pairing(), name="pairing")
        if not self.config.demo_markets:
            self._aerial_task = asyncio.create_task(self.aerial.run(), name="aerial")

    async def _run_link(self) -> None:
        while True:  # la liaison ne doit jamais rester morte à cause d'une erreur inattendue
            try:
                await self.link.run()
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("La liaison avec l'agent a planté ; redémarrage dans 5 s")
                await asyncio.sleep(5)

    async def stop(self) -> None:
        tasks = [
            t
            for t in (self._link_task, self._markets_task, self._pairing_task, self._aerial_task)
            if t is not None
        ]
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await self.portal.stop()
        self.player.shutdown()
        self.markets.save()
        await self.link.close()
        for conn in tuple(self.browsers):
            conn.outbox.close()

    def _link_settings(self) -> LinkSettings:
        cfg = self.config
        return LinkSettings(cfg.agent_host, cfg.agent_port, cfg.token)

    @property
    def is_paired(self) -> bool:
        """Un PC est configuré (par association ou à la main dans Réglages)."""
        return bool(self.config.agent_host and self.config.token)

    async def _run_pairing(self) -> None:
        """Constate l'expiration d'une association ; tant qu'aucun PC n'est associé, il y a un code.

        Un dock neuf montre donc d'emblée un code à taper sur le PC. Une fois un PC associé, un
        code n'existe que si on l'a demandé (Réglages → Associer un PC).
        """
        while True:
            try:
                self.pairing.tick()
                if not self.is_paired and not self.pairing.is_open() and self.portal.running:
                    self.pairing.start()
            except Exception:
                log.exception("Erreur dans la boucle d'association")
            await asyncio.sleep(1.0)

    # --- thèmes locaux -------------------------------------------------------------------

    def wol_info(self) -> dict[str, Any]:
        pc = self.store.pc
        interfaces = pc.get("interfaces") or []
        if self.config.wol_mac:
            known = next((i for i in interfaces if i["mac"] == self.config.wol_mac), None)
            return {
                "mac": self.config.wol_mac,
                "source": "manual",
                "iface": known["name"] if known else None,
                "kind": known["kind"] if known else None,
                "interfaces": interfaces,
            }
        learned = pc.get("wol")
        if learned:
            return {**learned, "source": "auto", "interfaces": interfaces}
        return {"mac": None, "source": None, "iface": None, "kind": None, "interfaces": interfaces}

    def portal_info(self) -> dict[str, Any]:
        """Où le PC ouvre le portail, et le paquet qu'il y télécharge (rien de secret)."""
        port = self.portal.port
        urls: list[str] = []
        if port is not None:
            suffix = "" if port == 80 else f":{port}"
            urls = [f"http://{ip}{suffix}" for ip in local_ipv4_addresses()]
            urls.append(f"http://{socket.gethostname()}.local{suffix}")
        client = self.clients.latest()
        return {
            "enabled": self.config.portal_enabled,
            "port": port,
            "urls": urls,
            "error": self.portal.error,
            "client": {"file": client.file, "version": client.version} if client else None,
        }

    def markets_info(self) -> dict[str, Any]:
        """Le résumé de la bourse pour les Réglages (sans abonnement aux cotations, donc sans trafic)."""
        return {
            "count": len(self.markets.watchlist),
            "currency": self.markets.currency,
            "refresh": int(self.store.markets.get("refresh", 15)),
            "finnhub": bool(self.config.finnhub_key),  # présente ou non : jamais la clé elle-même
            "demo": self.config.demo_markets,
        }

    def media_info(self) -> dict[str, Any]:
        """Divertissement, pour les Réglages : jamais le mot de passe, seulement s'il y en a un."""
        cfg = self.config
        return {
            "playlist_url": cfg.playlist_url,
            "smb_host": cfg.smb_host,
            "smb_user": cfg.smb_user,
            "smb_password_set": bool(cfg.smb_password),
            "smbclient": self.samba.available(),
        }

    def payload(self, topic: str) -> dict[str, Any]:
        if topic == "link":
            return self.link.status.to_dict()
        if topic == "dock":
            return {
                "version": __version__,
                "proto": proto.PROTOCOL_VERSION,
                "hostname": socket.gethostname(),
                "ips": local_ipv4_addresses(),
                "pc": {
                    "host": self.config.agent_host,
                    "port": self.config.agent_port,
                    "token_set": bool(self.config.token),
                    "name": self.store.pc.get("hostname"),
                },
                "wol": self.wol_info(),
                "markets": self.markets_info(),
                "portal": self.portal_info(),
                "pairing": self.pairing.info(),
                "media": self.media_info(),
            }
        if topic == "ui":
            return self.store.ui
        if topic == "player":
            return self.player.snapshot()
        if topic == "markets":
            return self.markets.payload()
        raise KeyError(topic)

    def local_text(self, topic: str) -> str:
        return proto.dumps(proto.snap(topic, self._revs[topic], self.payload(topic)))

    def push_local(self, topic: str) -> None:
        self._revs[topic] += 1
        text = self.local_text(topic)
        for conn in self.browsers:
            conn.outbox.push_snapshot(topic, text)

    def _on_link_status(self, _status: LinkStatus) -> None:
        self.push_local("link")

    def _on_agent_snapshot(self, topic: str, rev: int, data: dict[str, Any]) -> None:
        if topic == "host":
            self._learn_host(data)
        text = proto.dumps(proto.snap(topic, rev, data))
        for conn in self.browsers:
            if topic in conn.topics:
                conn.outbox.push_snapshot(topic, text)

    def _learn_host(self, data: dict[str, Any]) -> None:
        """Mémorise l'identité et les adresses MAC du PC : indispensables quand il sera éteint."""
        raw = data.get("interfaces")
        interfaces = []
        for itf in raw if isinstance(raw, list) else []:
            if isinstance(itf, dict):
                try:
                    interfaces.append(
                        {
                            "name": str(itf["name"]),
                            "mac": normalize_mac(itf["mac"]),
                            "kind": str(itf["kind"]),
                        }
                    )
                except (KeyError, ValidationFailure):
                    continue
        hostname = data.get("hostname")
        changed = self.store.update_pc(
            hostname=hostname if isinstance(hostname, str) else None,
            interfaces=interfaces,
            wol=pick_wol_interface(raw if isinstance(raw, list) else []),
            seen_at=int(time.time() * 1000),
        )
        if changed:
            self.push_local("dock")

    # --- association d'un PC ---------------------------------------------------------------

    def pi_addresses_towards(self, peer: str) -> list[str]:
        """Les adresses sous lesquelles ce Pi se présentera au PC : ce que l'agent doit autoriser."""
        toward = local_address_towards(peer)
        found = [toward] if toward else local_ipv4_addresses()[:3]
        return [ip for ip in found if ip]

    async def complete_pairing(self, peer: str, port: int, token: str, name: str) -> dict[str, Any]:
        """Le code est bon : vérifie que l'agent répond avec ce jeton, puis l'enregistre comme PC piloté."""
        async with self._pairing_lock:
            pi_ips = self.pi_addresses_towards(peer)
            state, detail, _agent = await probe(LinkSettings(peer, port, token))
            if state != LinkState.CONNECTED:
                raise PairingRefused(
                    "unreachable" if state != LinkState.AUTH_FAILED else "refused",
                    f"Le dock n'arrive pas à joindre l'agent de ce PC ({peer}:{port}) : {detail}",
                    status=502,
                    extra={"pi_ips": pi_ips, "state": str(state)},
                )
            previous = self.config.agent_host
            try:
                self.config.set_pairing(peer, port, token)
                if previous and previous != peer:
                    self.config.set_wol_mac("")  # l'adresse MAC d'un autre PC ne vaut plus rien
                    self.store.update_pc(interfaces=[], wol=None, seen_at=None)
                self.store.update_pc(hostname=name)
            except (ValidationFailure, OSError, ConfigError):
                log.exception("Impossible d'enregistrer le PC associé")
                raise PairingRefused(
                    "save_failed",
                    f"Le dock n'a pas pu enregistrer sa configuration ({self.config.path}).",
                    status=500,
                ) from None
            self.link.reload()
            self.pairing.finish(name, peer)
            log.warning("PC associé : %s (%s:%d)", name, peer, port)
            return {
                "ok": True,
                "dock": {"version": __version__, "hostname": socket.gethostname()},
                "pc": {"host": peer, "port": port},
                "pi_ips": pi_ips,
            }

    # --- abonnements des navigateurs -----------------------------------------------------

    def subscribe(self, conn: BrowserConn, topics: list[str]) -> None:
        for topic in topics:
            if topic in proto.DOCK_TOPICS:
                conn.outbox.push_snapshot(topic, self.local_text(topic))
            elif topic in ON_DEMAND_TOPICS:
                conn.markets = True
                conn.outbox.push_snapshot(topic, self.local_text(topic))
                self._sync_markets()
            elif topic in proto.AGENT_TOPICS:
                conn.topics.add(topic)
                cached = self.link.snapshots.get(topic)
                if cached:
                    conn.outbox.push_snapshot(
                        topic, proto.dumps(proto.snap(topic, cached["rev"], cached["data"]))
                    )
            else:
                raise proto.ProtocolError(proto.ErrorCode.UNKNOWN_TOPIC, f"Thème inconnu : {topic}")
        self._sync_upstream()

    def unsubscribe(self, conn: BrowserConn, topics: list[str]) -> None:
        conn.topics.difference_update(topics)
        if "markets" in topics:
            conn.markets = False
            self._sync_markets()
        self._sync_upstream()

    def _sync_markets(self) -> None:
        self.markets.set_watching(any(c.markets for c in self.browsers))

    def _sync_upstream(self) -> None:
        wanted = set(BASELINE_TOPICS).union(*(c.topics for c in self.browsers))
        self.link.set_subscriptions(wanted)

    def detach(self, conn: BrowserConn) -> None:
        self.browsers.discard(conn)
        self._sync_markets()
        self._sync_upstream()

    # --- actions -------------------------------------------------------------------------

    async def run_act(self, conn: BrowserConn, act: proto.Act) -> None:
        if act.name.startswith("dock."):
            reply = await self.actions.invoke(act, ActionContext(conn.id, conn.peer))
        else:
            reply = await self.link.request(
                act.name, act.params, confirm=act.confirm, timeout=agent_act_timeout(act.name)
            )
            reply["id"] = act.id
        conn.outbox.push_control(proto.dumps(reply))

    def _register_actions(self) -> None:
        reg = self.actions
        reg.register("dock.ping", self._act_ping)
        reg.register("dock.pc.set", self._act_pc_set, params=PcSetParams)
        reg.register("dock.token.set", self._act_token_set, params=TokenParams)
        reg.register("dock.pc.test", self._act_pc_test, params=PcTestParams, timeout=15.0)
        reg.register("dock.wol.send", self._act_wol_send, timeout=8.0)
        reg.register("dock.wol.set_mac", self._act_wol_set_mac, params=WolMacParams)
        reg.register("dock.ui.set", self._act_ui_set, params=UiSetParams)
        reg.register(
            "dock.saver.forget", self._act_saver_forget, params=SaverForgetParams, timeout=20.0
        )
        reg.register("dock.tv.list", self._act_tv_list, params=TvListParams, timeout=20.0)
        reg.register("dock.media.shares", self._act_media_shares, timeout=25.0)
        reg.register(
            "dock.media.browse", self._act_media_browse, params=MediaBrowseParams, timeout=25.0
        )
        reg.register("dock.media.set", self._act_media_set, params=MediaSetParams)
        reg.register("dock.media.test", self._act_media_test, timeout=45.0)
        reg.register("dock.player.play", self._act_player_play, params=PlayParams, timeout=20.0)
        reg.register("dock.player.pause", self._act_player_pause)
        reg.register("dock.player.stop", self._act_player_stop)
        reg.register("dock.player.seek", self._act_player_seek, params=PlayerSeekParams)
        reg.register("dock.player.volume", self._act_player_volume, params=PlayerVolumeParams)
        reg.register("dock.player.front", self._act_player_front, params=PlayerFrontParams)
        reg.register("dock.player.dismiss", self._act_player_dismiss)
        reg.register("dock.pairing.open", self._act_pairing_open)
        reg.register("dock.pairing.close", self._act_pairing_close)
        reg.register("dock.markets.set", self._act_markets_set, params=MarketsSetParams)
        reg.register(
            "dock.markets.check", self._act_markets_check, params=MarketsCheckParams, timeout=45.0
        )
        reg.register("dock.markets.refresh", self._act_markets_refresh, timeout=45.0)
        reg.register("dock.markets.diagnose", self._act_markets_diagnose, timeout=45.0)
        reg.register(
            "dock.markets.search",
            self._act_markets_search,
            params=MarketsSearchParams,
            timeout=45.0,
        )
        reg.register(
            "dock.markets.history",
            self._act_markets_history,
            params=MarketsHistoryParams,
            timeout=45.0,
        )

    def _persist(self, change: Any) -> None:
        """Exécute une modification de configuration en traduisant les erreurs pour l'écran."""
        try:
            change()
        except ValidationFailure as exc:
            raise ActionError(proto.ErrorCode.BAD_PARAMS, str(exc)) from None
        except (OSError, ConfigError):
            log.exception("Impossible d'enregistrer la configuration")
            raise ActionError(
                proto.ErrorCode.FAILED,
                f"Impossible d'enregistrer {self.config.path} (droits d'écriture ?).",
            ) from None

    async def _act_ping(self, _ctx: ActionContext, _params: NoParams) -> dict[str, int]:
        return {"dock_ms": int(time.time() * 1000)}

    async def _act_pc_set(self, _ctx: ActionContext, params: PcSetParams) -> dict[str, Any]:
        self._persist(lambda: self.config.set_pc(params.host, params.port))
        self.link.reload()
        self.push_local("dock")
        return {"host": self.config.agent_host, "port": self.config.agent_port}

    async def _act_token_set(self, _ctx: ActionContext, params: TokenParams) -> dict[str, bool]:
        self._persist(lambda: self.config.set_token(params.token))
        self.link.reload()
        self.push_local("dock")
        return {"token_set": True}

    async def _act_pc_test(self, _ctx: ActionContext, params: PcTestParams) -> dict[str, Any]:
        try:
            host = validate_host(params.host) if params.host else self.config.agent_host
            port = (
                validate_port(params.port, "Le port de l'agent")
                if params.port is not None
                else self.config.agent_port
            )
        except ValidationFailure as exc:
            raise ActionError(proto.ErrorCode.BAD_PARAMS, str(exc)) from None
        settings = LinkSettings(host, port, params.token or self.config.token)
        if not settings.configured:
            raise ActionError("not_configured", "Renseigne d'abord l'adresse du PC et le jeton.")
        state, detail, agent = await probe(settings)
        return {"state": str(state), "detail": detail, "agent": agent}

    async def _act_wol_send(self, _ctx: ActionContext, _params: NoParams) -> dict[str, Any]:
        info = self.wol_info()
        if not info["mac"]:
            raise ActionError(
                "no_mac",
                "Adresse MAC du PC inconnue : le dock la mémorise dès qu'il s'est connecté une "
                "fois au PC allumé, ou saisis-la dans Réglages.",
            )
        results = await send_magic_packet(info["mac"])
        sent = sum(1 for r in results if r["ok"])
        if sent == 0:
            raise ActionError(
                "wol_failed", "Aucun paquet n'a pu partir : le réseau du Pi est-il actif ?"
            )
        return {
            "mac": info["mac"],
            "kind": info["kind"],
            "sent": sent,
            "failed": len(results) - sent,
        }

    async def _act_wol_set_mac(self, _ctx: ActionContext, params: WolMacParams) -> dict[str, Any]:
        self._persist(lambda: self.config.set_wol_mac(params.mac))
        self.push_local("dock")
        return {"mac": self.config.wol_mac or None}

    async def _act_pairing_open(self, _ctx: ActionContext, _params: NoParams) -> dict[str, Any]:
        """Affiche un nouveau code d'association (l'ancien cesse de valoir)."""
        if not self.portal.running:
            raise ActionError(
                "portal_down",
                self.portal.error or "Le portail est désactivé ([portal] enabled dans dock.toml).",
            )
        self.pairing.start()
        return {"open": True}

    async def _act_pairing_close(self, _ctx: ActionContext, _params: NoParams) -> dict[str, Any]:
        self.pairing.stop("closed")
        return {"open": False}

    async def _act_ui_set(self, _ctx: ActionContext, params: UiSetParams) -> dict[str, Any]:
        patch = params.model_dump(exclude_none=True)
        ui: dict[str, Any] = {}

        def apply() -> None:
            ui.update(self.store.update_ui(patch))

        self._persist(apply)
        self.push_local("ui")
        return ui

    # --- Divertissement : Freetv et Kiosque ---------------------------------------------------------

    async def _act_tv_list(self, _ctx: ActionContext, params: TvListParams) -> dict[str, Any]:
        """Les chaînes de la Freebox (sans leurs adresses de flux)."""
        try:
            channels, warning = await self.channels.get(refresh=params.refresh)
        except FreeboxError as exc:
            media_log.warning(
                "Freetv : liste des chaînes illisible (%s) : %s", self.config.playlist_url, exc
            )
            raise ActionError(proto.ErrorCode.UNAVAILABLE, str(exc)) from None
        if warning:
            media_log.warning("Freetv : %d chaînes (liste gardée) : %s", len(channels), warning)
        elif params.refresh:
            media_log.info("Freetv : %d chaînes lues (%s)", len(channels), self.config.playlist_url)
        return {"channels": [c.public() for c in channels], "warning": warning}

    async def _act_media_shares(self, _ctx: ActionContext, _params: NoParams) -> dict[str, Any]:
        try:
            shares = await self.samba.shares()
        except SambaError as exc:
            media_log.warning("Kiosque : partages de %s illisibles : %s", self.config.smb_host, exc)
            raise ActionError(proto.ErrorCode.UNAVAILABLE, str(exc)) from None
        media_log.info("Kiosque : %d partage(s) sur %s", len(shares), self.config.smb_host)
        return {"host": self.config.smb_host, "shares": shares}

    async def _act_media_browse(
        self, _ctx: ActionContext, params: MediaBrowseParams
    ) -> dict[str, Any]:
        try:
            share, parts = clean_share(params.share), clean_path(params.path)
            entries = await self.samba.listing(share, parts)
        except SambaError as exc:
            media_log.warning(
                "Kiosque : dossier %s/%s illisible : %s", params.share, params.path or "", exc
            )
            raise ActionError(proto.ErrorCode.UNAVAILABLE, str(exc)) from None
        media_log.info("Kiosque : %s/%s : %d entrée(s)", share, "/".join(parts), len(entries))
        return {"share": share, "path": "/".join(parts), "entries": [e.public() for e in entries]}

    async def _act_media_set(self, _ctx: ActionContext, params: MediaSetParams) -> dict[str, Any]:
        patch = params.model_dump(exclude_none=True)
        self._persist(lambda: self.config.set_media(**patch))
        shown = {
            k: ("(défini)" if k == "smb_password" else v) for k, v in patch.items()
        }  # jamais le mot de passe
        media_log.info("Réglages du Divertissement changés : %s", shown)
        self.push_local("dock")
        return self.media_info()

    async def _act_media_test(self, _ctx: ActionContext, _params: NoParams) -> dict[str, Any]:
        """Essaie chaque maillon (liste des chaînes, partages, lecteur) et dit en clair lequel ne va pas."""
        result: dict[str, Any] = {}
        media_log.info(
            "Essai Freetv et Kiosque demandé (liste %s, serveur %s)",
            self.config.playlist_url,
            self.config.smb_host,
        )
        try:
            channels, warning = await self.channels.get(refresh=True)
            result["tv"] = {
                "ok": True,
                "text": f"{len(channels)} chaînes lues.",
                "warning": warning,
            }
        except FreeboxError as exc:
            result["tv"] = {"ok": False, "text": str(exc)}
        try:
            shares = await self.samba.shares()
            names = ", ".join(shares[:6]) + ("…" if len(shares) > 6 else "")
            result["files"] = {
                "ok": bool(shares),
                "text": f"Partages : {names}."
                if shares
                else "Aucun partage visible (identifiant ?).",
            }
        except SambaError as exc:
            result["files"] = {"ok": False, "text": str(exc)}
        info = self.player.snapshot()["helper"]
        if info is None:
            result["player"] = {
                "ok": False,
                "text": "L'assistant de lecture ne répond pas (il démarre avec l'écran du dock).",
            }
        elif not info["vlc"]:
            result["player"] = {
                "ok": False,
                "text": "VLC est absent : sudo apt install vlc-bin vlc-plugin-base vlc-plugin-video-output.",
            }
        elif not info["touch"]:
            result["player"] = {
                "ok": False,
                "text": "swayidle, ou wlrctl (ou xdotool), manque : sudo apt install swayidle wlrctl.",
            }
        else:
            result["player"] = {
                "ok": True,
                "text": "VLC est prêt."
                + (
                    ""
                    if info["live555"] or info["ffmpeg"]
                    else " (ffmpeg manque : les chaînes ne passeront pas)"
                ),
            }
        media_log.info(
            "Essai : %s",
            " ; ".join(
                f"{k} {'ok' if v['ok'] else 'ÉCHEC'} ({v['text']})" for k, v in result.items()
            ),
        )
        return result

    async def _act_player_play(self, _ctx: ActionContext, params: PlayParams) -> dict[str, Any]:
        try:
            if params.kind == "tv":
                media, url, secrets = await self._tv_request(params)
            else:
                media, url, secrets = self._file_request(params)
            self.player.play(media, url, secrets=secrets)
        except (PlayerError, SambaError, FreeboxError) as exc:
            media_log.warning("Lecture refusée (%s) : %s", params.kind, exc)
            raise ActionError(proto.ErrorCode.UNAVAILABLE, str(exc)) from None
        media_log.info(
            "Lecture %d demandée à l'assistant : %s « %s » (%s)",
            self.player.job,
            params.kind,
            media["title"],
            url,
        )
        return {"started": media["title"]}

    async def _tv_request(self, params: PlayParams) -> tuple[dict[str, Any], str, dict[str, str]]:
        if not params.id:
            raise PlayerError("Quelle chaîne ?")
        channel = self.channels.find(params.id)
        if channel is None:
            await self.channels.get()
            channel = self.channels.find(params.id)
        if channel is None:
            raise PlayerError("Cette chaîne n'est plus dans la liste de la Freebox.")
        hd = self.store.ui["tv_hd"] if params.hd is None else params.hd
        quality, url = channel.stream(hd=bool(hd))
        number = f"{channel.number} · " if channel.number else ""
        media = {
            "kind": "tv",
            "title": channel.name,
            "subtitle": f"{number}{quality.upper() if quality != 'auto' else 'Direct'}",
            "live": True,
        }
        return media, url, {}

    def _file_request(self, params: PlayParams) -> tuple[dict[str, Any], str, dict[str, str]]:
        if not params.share or not params.path:
            raise PlayerError("Quel fichier ?")
        share, parts = clean_share(params.share), clean_path(params.path)
        if not parts or not is_video(parts[-1]):
            raise PlayerError("Ce fichier n'est pas une vidéo.")
        host = self.config.smb_host
        if not host:
            raise PlayerError(
                "Aucun serveur de fichiers : renseigne-le dans Réglages → Divertissement."
            )
        stem = parts[-1].rsplit(".", 1)[0]
        media = {
            "kind": "file",
            "title": stem,
            "subtitle": "/".join([share, *parts[:-1]]),
            "live": False,
            "smb": {"host": host, "share": share, "path": "/".join(parts)},
        }
        secrets: dict[str, str] = {}
        if self.config.smb_user:
            secrets = {"smb_user": self.config.smb_user, "smb_password": self.config.smb_password}
        return media, smb_url(host, share, parts), secrets

    async def _player_command(self, name: str, **params: Any) -> dict[str, Any]:
        try:
            self.player.command(name, **params)
        except PlayerError as exc:
            raise ActionError(proto.ErrorCode.UNAVAILABLE, str(exc)) from None
        return {}

    async def _act_player_pause(self, _ctx: ActionContext, _params: NoParams) -> dict[str, Any]:
        return await self._player_command("toggle")

    async def _act_player_stop(self, _ctx: ActionContext, _params: NoParams) -> dict[str, Any]:
        return await self._player_command("stop")

    async def _act_player_seek(
        self, _ctx: ActionContext, params: PlayerSeekParams
    ) -> dict[str, Any]:
        return await self._player_command("seek", delta=params.delta)

    async def _act_player_volume(
        self, _ctx: ActionContext, params: PlayerVolumeParams
    ) -> dict[str, Any]:
        return await self._player_command("volume", value=params.value)

    async def _act_player_front(
        self, _ctx: ActionContext, params: PlayerFrontParams
    ) -> dict[str, Any]:
        return await self._player_command("front", to=params.to)

    async def _act_player_dismiss(self, _ctx: ActionContext, _params: NoParams) -> dict[str, Any]:
        self.player.reset_error()
        return {}

    async def _act_saver_forget(
        self, _ctx: ActionContext, params: SaverForgetParams
    ) -> dict[str, Any]:
        source = params.source
        ui = self.store.ui
        if source == APPLE_ID:
            patch: dict[str, Any] = {"saver_apple": False}
        elif source in ui["saver_sources"]:
            patch = {"saver_sources": [u for u in ui["saver_sources"] if u != source]}
        else:
            raise ActionError(proto.ErrorCode.BAD_PARAMS, "Cette source n'est pas dans la liste.")
        self._persist(
            lambda: self.store.update_ui(patch)
        )  # d'abord les réglages : plus rien ne la retélécharge
        self.push_local("ui")
        removed = await asyncio.to_thread(self.aerial.forget, source)
        return {"removed": removed}

    async def _act_markets_set(
        self, _ctx: ActionContext, params: MarketsSetParams
    ) -> dict[str, Any]:
        patch = params.model_dump(exclude_none=True)
        result: dict[str, Any] = {}

        def apply() -> None:
            result.update(self.store.update_markets(patch))

        self._persist(apply)
        self.markets.watchlist_changed()
        self.push_local("markets")
        self.push_local("dock")  # le résumé des Réglages
        return result

    async def _provider_call(self, call: Any) -> Any:
        """Traduit les échecs d'un fournisseur en réponses lisibles à l'écran."""
        try:
            return await call
        except NotFound as exc:
            raise ActionError("not_found", exc.message) from None
        except ProviderError as exc:
            raise ActionError(proto.ErrorCode.UNAVAILABLE, exc.message) from None
        except ValidationFailure as exc:
            raise ActionError(proto.ErrorCode.BAD_PARAMS, str(exc)) from None

    async def _act_markets_check(
        self, _ctx: ActionContext, params: MarketsCheckParams
    ) -> dict[str, Any]:
        return await self._provider_call(self.markets.check(params.kind, params.symbol))

    async def _act_markets_search(
        self, _ctx: ActionContext, params: MarketsSearchParams
    ) -> dict[str, Any]:
        return await self._provider_call(self.markets.search(params.kind, params.query))

    async def _act_markets_refresh(self, _ctx: ActionContext, _params: NoParams) -> dict[str, Any]:
        return await self._provider_call(self.markets.refresh())

    async def _act_markets_diagnose(self, _ctx: ActionContext, _params: NoParams) -> dict[str, Any]:
        """Teste la connexion de la Bourse (heure, noms, IPv4/IPv6, chaque fournisseur) et dit ce qui coince."""
        return await diagnose(self.markets.providers, self.markets.currency)

    async def _act_markets_history(
        self, _ctx: ActionContext, params: MarketsHistoryParams
    ) -> dict[str, Any]:
        return await self._provider_call(self.markets.history(params.id, params.range))


# --- HTTP / WebSocket -----------------------------------------------------------------------


def host_allowed(host_header: str | None, config: DockConfig) -> bool:
    """Contre le « DNS rebinding » : en local seul 127.0.0.1 / localhost sont des hôtes valides."""
    if not config.loopback_only:
        return True
    if not host_header:
        return False
    name, sep, port = host_header.rpartition(":")
    hostname = name if sep and port.isdigit() else host_header
    return hostname.lower() in LOOPBACK_NAMES


def origin_allowed(origin: str | None, host_header: str | None) -> bool:
    """L'origine doit être l'interface elle-même : aucun autre site ne pilote le dock."""
    return origin is None or (host_header is not None and origin == f"http://{host_header}")


class SecurityMiddleware:
    def __init__(self, app: ASGIApp, config: DockConfig):
        self.app = app
        self.config = config

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return
        headers = {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope["headers"]}
        host = headers.get("host")
        if not host_allowed(host, self.config) or not origin_allowed(headers.get("origin"), host):
            if scope["type"] == "websocket":
                await receive()
                await send({"type": "websocket.close", "code": 1008})
            else:
                await send(
                    {
                        "type": "http.response.start",
                        "status": 421,
                        "headers": [(b"content-length", b"0")],
                    }
                )
                await send({"type": "http.response.body", "body": b""})
            return

        async def send_with_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                extra = [
                    (b"content-security-policy", CSP.encode()),
                    (b"x-content-type-options", b"nosniff"),
                    (b"referrer-policy", b"no-referrer"),
                ]
                message = {**message, "headers": [*message.get("headers", []), *extra]}
            await send(message)

        await self.app(scope, receive, send_with_headers)


class WebFiles(StaticFiles):
    """Fichiers de l'interface : les ressources hachées se mettent en cache, la page jamais."""

    async def get_response(self, path: str, scope: Scope):  # type: ignore[override]
        response = await super().get_response(path, scope)
        if path.startswith("assets/"):
            response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
        else:
            response.headers["Cache-Control"] = "no-cache"
        return response


MISSING_WEB_PAGE = (
    "<!doctype html><meta charset=utf-8><title>MyDock</title>"
    "<body style='font:20px sans-serif;background:#0b0e14;color:#eef2f8;padding:2em'>"
    "<h1>Interface non installée</h1><p>Le dossier de l'interface est introuvable : "
    "{path}</p><p>Compile-la avec <code>make web</code> puis relance le dock.</p>"
)


def create_app(server: DockServer) -> FastAPI:
    config = server.config

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        server.start()
        try:
            yield
        finally:
            await server.stop()

    app = FastAPI(
        title="MyDock dock", docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan
    )
    app.add_middleware(SecurityMiddleware, config=config)

    @app.get("/api/health")
    async def health() -> JSONResponse:
        return JSONResponse(
            {"ok": True, "version": __version__, "link": str(server.link.status.state)},
            headers={"Cache-Control": "no-store"},
        )

    @app.get("/api/screen")
    async def screen() -> JSONResponse:
        """La durée d'inactivité choisie dans les Réglages : le script de veille de l'écran la lit."""
        # Pendant une lecture l'écran reste allumé : la veille attendrait que personne ne touche l'écran.
        sleep_after = 0 if server.player.playing else server.store.ui["screen_sleep"]
        return JSONResponse({"sleep_after": sleep_after}, headers={"Cache-Control": "no-store"})

    @app.get("/api/screensaver")
    async def saver_list() -> JSONResponse:
        """Les vidéos que l'économiseur d'écran peut montrer (celles du dossier du Pi)."""
        found = await asyncio.to_thread(screensaver.videos, config.saver_dir)
        return JSONResponse(
            {
                "videos": [{"name": v.name, "size": v.size, "mime": v.mime} for v in found],
                "aerial": await asyncio.to_thread(server.aerial.status),
            },
            headers={"Cache-Control": "no-store"},
        )

    @app.get("/screensaver/{name}")
    async def saver_file(name: str) -> FileResponse:
        """Une vidéo de la liste (jamais un chemin : le nom doit être l'un de ceux du dossier). Gère les « Range »."""
        video = await asyncio.to_thread(screensaver.find, config.saver_dir, name)
        if video is None:
            raise HTTPException(status_code=404)
        return FileResponse(
            video.path, media_type=video.mime, headers={"Cache-Control": "private, max-age=600"}
        )

    @app.websocket("/ws")
    async def ws_endpoint(ws: WebSocket) -> None:
        await ws.accept()
        await _serve_browser(server, ws)

    @app.websocket("/ws/player")
    async def ws_player(ws: WebSocket) -> None:
        """L'assistant de lecture de l'écran (jamais un navigateur : ni Origin, ni autre adresse que locale)."""
        host = ws.client.host if ws.client else ""
        try:
            local = ipaddress.ip_address(host).is_loopback
        except ValueError:
            local = False
        if not local or "origin" in ws.headers:
            await ws.close(code=1008)
            return
        await ws.accept()
        await _serve_player(server, ws)

    web_dir = config.web_dir
    if web_dir is not None and (Path(web_dir) / "index.html").is_file():
        app.mount("/", WebFiles(directory=str(web_dir), html=True), name="web")
    else:

        @app.get("/", response_class=HTMLResponse)
        async def missing() -> HTMLResponse:
            return HTMLResponse(MISSING_WEB_PAGE.format(path=web_dir), status_code=503)

    return app


MAX_PLAYER_MESSAGE = 16 * 1024


async def _serve_player(server: DockServer, ws: WebSocket) -> None:
    conn = HelperConn()
    server.player.attach(conn)

    async def write() -> None:
        while (message := await conn.outbox.get()) is not None:
            await ws.send_text(json.dumps(message, separators=(",", ":")))

    async def read() -> None:
        while True:
            incoming = await ws.receive()
            if incoming["type"] == "websocket.disconnect":
                return
            raw = incoming.get("text")
            if not isinstance(raw, str) or len(raw) > MAX_PLAYER_MESSAGE:
                continue
            try:
                message = json.loads(raw)
            except ValueError:
                continue
            if isinstance(message, dict):
                server.player.on_message(conn, message)

    writer, reader = asyncio.create_task(write()), asyncio.create_task(read())
    try:
        await asyncio.wait({writer, reader}, return_when=asyncio.FIRST_COMPLETED)
    finally:
        for task in (reader, writer):
            task.cancel()
        await asyncio.gather(reader, writer, return_exceptions=True)
        server.player.detach(conn)
        with contextlib.suppress(Exception):
            await ws.close()


async def _serve_browser(server: DockServer, ws: WebSocket) -> None:
    peer = ws.client.host if ws.client else "?"
    conn = BrowserConn(peer)
    inflight: set[asyncio.Task[None]] = set()
    server.browsers.add(conn)
    log.debug("Navigateur connecté (%s)", conn.id)

    conn.outbox.push_control(proto.dumps(proto.hello({"role": "dock", "version": __version__})))
    for topic in proto.DOCK_TOPICS:
        conn.outbox.push_snapshot(topic, server.local_text(topic))

    def handle(msg: dict[str, Any]) -> None:
        kind = msg["t"]
        if kind == "sub":
            server.subscribe(conn, proto.parse_topics(msg))
        elif kind == "unsub":
            server.unsubscribe(conn, proto.parse_topics(msg))
        elif kind == "act":
            act = proto.parse_act(msg)
            if len(inflight) >= MAX_INFLIGHT_ACTIONS:
                reply = proto.ack_error(act.id, proto.ErrorCode.FAILED, "Trop d'actions en cours")
                conn.outbox.push_control(proto.dumps(reply))
                return
            task = asyncio.create_task(server.run_act(conn, act))
            inflight.add(task)
            task.add_done_callback(inflight.discard)
        elif kind == "ping":
            n = msg.get("n")
            n = n if isinstance(n, int | str) and len(str(n)) <= 32 else None
            conn.outbox.push_control(proto.dumps(proto.pong(n, int(time.time() * 1000))))
        else:
            raise proto.ProtocolError(proto.ErrorCode.UNKNOWN_TYPE, "Type de message inconnu")

    async def receive_loop() -> None:
        bad = 0
        while True:
            message = await ws.receive()
            if message["type"] == "websocket.disconnect":
                return
            try:
                raw = message.get("text")
                if raw is None:
                    raise proto.ProtocolError(proto.ErrorCode.BAD_MESSAGE, "Texte uniquement")
                handle(proto.loads(raw))
                bad = 0
            except proto.ProtocolError as exc:
                bad += 1
                conn.outbox.push_control(proto.dumps(proto.err(exc.code, exc.message)))
                if bad >= MAX_CONSECUTIVE_BAD_MESSAGES:
                    return

    writer = asyncio.create_task(conn.outbox.run(ws.send_text))
    reader = asyncio.create_task(receive_loop())
    try:
        done, _ = await asyncio.wait({writer, reader}, return_when=asyncio.FIRST_COMPLETED)
        for task in done:
            exc = task.exception()
            if exc and not isinstance(exc, WebSocketDisconnect | ConnectionError | RuntimeError):
                log.error("Erreur sur la connexion navigateur", exc_info=exc)
    finally:
        conn.outbox.close()
        everything = [reader, writer, *inflight]
        for task in everything:
            task.cancel()
        await asyncio.gather(*everything, return_exceptions=True)
        server.detach(conn)
        try:
            await ws.close()
        except Exception:
            pass
