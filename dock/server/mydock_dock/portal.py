"""Le portail : la page web que le Pi sert sur le réseau local, d'où un PC s'installe et s'associe.

C'est tout ce que le Pi montre au réseau. Une personne ouvre `http://<le Pi>/` depuis le PC :

  1. elle télécharge le paquet du PC (`mydock-agent_<version>_all.deb`, embarqué dans le paquet du Pi) ;
  2. elle l'installe (`sudo apt install ./…`) ;
  3. `mydock-agent setup --pi <le Pi>` associe le PC au dock avec le code affiché sur l'écran du Pi :
     plus de jeton à recopier (voir pairing.py).

Le portail est **un autre serveur** que celui de l'interface de pilotage : il ne sert jamais
l'interface, jamais le WebSocket, jamais la configuration, et n'a rien d'autre à dire que :

    GET  /                       la page d'installation (autonome, sans ressource externe)
    GET  /clients/<fichier>      un paquet de la liste (jamais un chemin construit à partir de l'adresse)
    GET  /api/portal             l'état public : version, PC associé ou non, paquets proposés
    POST /api/pair               l'association par code (limitée, voir pairing.py)
    GET  /jeux                   la page d'envoi de jeux d'arcade (Retrogame), protégée par un code de l'écran
    POST /api/retro/login        le code de l'écran du dock, contre un jeton d'envoi (voir retro_upload.py)
    POST /api/retro/upload       un jeu (.zip ou .7z) : écrit dans le dossier des ROMs, après le contrôle du nom et de l'archive

Tout le reste répond 404. Aucune page n'affiche un en-tête de la requête (le nom du Pi et ses
adresses viennent du Pi lui-même), la politique de sécurité du contenu n'autorise que le script et
le style de la page, et les corps de requête sont plafonnés.
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import errno
import hashlib
import html
import json
import logging
import socket
from collections.abc import Iterator
from typing import TYPE_CHECKING, Any

import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from mydock_common.auth import is_valid_token_format
from mydock_dock import __version__
from mydock_dock.clients import DEB_RE, Client
from mydock_dock.netinfo import is_lan_candidate, local_address_towards, local_ipv4_addresses
from mydock_dock.pairing import PairingRefused
from mydock_dock.retro_upload import (
    MAX_CONCURRENT,
    UPLOAD_CSP,
    UploadRefused,
    save_rom,
)
from mydock_dock.retro_upload import (
    render_page as render_upload_page,
)

if TYPE_CHECKING:
    from mydock_dock.app import DockServer

log = logging.getLogger(__name__)
media_log = logging.getLogger("mydock_dock.media")  # dock.log

MAX_BODY_BYTES = 4096
MAX_NAME_LENGTH = 60
DEB_MEDIA_TYPE = "application/vnd.debian.binary-package"

# --- la page ---------------------------------------------------------------------------------

STYLE = """
:root{color-scheme:dark;--bg:#0b0e14;--surface:#131923;--surface2:#1a2231;--line:#2a3446;--text:#eef2f8;--text2:#aab6ca;--accent:#5ab4ff;--ink:#04121f;--good:#3ad29f;--warn:#f5b74a}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--text);font:18px/1.5 system-ui,-apple-system,"Segoe UI",Roboto,"Noto Sans","DejaVu Sans",sans-serif}
main{max-width:760px;margin:0 auto;padding:28px 16px 48px}
h1{margin:0;font-size:34px}
h2{margin:0 0 8px;font-size:22px;display:flex;gap:12px;align-items:center}
p{margin:8px 0}
.sub{color:var(--text2);margin:4px 0 14px}
.chip{display:inline-block;padding:4px 14px;border-radius:999px;background:var(--surface2);color:var(--text2);font-size:16px;font-weight:600}
.chip.good{background:#0f2f27;color:var(--good)}
.chip.warn{background:#3a2c10;color:var(--warn)}
ol{list-style:none;margin:22px 0 0;padding:0;display:grid;gap:14px}
li{background:var(--surface);border:1px solid var(--line);border-radius:14px;padding:18px 20px}
.n{display:inline-grid;place-items:center;width:34px;height:34px;border-radius:50%;background:var(--accent);color:var(--ink);font-weight:700}
.btn{display:inline-block;margin:6px 0;padding:13px 22px;border-radius:12px;background:var(--accent);color:var(--ink);font-weight:700;text-decoration:none;border:0;font-size:18px;cursor:pointer}
.btn.small{padding:6px 14px;background:var(--surface2);color:var(--text);font-size:16px;font-weight:600;margin:0}
small,.dim{color:var(--text2)}
pre{margin:8px 0;padding:12px 14px;background:#0b0e14;border:1px solid var(--line);border-radius:10px;overflow-x:auto}
code{font:16px/1.4 ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;word-break:break-all}
pre code{word-break:normal}
.row{display:flex;gap:10px;align-items:center;flex-wrap:wrap}
details{margin-top:8px;color:var(--text2)}
summary{cursor:pointer}
.warnbox{border-color:var(--warn)}
footer{margin-top:28px;color:var(--text2);font-size:16px}
"""

SCRIPT = """
(function(){
  function copy(text){
    if(navigator.clipboard&&window.isSecureContext){return navigator.clipboard.writeText(text)}
    return new Promise(function(ok,ko){
      var a=document.createElement('textarea');a.value=text;a.style.position='fixed';a.style.opacity='0';
      document.body.appendChild(a);a.select();
      try{document.execCommand('copy')?ok():ko()}catch(e){ko(e)}
      document.body.removeChild(a)
    })
  }
  document.addEventListener('click',function(e){
    var b=e.target.closest('[data-copy]');if(!b)return;
    var el=document.getElementById(b.getAttribute('data-copy'));if(!el)return;
    copy(el.textContent).then(function(){b.textContent='Copié ✔'},function(){b.textContent='Sélectionne et copie'});
    setTimeout(function(){b.textContent='Copier'},1800)
  });
  var chip=document.getElementById('status');
  function show(text,kind){chip.textContent=text;chip.className='chip '+(kind||'')}
  function refresh(){
    fetch('/api/portal',{cache:'no-store'}).then(function(r){return r.json()}).then(function(d){
      if(d.pairing){show('Association en cours : saisis le code affiché sur l’écran du dock','warn')}
      else if(d.paired&&d.connected){show('Un PC est associé et connecté','good')}
      else if(d.paired){show('Un PC est associé (hors ligne pour l’instant)','warn')}
      else{show('Aucun PC associé pour l’instant','')}
    }).catch(function(){show('Le dock ne répond plus','warn')})
  }
  refresh();setInterval(refresh,5000)
})();
"""


def _csp_hash(text: str) -> str:
    return "'sha256-" + base64.b64encode(hashlib.sha256(text.encode()).digest()).decode() + "'"


#: Rien d'autre que ce style et ce script, rien de l'extérieur, aucune forme, aucun cadre.
PORTAL_CSP = (
    "default-src 'none'; "
    f"style-src {_csp_hash(STYLE)}; script-src {_csp_hash(SCRIPT)}; "
    "connect-src 'self'; img-src data:; base-uri 'none'; form-action 'none'; "
    "frame-ancestors 'none'"
)
SECURITY_HEADERS = {
    "Content-Security-Policy": PORTAL_CSP,
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
}

PAGE = """<!doctype html>
<html lang="fr">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>MyDock - installer sur un PC</title>
<style>{style}</style>
</head>
<body>
<main>
<h1>MyDock</h1>
<p class="sub">Le dock tactile <b>{hostname}</b> pilote ton PC. Trois gestes pour l'installer.</p>
<span id="status" class="chip">…</span>
<ol>
{steps}
</ol>
<footer>
<p>Cette page est servie par le dock lui-même, sur ton réseau local : rien ne sort sur Internet.
Pour un autre type de PC, voir le README du projet.</p>
</footer>
</main>
<script>{script}</script>
</body>
</html>
"""


def human_size(size: int) -> str:
    if size < 1024 * 1024:
        return f"{max(1, round(size / 1024))} Ko"
    return f"{size / (1024 * 1024):.1f} Mo".replace(".", ",")


def setup_command(address: str, port: int | None) -> str:
    """La commande d'association à lancer sur le PC, avec l'adresse de CE Pi."""
    extra = f" --portal-port {port}" if port not in (None, 80) else ""
    return f"mydock-agent setup --pi {address}{extra} --open-firewall"


def render_page(*, hostname: str, address: str, port: int | None, client: Client | None) -> str:
    """La page d'installation. Tout ce qui y est écrit est échappé ; rien ne vient de la requête."""
    e = html.escape
    if client is None:
        first = (
            '<li class="warnbox"><h2><span class="n">1</span> Télécharger l\'agent</h2>'
            "<p>Aucun paquet pour PC n'est installé sur ce dock. Réinstalle le paquet "
            "<code>mydock</code> du Pi : il embarque celui du PC.</p></li>"
        )
        install = "sudo apt install ./mydock-agent_*_all.deb"
    else:
        first = (
            '<li><h2><span class="n">1</span> Télécharger l\'agent</h2>'
            f"<p>Pour {e(client.label)}.</p>"
            f'<a class="btn" href="/clients/{e(client.file)}" download>'
            f"⬇ Télécharger {e(client.file)}</a>"
            f"<p><small>Version {e(client.version)} · {human_size(client.size)}</small></p>"
            "<details><summary>Vérifier l'empreinte</summary>"
            f"<p>SHA-256 : <code>{e(client.sha256)}</code></p></details></li>"
        )
        install = f"sudo apt install ./{client.file}"
    steps = [
        first,
        '<li><h2><span class="n">2</span> Installer</h2>'
        "<p>Dans un terminal, depuis le dossier de téléchargement :</p>"
        f'<pre><code id="c1">{e(install)}</code></pre>'
        '<button class="btn small" data-copy="c1">Copier</button></li>',
        '<li><h2><span class="n">3</span> Associer ce PC au dock</h2>'
        "<p>Dans un terminal <b>de ta session</b>, sans <code>sudo</code> :</p>"
        f'<pre><code id="c2">{e(setup_command(address, port))}</code></pre>'
        '<button class="btn small" data-copy="c2">Copier</button>'
        "<p>L'écran du dock affiche un <b>code de huit caractères</b> (Réglages → « Associer un PC » "
        "s'il n'y est pas) : tape-le quand la commande le demande.</p></li>",
    ]
    return PAGE.format(style=STYLE, script=SCRIPT, hostname=e(hostname), steps="\n".join(steps))


# --- les mesures de sécurité --------------------------------------------------------------------


class PortalSecurity:
    """Aucun WebSocket, en-têtes de sécurité partout, une seule famille de méthodes."""

    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "websocket":
            await receive()
            await send({"type": "websocket.close", "code": 1008})
            return
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def send_secured(message: Message) -> None:
            if message["type"] == "http.response.start":
                present = {k.lower() for k, _ in message.get("headers", [])}
                extra = [
                    (k.lower().encode(), v.encode())
                    for k, v in SECURITY_HEADERS.items()
                    if k.lower().encode() not in present
                ]
                message = {**message, "headers": [*message.get("headers", []), *extra]}
            await send(message)

        await self.app(scope, receive, send_secured)


def _error(status: int, code: str, message: str, **extra: Any) -> JSONResponse:
    return JSONResponse(
        {"ok": False, "error": code, "message": message, **extra},
        status_code=status,
        headers={"Cache-Control": "no-store"},
    )


async def _read_limited(request: Request) -> bytes | None:
    """Le corps de la requête, ou None s'il dépasse le plafond."""
    declared = request.headers.get("content-length")
    if declared is not None and (not declared.isdigit() or int(declared) > MAX_BODY_BYTES):
        return None
    body = b""
    async for chunk in request.stream():
        body += chunk
        if len(body) > MAX_BODY_BYTES:
            return None
    return body


async def _discard(request: Request, limit: int = 262_144, seconds: float = 2.0) -> None:
    """Lit et jette le début du corps (256 Ko au plus, 2 s au plus) d'une demande qu'on refuse."""
    seen = 0
    with contextlib.suppress(Exception):
        async with asyncio.timeout(seconds):
            async for chunk in request.stream():
                seen += len(chunk)
                if seen > limit:
                    break


def clean_name(value: object) -> str:
    """Le nom que le PC se donne : du texte affichable, de longueur raisonnable."""
    text = value if isinstance(value, str) else ""
    text = "".join(ch for ch in text if ch.isprintable() and ch not in "<>&\"'`")
    return " ".join(text.split())[:MAX_NAME_LENGTH] or "PC"


def create_portal_app(server: DockServer) -> FastAPI:
    app = FastAPI(
        title="MyDock portail",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        lifespan=None,
        redirect_slashes=False,  # une adresse, un contenu : « /clients/x/ » n'est pas « /clients/x »
    )
    app.add_middleware(PortalSecurity)

    def public_state() -> dict[str, Any]:
        config = server.config
        return {
            "ok": True,
            "app": "mydock",
            "version": __version__,
            "hostname": socket.gethostname(),
            "paired": bool(config.agent_host and config.token),
            "connected": server.link.connected,
            "pairing": server.pairing.is_open(),
            "clients": [c.public() for c in server.clients.scan()],
        }

    @app.api_route("/", methods=["GET", "HEAD"])
    async def index(request: Request) -> Response:
        peer = request.client.host if request.client else ""
        # L'adresse de CE Pi telle que ce PC peut la joindre ; jamais 127.0.0.1 (le navigateur du Pi).
        toward = local_address_towards(peer) if peer else None
        address = (toward if toward and is_lan_candidate(toward) else None) or next(
            iter(local_ipv4_addresses()), "ADRESSE_DU_PI"
        )
        page = render_page(
            hostname=socket.gethostname(),
            address=address,
            port=server.portal.port,
            client=server.clients.latest(),
        )
        return HTMLResponse(page, headers={"Cache-Control": "no-store"})

    @app.api_route("/clients/{name}", methods=["GET", "HEAD"])
    async def download(name: str) -> Response:
        client = server.clients.get(name) if DEB_RE.fullmatch(name) else None
        if client is None:
            return Response(status_code=404)
        return FileResponse(
            client.path,
            media_type=DEB_MEDIA_TYPE,
            filename=client.file,
            headers={"Cache-Control": "no-cache"},
        )

    @app.get("/api/portal")
    async def state() -> JSONResponse:
        return JSONResponse(public_state(), headers={"Cache-Control": "no-store"})

    @app.post("/api/pair")
    async def pair(request: Request) -> JSONResponse:
        peer = request.client.host if request.client else "?"
        if not request.headers.get("content-type", "").lower().startswith("application/json"):
            return _error(415, "bad_request", "La demande doit être du JSON.")
        raw = await _read_limited(request)
        if raw is None:
            return _error(413, "too_large", "Demande trop volumineuse.")
        try:
            data = json.loads(raw)
        except ValueError:
            data = None
        if not isinstance(data, dict):
            return _error(400, "bad_request", "Demande illisible.")
        try:
            server.pairing.check(peer, data.get("code"))
        except PairingRefused as refusal:
            log.warning("Association refusée pour %s : %s", peer, refusal.code)
            return _error(refusal.status, refusal.code, refusal.message, **refusal.extra)
        # Le code est bon : le reste de la demande doit l'être aussi (sans consommer d'essai).
        token, port = data.get("token"), data.get("port")
        if (
            not is_valid_token_format(token)
            or isinstance(port, bool)
            or not isinstance(port, int)
            or not 1 <= port <= 65535
        ):
            return _error(400, "bad_request", "Jeton ou port invalide.")
        try:
            result = await server.complete_pairing(
                peer, port, str(token), clean_name(data.get("name"))
            )
        except PairingRefused as refusal:
            log.warning("Association impossible pour %s : %s", peer, refusal.code)
            return _error(refusal.status, refusal.code, refusal.message, **refusal.extra)
        return JSONResponse(result, headers={"Cache-Control": "no-store"})

    # --- Retrogame : envoyer des jeux depuis le navigateur du PC -------------------------------------

    uploading = asyncio.Semaphore(MAX_CONCURRENT)

    @app.api_route("/jeux", methods=["GET", "HEAD"])
    async def retro_page() -> Response:
        return HTMLResponse(
            render_upload_page(socket.gethostname()),
            headers={"Cache-Control": "no-store", "Content-Security-Policy": UPLOAD_CSP},
        )

    @app.post("/api/retro/login")
    async def retro_login(request: Request) -> JSONResponse:
        peer = request.client.host if request.client else "?"
        if not request.headers.get("content-type", "").lower().startswith("application/json"):
            return _error(415, "bad_request", "La demande doit être du JSON.")
        raw = await _read_limited(request)
        if raw is None:
            return _error(413, "too_large", "Demande trop volumineuse.")
        try:
            data = json.loads(raw)
        except ValueError:
            data = None
        if not isinstance(data, dict):
            return _error(400, "bad_request", "Demande illisible.")
        try:
            token = server.uploads.login(peer, data.get("code"))
        except UploadRefused as refusal:
            log.warning("Envoi de jeux refusé pour %s : %s", peer, refusal.code)
            return _error(refusal.status, refusal.code, refusal.message)
        return JSONResponse({"ok": True, "token": token}, headers={"Cache-Control": "no-store"})

    @app.post("/api/retro/upload")
    async def retro_upload(request: Request) -> JSONResponse:
        peer = request.client.host if request.client else "?"
        name = request.query_params.get("name")
        declared = request.headers.get("content-length")
        size = int(declared) if declared and declared.isdigit() else None
        try:
            server.uploads.authorize(peer, request.headers.get("x-upload-token"))
            if uploading.locked():
                raise UploadRefused(
                    "busy", "Un autre envoi est en cours : patiente un instant.", status=429
                )
            async with uploading:
                server.uploads.reserve(size or 0)
                saved = await save_rom(request.stream(), name, size, server.config.roms_path)
        except UploadRefused as refusal:
            media_log.warning("Envoi de jeu refusé (%s) : %s", peer, refusal.code)
            await _discard(
                request
            )  # un petit corps est lu : fermer sur des octets non lus ferait perdre la réponse au navigateur
            reply = _error(refusal.status, refusal.code, refusal.message)
            reply.headers["Connection"] = "close"  # le reste d'un gros fichier n'est pas lu
            return reply
        server.uploads.done(saved.size)
        server.retro.refresh()
        media_log.info("Jeu reçu : %s (%d octets) depuis %s", saved.name, saved.size, peer)
        return JSONResponse(
            {"ok": True, "name": saved.name, "size": saved.size},
            headers={"Cache-Control": "no-store"},
        )

    return app


# --- le serveur du portail ------------------------------------------------------------------------


class _PortalServer(uvicorn.Server):
    """Un uvicorn qui ne touche pas aux signaux : l'arrêt vient du dock, pas d'un Ctrl-C."""

    def install_signal_handlers(self) -> None:  # anciennes versions d'uvicorn
        pass

    @contextlib.contextmanager
    def capture_signals(self) -> Iterator[None]:
        yield


def bind_socket(host: str, port: int) -> socket.socket:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind((host, port))
        sock.listen(64)
        sock.setblocking(False)
    except OSError:
        sock.close()
        raise
    return sock


def explain_bind_error(exc: OSError, port: int) -> str:
    if exc.errno in (errno.EACCES, errno.EPERM):
        return (
            f"Droit refusé pour le port {port} : le service doit pouvoir ouvrir un port "
            "inférieur à 1024 (c'est le cas avec le paquet mydock), ou choisis un port plus "
            "élevé dans [portal] de dock.toml."
        )
    if exc.errno == errno.EADDRINUSE:
        return (
            f"Le port {port} est déjà pris par un autre programme : change-le dans [portal] "
            "de dock.toml."
        )
    return f"Impossible d'ouvrir le port {port} ({exc.strerror or exc.__class__.__name__})."


class Portal:
    """Démarre et arrête le portail avec le dock. Un portail qui ne peut pas démarrer ne gêne rien."""

    def __init__(self, server: DockServer):
        self._server = server
        self.port: int | None = None
        self.error: str | None = None
        #: Pour les essais : écouter ailleurs que dans la configuration (qui, elle, reste valide).
        self.bind_host: str | None = None
        self.bind_port: int | None = None
        self._uvicorn: _PortalServer | None = None
        self._task: asyncio.Task[None] | None = None
        self._sock: socket.socket | None = None

    @property
    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    def start(self) -> None:
        config = self._server.config
        if not config.portal_enabled:
            return
        host = self.bind_host if self.bind_host is not None else config.portal_host
        wanted = self.bind_port if self.bind_port is not None else config.portal_port
        try:
            self._sock = bind_socket(host, wanted)
        except OSError as exc:
            self.error = explain_bind_error(exc, wanted)
            log.error("Portail indisponible : %s", self.error)
            return
        self.port = self._sock.getsockname()[1]
        self._uvicorn = _PortalServer(
            uvicorn.Config(
                create_portal_app(self._server),
                log_config=None,
                log_level="warning",
                access_log=False,
                server_header=False,
                date_header=False,
                proxy_headers=False,  # l'adresse du PC est celle de la connexion, jamais un en-tête
                ws="none",
                lifespan="off",
                limit_concurrency=32,
                timeout_keep_alive=5,
            )
        )
        self._task = asyncio.create_task(self._serve(), name="portal")
        log.info("Portail sur http://%s:%d", host, self.port)

    async def _serve(self) -> None:
        assert self._uvicorn is not None
        assert self._sock is not None
        try:
            await self._uvicorn.serve(sockets=[self._sock])
        except Exception:
            log.exception("Le portail s'est arrêté")
            self.error = "Le portail s'est arrêté : voir le journal (journalctl -u mydock-dock -e)."

    async def stop(self) -> None:
        if self._uvicorn is not None:
            self._uvicorn.should_exit = True
        if self._task is not None:
            try:
                await asyncio.wait_for(self._task, 5)
            except (TimeoutError, asyncio.CancelledError):
                self._task.cancel()
        if self._sock is not None:
            self._sock.close()
        self._task = self._uvicorn = self._sock = None
