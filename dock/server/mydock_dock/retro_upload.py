"""L'envoi des jeux depuis le navigateur du PC : la page `/jeux` du portail, protégée par un code affiché sur le dock.

Le portail est la seule porte du Pi vers le réseau local, et jusqu'ici il ne faisait rien d'écrit. Envoyer des ROMs, c'est
**écrire des fichiers sur le Pi** depuis le réseau. Deux façons d'y être autorisé :

  * **le PC associé au dock** (celui dont l'adresse IP est enregistrée à l'association : il a déjà prouvé qu'il était devant le
    dock) envoie **sans rien saisir** : la page `/jeux` s'ouvre sur le choix des fichiers (réglable : Réglages → Retrogame) ;
  * **tout autre appareil** doit être devant le dock : sur l'écran, « Envoyer des jeux » ouvre une session et affiche un code
    de huit caractères ; la page le demande, puis accepte des fichiers. La session dure un quart d'heure et se ferme d'elle-même
    après cinq codes faux.

Dans les deux cas, ce qu'on peut écrire est borné de la même façon (voir plus bas), et la session du PC associé a ses propres
plafonds (15 minutes, 3 Go, 300 fichiers).

Ce que la page peut écrire est très borné :

  * un fichier `nom.zip` ou `nom.7z` dont le nom est un nom de jeu valide (`[a-z0-9_]`, 24 caractères au plus, mis en
    minuscules) : jamais un chemin, jamais un autre type de fichier ;
  * une vraie archive (signature ZIP ou 7z, structure ZIP lisible), de 400 Mo au plus, 3 Go au plus par session, et jamais
    en laissant moins de 500 Mo libres sur la carte ;
  * écrit d'abord dans un fichier provisoire du même dossier, puis renommé d'un coup : jamais un jeu à moitié écrit.

Rien n'est extrait, rien n'est exécuté : MAME lit l'archive en place, et vérifie lui-même qu'elle est complète.
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import hashlib
import hmac
import html
import logging
import os
import re
import secrets
import shutil
import time
import zipfile
from collections import deque
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from mydock_dock.pairing import ALPHABET, CODE_LENGTH, format_code, normalize_code

log = logging.getLogger(__name__)

SESSION_SECONDS = 900.0
MAX_WRONG = 5
THROTTLE_WINDOW = 60.0
THROTTLE_MAX = 20  # demandes de connexion par minute et par adresse
MAX_TRACKED_PEERS = 256
MAX_TOKENS = 8
MAX_FILE_BYTES = 400 * 1024 * 1024
MAX_SESSION_BYTES = 3 * 1024 * 1024 * 1024
MAX_FILES = 300
FREE_RESERVE = 500 * 1024 * 1024
CHUNK_TIMEOUT = 30.0  # un envoi qui ne donne rien pendant ce temps est abandonné
MAX_CONCURRENT = 2
NAME_RE = re.compile(r"^([a-z0-9_]{1,24})\.(zip|7z)$")
ZIP_MAGIC = (b"PK\x03\x04", b"PK\x05\x06")
SEVENZIP_MAGIC = b"7z\xbc\xaf\x27\x1c"
MAX_ZIP_ENTRIES = 20000


class UploadRefused(Exception):
    """Un envoi ou une connexion refusés, avec un message affichable sur le PC."""

    def __init__(self, code: str, message: str, *, status: int = 400) -> None:
        super().__init__(message)
        self.code, self.message, self.status = code, message, status


@dataclass
class _Session:
    code: str | None  # None : la session du PC associé, qui n'a pas de code
    expires_at: float
    expires_ms: int
    wrong: int = 0
    tokens: dict[str, str] = field(default_factory=dict)  # jeton -> adresse du PC
    files: int = 0
    total: int = 0


class UploadSessions:
    """Au plus une session ouverte à la fois ; l'ouvrir de nouveau change le code et annule les connexions."""

    def __init__(
        self,
        *,
        on_change: Callable[[], None] = lambda: None,
        clock: Callable[[], float] = time.monotonic,
        wall: Callable[[], float] = time.time,
        session_seconds: float = SESSION_SECONDS,
        max_wrong: int = MAX_WRONG,
        choose: Callable[[str], str] = secrets.choice,
    ) -> None:
        self._on_change, self._clock, self._wall = on_change, clock, wall
        self._seconds, self._max_wrong, self._choose = session_seconds, max_wrong, choose
        self._session: _Session | None = None
        self._trusted: dict[str, _Session] = {}  # la session du PC associé, par adresse (sans code)
        self._hits: dict[str, deque[float]] = {}
        self.last: dict[str, Any] | None = None

    # --- la session (côté écran du dock) --------------------------------------------------------

    def start(self) -> None:
        now = self._clock()
        self._session = _Session(
            code="".join(self._choose(ALPHABET) for _ in range(CODE_LENGTH)),
            expires_at=now + self._seconds,
            expires_ms=int((self._wall() + self._seconds) * 1000),
        )
        self.last = None
        self._on_change()

    def stop(self, reason: str = "closed") -> None:
        if self._session is None:
            return
        session, self._session = self._session, None
        self.last = {"result": reason, "files": session.files, "at": int(self._wall() * 1000)}
        self._on_change()

    def is_open(self) -> bool:
        session = self._session
        if session is None:
            return False
        if self._clock() >= session.expires_at:
            self.stop("expired")
            return False
        return True

    def info(self) -> dict[str, Any]:
        """L'état pour l'écran du dock (le code n'est jamais envoyé ailleurs)."""
        session = self._session if self.is_open() else None
        if session is None:
            return {"open": False, "last": self.last}
        return {
            "open": True,
            "code": format_code(session.code or ""),
            "expires_at": session.expires_ms,
            "files": session.files,
            "wrong_left": self._max_wrong - session.wrong,
            "last": None,
        }

    # --- ce que le PC présente --------------------------------------------------------------------

    def _throttle(self, peer: str) -> None:
        now = self._clock()
        hits = self._hits.setdefault(peer, deque())
        while hits and now - hits[0] > THROTTLE_WINDOW:
            hits.popleft()
        if len(hits) >= THROTTLE_MAX:
            raise UploadRefused(
                "too_many",
                "Trop de demandes depuis cette adresse : réessaie dans une minute.",
                status=429,
            )
        hits.append(now)
        if len(self._hits) > MAX_TRACKED_PEERS:
            for other in [
                p for p, q in self._hits.items() if not q or now - q[-1] > THROTTLE_WINDOW
            ]:
                del self._hits[other]

    def trusted_session(self, peer: str) -> tuple[str, int]:
        """Le jeton d'envoi du PC associé (`peer`) : sans code. L'appelant a vérifié que `peer` est bien ce PC.

        Renvoie (jeton, secondes restantes). La session de ce PC se réutilise tant qu'elle dure : recharger la page ne
        remet pas à zéro les plafonds de fichiers et d'octets.
        """
        self._throttle(peer)
        now = self._clock()
        for gone in [p for p, s in self._trusted.items() if now >= s.expires_at]:
            del self._trusted[gone]
        session = self._trusted.get(peer)
        if session is None:
            session = self._trusted[peer] = _Session(
                code=None,
                expires_at=now + self._seconds,
                expires_ms=int((self._wall() + self._seconds) * 1000),
            )
            while len(self._trusted) > MAX_TOKENS:
                del self._trusted[next(iter(self._trusted))]
        token = secrets.token_urlsafe(24)
        session.tokens[token] = peer
        while len(session.tokens) > MAX_TOKENS:
            del session.tokens[next(iter(session.tokens))]
        return token, max(0, int(session.expires_at - now))

    def forget_trusted(self) -> None:
        """Le PC associé n'a plus ce droit (réglage changé, autre PC associé) : ses jetons ne valent plus rien."""
        self._trusted.clear()

    def login(self, peer: str, presented: object) -> str:
        """Le code de l'écran est bon : un jeton pour les envois qui suivent (lié à l'adresse du PC). Sinon `UploadRefused`."""
        self._throttle(peer)
        if not self.is_open():
            raise UploadRefused(
                "not_open",
                "Aucun envoi n'est ouvert : sur l'écran du dock, ouvre Divertissement → Retrogame → "
                "« Envoyer des jeux » pour afficher un code.",
                status=409,
            )
        session = self._session
        assert session is not None
        assert session.code is not None
        candidate = normalize_code(presented)
        good = hmac.compare_digest((candidate or "-" * CODE_LENGTH).encode(), session.code.encode())
        if good and candidate is not None:
            token = secrets.token_urlsafe(24)
            session.tokens[token] = peer
            while len(session.tokens) > MAX_TOKENS:
                del session.tokens[next(iter(session.tokens))]
            return token
        session.wrong += 1
        if session.wrong >= self._max_wrong:
            log.warning("Envoi de jeux annulé : trop de codes faux depuis %s", peer)
            self.stop("locked")
            raise UploadRefused(
                "locked",
                "Trop d'essais : l'envoi est annulé. Rouvre « Envoyer des jeux » sur le dock.",
                status=403,
            )
        self._on_change()
        raise UploadRefused("bad_code", "Code incorrect.", status=403)

    def _live(self) -> list[_Session]:
        """Les sessions en cours : celle de l'écran (si ouverte) et celle du PC associé (si elle dure encore)."""
        now = self._clock()
        live = [s for s in self._trusted.values() if now < s.expires_at]
        if self.is_open() and self._session is not None:
            live.append(self._session)
        return live

    def authorize(self, peer: str, token: object) -> _Session:
        """Le jeton donné à ce PC (par le code de l'écran, ou parce qu'il est le PC associé) : sa session, sinon `UploadRefused`."""
        live = self._live()
        if not live:
            raise UploadRefused(
                "not_open", "La session d'envoi est terminée : rouvre-la sur le dock.", status=409
            )
        for session in live:
            known = next(
                (
                    t
                    for t in session.tokens
                    if isinstance(token, str) and hmac.compare_digest(t, token)
                ),
                None,
            )
            if known is not None and session.tokens[known] == peer:
                return session
        raise UploadRefused("unauthorized", "Code à saisir de nouveau.", status=401)

    def reserve(self, session: _Session, size: int) -> None:
        """Compte un fichier de plus dans la session (ou refuse : trop de fichiers, trop d'octets)."""
        if session.files >= MAX_FILES:
            raise UploadRefused(
                "too_many_files", f"{MAX_FILES} fichiers au plus par session d'envoi.", status=429
            )
        if session.total + size > MAX_SESSION_BYTES:
            raise UploadRefused(
                "session_full", "Cette session d'envoi a atteint 3 Go : rouvre-en une.", status=413
            )

    def done(self, session: _Session, size: int) -> None:
        session.files += 1
        session.total += size
        if session is self._session:
            self._on_change()  # l'écran du dock montre « N jeux reçus »


# --- l'écriture d'un fichier ---------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Saved:
    name: str  # le nom du jeu, sans l'extension
    size: int


def clean_filename(value: object) -> tuple[str, str]:
    """`(nom du jeu, extension)` d'un nom de fichier envoyé ; `UploadRefused` s'il n'est pas un nom de jeu."""
    text = value.strip().lower() if isinstance(value, str) else ""
    match = NAME_RE.fullmatch(text)
    if match is None:
        raise UploadRefused(
            "bad_name",
            "Nom de fichier refusé : un jeu s'envoie en « nom.zip » ou « nom.7z » (lettres minuscules, "
            "chiffres et _, 24 caractères au plus), comme MAME l'appelle.",
        )
    return match.group(1), match.group(2)


def _signature_ok(head: bytes, extension: str) -> bool:
    return head.startswith(SEVENZIP_MAGIC) if extension == "7z" else head.startswith(ZIP_MAGIC)


def _check_archive(path: Path, extension: str) -> None:
    with path.open("rb") as handle:
        head = handle.read(8)
    if not _signature_ok(head, extension):
        raise UploadRefused(
            "not_archive", "Ce fichier n'est pas une archive .zip ou .7z valide.", status=415
        )
    if extension == "zip":
        try:
            with zipfile.ZipFile(path) as archive:
                if len(archive.namelist()) > MAX_ZIP_ENTRIES:
                    raise UploadRefused(
                        "not_archive", "Cette archive contient trop de fichiers.", status=415
                    )
        except zipfile.BadZipFile:
            raise UploadRefused(
                "not_archive",
                "Cette archive ZIP est abîmée ou incomplète : renvoie-la.",
                status=415,
            ) from None


async def save_rom(
    chunks: AsyncIterator[bytes],
    filename: object,
    declared: int | None,
    roms_dir: Path,
    *,
    max_bytes: int | None = None,
    reserve: int | None = None,
) -> Saved:
    """Écrit un jeu envoyé dans le dossier des ROMs, ou lève `UploadRefused` sans rien laisser derrière."""
    max_bytes = MAX_FILE_BYTES if max_bytes is None else max_bytes
    reserve = FREE_RESERVE if reserve is None else reserve
    name, extension = clean_filename(filename)
    if declared is None or declared <= 0:
        raise UploadRefused("length_required", "La taille du fichier est requise.", status=411)
    if declared > max_bytes:
        raise UploadRefused(
            "too_large", f"Fichier trop gros ({max_bytes // (1024 * 1024)} Mo au plus).", status=413
        )
    try:
        await asyncio.to_thread(roms_dir.mkdir, parents=True, exist_ok=True)
        free = (await asyncio.to_thread(shutil.disk_usage, roms_dir)).free
    except OSError:
        raise UploadRefused(
            "not_writable", "Le dossier des ROMs n'est pas modifiable par le dock.", status=507
        ) from None
    if free - declared < reserve:
        raise UploadRefused("no_space", "Pas assez de place sur la carte du Pi.", status=507)
    partial = (
        roms_dir / f".envoi-{secrets.token_hex(6)}.part"
    )  # sans suffixe de ROM : MAME et la liste l'ignorent
    written = 0
    try:
        handle = await asyncio.to_thread(partial.open, "xb")
    except OSError:
        raise UploadRefused(
            "not_writable", "Le dossier des ROMs n'est pas modifiable par le dock.", status=507
        ) from None
    try:
        with handle:
            iterator = chunks.__aiter__()
            while True:
                try:
                    chunk = await asyncio.wait_for(iterator.__anext__(), CHUNK_TIMEOUT)
                except StopAsyncIteration:
                    break
                except TimeoutError:
                    raise UploadRefused(
                        "stalled", "L'envoi s'est interrompu.", status=408
                    ) from None
                written += len(chunk)
                if written > declared:
                    raise UploadRefused(
                        "bad_length", "Le fichier est plus gros que annoncé.", status=400
                    )
                await asyncio.to_thread(handle.write, chunk)
        if written != declared:
            raise UploadRefused(
                "incomplete", "Le fichier est arrivé incomplet : renvoie-le.", status=400
            )
        await asyncio.to_thread(_check_archive, partial, extension)
        await asyncio.to_thread(os.chmod, partial, 0o644)
        # Un jeu de même nom (l'autre extension comprise) est remplacé : renvoyer un fichier corrige un fichier abîmé.
        other = roms_dir / f"{name}.{'7z' if extension == 'zip' else 'zip'}"
        await asyncio.to_thread(os.replace, partial, roms_dir / f"{name}.{extension}")
        with contextlib.suppress(OSError):
            await asyncio.to_thread(other.unlink)
    except OSError:
        raise UploadRefused(
            "not_writable", "Le dock n'a pas pu écrire le fichier.", status=507
        ) from None
    finally:
        with contextlib.suppress(OSError):
            partial.unlink()
    return Saved(name=name, size=written)


# --- la page ---------------------------------------------------------------------------------------------------------------

STYLE = """
:root{color-scheme:dark;--bg:#0b0e14;--surface:#131923;--surface2:#1a2231;--line:#2a3446;--text:#eef2f8;--text2:#aab6ca;--accent:#f97316;--ink:#1a0a00;--good:#3ad29f;--bad:#ff7a7a}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--text);font:18px/1.5 system-ui,-apple-system,"Segoe UI",Roboto,"Noto Sans","DejaVu Sans",sans-serif}
main{max-width:760px;margin:0 auto;padding:28px 16px 48px}
h1{margin:0;font-size:34px}
p{margin:8px 0}
.sub,.dim{color:var(--text2)}
.card{background:var(--surface);border:1px solid var(--line);border-radius:14px;padding:18px 20px;margin-top:18px}
input[type=text]{width:100%;max-width:340px;padding:12px 14px;border-radius:10px;border:1px solid var(--line);background:#0b0e14;color:var(--text);font:600 24px/1.2 ui-monospace,Menlo,Consolas,monospace;letter-spacing:.14em;text-transform:uppercase}
.btn{display:inline-block;margin:10px 0 0;padding:12px 22px;border-radius:12px;background:var(--accent);color:var(--ink);font-weight:700;border:0;font-size:18px;cursor:pointer}
.drop{margin-top:12px;border:2px dashed var(--line);border-radius:14px;padding:34px 16px;text-align:center;color:var(--text2)}
.drop.over{border-color:var(--accent);color:var(--text)}
ul{list-style:none;margin:14px 0 0;padding:0;display:grid;gap:8px}
li{display:flex;gap:12px;align-items:center;justify-content:space-between;background:var(--surface2);border-radius:10px;padding:10px 14px}
li span:first-child{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.ok{color:var(--good)}.ko{color:var(--bad)}
.msg{min-height:1.5em;margin-top:10px}
[hidden]{display:none}
"""

SCRIPT = """
(function(){
  var token=null,busy=false,queue=[],NAME=/^[a-z0-9_]{1,24}[.](zip|7z)$/i,MAX=400*1048576;
  var $=function(id){return document.getElementById(id)};
  var msg=$('msg'),list=$('list');
  function say(t,c){msg.textContent=t;msg.className='msg '+(c||'')}
  function fmt(n){return n<1048576?Math.max(1,Math.round(n/1024))+' Ko':(n/1048576).toFixed(1).replace('.',',')+' Mo'}
  function showLogin(){$('probe').hidden=true;$('send-card').hidden=true;$('login-card').hidden=false}
  function showSend(note){$('probe').hidden=true;$('login-card').hidden=true;$('send-card').hidden=false;$('note').textContent=note||''}
  function probe(){
    fetch('/api/retro/me',{method:'POST',headers:{'Content-Type':'application/json'},body:'{}'})
      .then(function(r){return r.json()}).then(function(d){
        if(d.ok&&d.trusted){token=d.token;showSend('Ce PC est celui qui est associé au dock : aucun code à saisir.')}
        else{token=null;showLogin()}
      }).catch(function(){$('probe').hidden=true;say('Le dock ne répond pas.','ko')})
  }
  $('login').addEventListener('submit',function(e){
    e.preventDefault();
    fetch('/api/retro/login',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({code:$('code').value})})
      .then(function(r){return r.json()}).then(function(d){
        if(d.ok){token=d.token;showSend('');say('')}
        else{say(d.message||'Code refusé.','ko')}
      }).catch(function(){say('Le dock ne répond pas.','ko')})
  });
  function row(file){
    var li=document.createElement('li'),a=document.createElement('span'),b=document.createElement('span');
    a.textContent=file.name+' · '+fmt(file.size);b.textContent='en attente';
    li.appendChild(a);li.appendChild(b);list.appendChild(li);return b
  }
  function next(){
    if(busy||!queue.length)return;
    busy=true;var job=queue.shift(),xhr=new XMLHttpRequest();
    xhr.open('POST','/api/retro/upload?name='+encodeURIComponent(job.file.name));
    xhr.setRequestHeader('X-Upload-Token',token);
    xhr.upload.onprogress=function(e){if(e.lengthComputable)job.out.textContent=Math.round(100*e.loaded/e.total)+' %'};
    xhr.onload=function(){
      var d={};try{d=JSON.parse(xhr.responseText)}catch(e){}
      if(xhr.status===200&&d.ok){job.out.textContent='envoyé ✔';job.out.className='ok'}
      else{job.out.textContent=d.message||('refusé ('+xhr.status+')');job.out.className='ko'}
      if(xhr.status===401||xhr.status===409){token=null;say(d.message||'','ko');probe()}
      busy=false;next()
    };
    xhr.onerror=function(){job.out.textContent='envoi interrompu : le dock n’a pas répondu (fichier refusé ?)';job.out.className='ko';busy=false;next()};
    xhr.send(job.file)
  }
  function refuse(out,text){out.textContent=text;out.className='ko'}
  function add(files){
    for(var i=0;i<files.length;i++){
      var f=files[i],out=row(f);
      if(!NAME.test(f.name.trim())){refuse(out,'nom refusé : un jeu s’envoie en « nom.zip » ou « nom.7z » (minuscules, chiffres et _, 24 caractères au plus), comme MAME l’appelle');continue}
      if(f.size>MAX){refuse(out,'fichier trop gros (400 Mo au plus)');continue}
      queue.push({file:f,out:out})
    }
    next()
  }
  var drop=$('drop');
  drop.addEventListener('dragover',function(e){e.preventDefault();drop.className='drop over'});
  drop.addEventListener('dragleave',function(){drop.className='drop'});
  drop.addEventListener('drop',function(e){e.preventDefault();drop.className='drop';add(e.dataTransfer.files)});
  $('pick').addEventListener('change',function(e){add(e.target.files);e.target.value=''});
  probe();
})();
"""


def _csp_hash(text: str) -> str:
    return "'sha256-" + base64.b64encode(hashlib.sha256(text.encode()).digest()).decode() + "'"


#: Cette page seule a le droit d'envoyer (`connect-src 'self'`) ; aucune ressource de l'extérieur, aucun cadre.
UPLOAD_CSP = (
    "default-src 'none'; "
    f"style-src {_csp_hash(STYLE)}; script-src {_csp_hash(SCRIPT)}; "
    "connect-src 'self'; img-src data:; base-uri 'none'; form-action 'none'; frame-ancestors 'none'"
)

PAGE = """<!doctype html>
<html lang="fr">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>MyDock - envoyer des jeux</title>
<style>{style}</style>
</head>
<body>
<main>
<h1>Retrogame</h1>
<p class="sub">Envoie des jeux d'arcade (MAME) au dock <b>{hostname}</b>.</p>
<section id="probe" class="card"><p class="dim">Connexion au dock…</p></section>
<section id="login-card" class="card" hidden>
<form id="login">
<p>Ce PC n'est pas celui qui est associé au dock : il faut être devant le dock.
Sur son écran : <b>Divertissement → Retrogame → Envoyer des jeux</b>. Recopie le code affiché.</p>
<input id="code" type="text" inputmode="text" autocomplete="off" maxlength="12" placeholder="XXXX-XXXX" aria-label="Code affiché sur le dock">
<br><button class="btn" type="submit">Continuer</button>
</form>
</section>
<section id="send-card" class="card" hidden>
<p id="note" class="dim"></p>
<p>Dépose des fichiers <b>.zip</b> ou <b>.7z</b>, nommés comme MAME les appelle (<code>pacman.zip</code>, <code>galaga.zip</code>…).</p>
<div id="drop" class="drop">Glisse les fichiers ici<br>ou<br>
<label class="btn" for="pick">Choisir des fichiers</label>
<input id="pick" type="file" accept=".zip,.7z" multiple hidden></div>
<ul id="list"></ul>
</section>
<div id="msg" class="msg" role="status"></div>
<p class="dim">Le dock vérifie ensuite chaque jeu avec MAME : seuls ceux dont les fichiers sont complets apparaissent dans la liste.
Cette page est servie par le dock lui-même, sur ton réseau local.</p>
</main>
<script>{script}</script>
</body>
</html>
"""


def render_page(hostname: str) -> str:
    return PAGE.format(style=STYLE, script=SCRIPT, hostname=html.escape(hostname))
