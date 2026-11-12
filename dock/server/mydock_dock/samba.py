"""Kiosque : parcourir les partages Samba de la Freebox (ou de tout autre NAS) et choisir une vidéo.

Le dock liste les partages et leurs dossiers avec `smbclient` ; la vidéo choisie est ensuite lue par VLC, sur l'écran du
dock (voir player.py). Le Pi ne copie rien : VLC lit le fichier en direct sur le partage.

Sûreté :
  * le serveur (`smb_host`) vient des Réglages, jamais d'une demande de l'écran ; l'écran ne donne qu'un partage et un
    chemin, validés ici (pas de « .. », pas de caractère de contrôle) ;
  * le dossier est passé à `smbclient` par `-D` (un argument), jamais dans sa commande `-c` : un nom de dossier contenant
    « ; » ne peut donc pas y glisser une seconde commande (« put », « del »…) ;
  * le mot de passe passe par l'environnement du processus (`USER=nom%mot-de-passe`), pas par sa ligne de commande, que
    tout utilisateur du Pi peut lire avec `ps` ; il n'est jamais écrit dans un journal ni envoyé à l'interface.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import re
import shutil
import urllib.parse
from dataclasses import dataclass
from typing import Any

TIMEOUT = 12.0
MAX_ENTRIES = 400
VIDEO_EXTENSIONS = frozenset(
    (
        ".mkv",
        ".mp4",
        ".m4v",
        ".avi",
        ".mov",
        ".mpg",
        ".mpeg",
        ".ts",
        ".m2ts",
        ".mts",
        ".wmv",
        ".flv",
        ".webm",
        ".ogv",
        ".divx",
        ".vob",
    )
)
#: Dossiers que les NAS créent pour eux-mêmes : ils n'ont rien à faire à l'écran.
HIDDEN_NAMES = frozenset(
    ("lost+found", "system volume information", "$recycle.bin", "recycler", "#recycle", "@eadir")
)

_LS_LINE = re.compile(
    r"^  (?P<name>.+?)\s+(?P<attrs>[ADHSRN]+)\s+(?P<size>\d+)\s+"
    r"(?P<date>[A-Z][a-z]{2} [A-Z][a-z]{2} [ \d]\d \d\d:\d\d:\d\d \d{4})\s*$"
)


class SambaError(Exception):
    """Le partage n'a pas pu être lu ; le message est affichable à l'écran."""


@dataclass(frozen=True)
class Entry:
    name: str
    is_dir: bool
    size: int

    def public(self) -> dict[str, Any]:
        return {"name": self.name, "dir": self.is_dir, "size": self.size}


def is_video(name: str) -> bool:
    return os.path.splitext(name)[1].lower() in VIDEO_EXTENSIONS


# --- ce qu'on accepte de l'écran -------------------------------------------------------------------------------------


def _clean_segment(value: str, label: str) -> str:
    if (
        not value
        or len(value) > 255
        or value in (".", "..")
        or any(c in value for c in "\\/")
        or any(ord(c) < 32 or ord(c) == 127 for c in value)
    ):
        raise SambaError(f"{label} invalide.")
    return value


def clean_share(share: object) -> str:
    if not isinstance(share, str):
        raise SambaError("Partage invalide.")
    return _clean_segment(share.strip(), "Le nom du partage")


def clean_path(path: object) -> list[str]:
    """Le chemin dans le partage, en morceaux (« Films/Dessins » -> ["Films", "Dessins"]) ; « » est la racine."""
    if path in (None, ""):
        return []
    if not isinstance(path, str) or len(path) > 1000:
        raise SambaError("Chemin invalide.")
    return [_clean_segment(part, "Le nom du dossier") for part in path.split("/") if part]


def smb_url(host: str, share: str, parts: list[str]) -> str:
    """L'adresse smb:// d'un fichier, que VLC ouvre telle quelle."""
    quoted = "/".join(urllib.parse.quote(p, safe="") for p in [share, *parts])
    return f"smb://{host}/{quoted}"


# --- lire un fichier par smbclient (quand le module Samba de VLC ne l'ouvre pas) ---------------------------------------


def stream_name_is_safe(parts: list[str]) -> bool:
    """Le nom du fichier peut-il se glisser dans « get "nom" - » ? smbclient lit sa ligne de commandes sans prudence :
    « ; » sépare deux ordres (`get "a";rm "b"`) et « " » ferme le nom. Ces noms-là ne passent que par VLC."""
    return bool(parts) and not any(c in parts[-1] for c in '";')


def smbclient_args(
    host: str,
    share: str,
    parts: list[str],
    command: str,
    *,
    anonymous: bool,
    port: int | None = None,
) -> list[str]:
    """Les arguments de smbclient (sans le programme) pour `command` sur le fichier `parts` : le dossier passe par -D,
    jamais dans la commande ; le mot de passe n'est jamais là (il va dans l'environnement : voir smbclient_env)."""
    args = [f"//{host}/{share}"]
    if len(parts) > 1:
        args += ["-D", "\\".join(parts[:-1])]
    if port:
        args += ["-p", str(port)]
    if anonymous:
        args.append("-N")
    args += ["-s", "/dev/null", "--option=name resolve order=host", "-c", command]
    return args


def smbclient_env(user: str, password: str) -> dict[str, str]:
    env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": "/tmp", "LC_ALL": "C.UTF-8"}
    if user:
        env["USER"] = f"{user}%{password}"  # jamais dans les arguments
    return env


def stream_command(
    binary: str, host: str, share: str, parts: list[str], *, user: str, port: int | None = None
) -> list[str]:
    """smbclient qui écrit le fichier sur sa sortie standard (VLC le lit comme un tube)."""
    if not stream_name_is_safe(parts):
        raise SambaError("Ce nom de fichier ne peut pas être lu par smbclient.")
    return [
        binary,
        *smbclient_args(host, share, parts, f'get "{parts[-1]}" -', anonymous=not user, port=port),
    ]


#: Ce que smbclient écrit sur sa sortie standard quand `get "nom" -` échoue : le texte arrive dans la vidéo elle-même.
_STREAM_ERROR_MARKERS = (
    "NT_STATUS_",
    "tree connect failed",
    "session setup failed",
    "Anonymous login successful",
    "protocol negotiation failed",
    "do_connect",
    "Connection to ",
    "Unable to initialize messaging context",
    "Can't load",
)


def output_is_error(first_block: bytes) -> bool:
    """Le début de ce que smbclient écrit est-il un message d'erreur plutôt que les premiers octets d'une vidéo ?

    Un fichier vidéo ne commence jamais par du texte comme « NT_STATUS_… » : smbclient, lui, écrit ses erreurs sur la
    même sortie que le fichier, et c'est la seule façon de les voir avant de les donner à VLC."""
    head = first_block[:600].decode("utf-8", "replace")
    return any(marker in head for marker in _STREAM_ERROR_MARKERS)


def explain_stream(text: str, host: str) -> str:
    """La phrase qui dit pourquoi smbclient n'a pas donné le fichier, d'après ce qu'il a écrit."""
    if any(
        m in text
        for m in (
            "NT_STATUS_OBJECT_NAME_NOT_FOUND",
            "NT_STATUS_NO_SUCH_FILE",
            "NT_STATUS_OBJECT_PATH_NOT_FOUND",
        )
    ):
        return "Ce fichier n'existe plus sur le partage."
    known = explain(text, host)
    if known != _UNKNOWN:
        return known
    first = next((row.strip() for row in text.splitlines() if row.strip()), "")
    return (
        f"Le serveur de fichiers refuse la lecture : « {first[:120]} »."
        if first
        else "smbclient n'a rien lu du fichier."
    )


# --- ce que dit smbclient --------------------------------------------------------------------------------------------


def parse_shares(output: str) -> list[str]:
    """Les partages de disque (« Disk|nom|commentaire ») : ni IPC$ ni les partages d'administration."""
    shares: list[str] = []
    for line in output.splitlines():
        kind, _, rest = line.partition("|")
        name = rest.partition("|")[0]
        if kind == "Disk" and name and not name.endswith("$") and name not in shares:
            shares.append(name)
    return shares


def parse_listing(output: str) -> list[Entry]:
    """Les dossiers et les vidéos d'un `ls` de smbclient : le reste (documents, fichiers cachés, déchets des NAS) est ignoré."""
    entries: list[Entry] = []
    for line in output.splitlines():
        match = _LS_LINE.match(line)
        if not match:
            continue
        name, attrs = match.group("name"), match.group("attrs")
        if (
            name in (".", "..")
            or name.startswith(".")
            or "H" in attrs
            or name.lower() in HIDDEN_NAMES
        ):
            continue
        is_dir = "D" in attrs
        if is_dir or is_video(name):
            entries.append(Entry(name, is_dir, int(match.group("size"))))
    entries.sort(key=lambda e: (not e.is_dir, _natural(e.name)))
    return entries[:MAX_ENTRIES]


def _natural(name: str) -> list[Any]:
    return [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", name)]


_UNKNOWN = "Le serveur de fichiers a répondu autrement que prévu."

_ERRORS = (
    ("NT_STATUS_LOGON_FAILURE", "Identifiant ou mot de passe refusé par le partage."),
    (
        "NT_STATUS_ACCESS_DENIED",
        "Accès refusé : ce partage demande un identifiant (Réglages → Divertissement).",
    ),
    ("NT_STATUS_BAD_NETWORK_NAME", "Ce partage n'existe pas."),
    ("NT_STATUS_OBJECT_NAME_NOT_FOUND", "Ce dossier n'existe plus."),
    ("NT_STATUS_OBJECT_PATH_NOT_FOUND", "Ce dossier n'existe plus."),
    ("NT_STATUS_NO_SUCH_FILE", "Ce dossier n'existe plus."),
    ("NT_STATUS_HOST_UNREACHABLE", "Le serveur de fichiers est injoignable."),
    ("NT_STATUS_IO_TIMEOUT", "Le serveur de fichiers ne répond pas."),
    (
        "NT_STATUS_CONNECTION_REFUSED",
        "Le serveur de fichiers refuse la connexion (le partage Windows est-il activé ?).",
    ),
    ("NT_STATUS_UNSUCCESSFUL", "Le serveur de fichiers est injoignable."),
    ("NT_STATUS_BAD_NETWORK_PATH", "Le serveur de fichiers est introuvable."),
    ("NT_STATUS_NO_LOGON_SERVERS", "Le serveur de fichiers est introuvable."),
    (
        "NT_STATUS_UNSUPPORTED",
        "Le serveur de fichiers n'est pas compatible (SMB2 ou plus récent requis).",
    ),
)


def explain(output: str, host: str) -> str:
    """La phrase qui dit ce qui a échoué d'après la sortie d'erreur de smbclient."""
    for marker, message in _ERRORS:
        if marker in output:
            return message
    if "Connection to" in output and "failed" in output:
        return f"Le serveur de fichiers « {host} » est injoignable."
    if "Unable to initialize messaging context" in output or (
        "Can't load" in output and "NT_STATUS" not in output
    ):
        return "smbclient ne démarre pas : sudo apt install smbclient."
    return _UNKNOWN


class SambaBrowser:
    def __init__(self, host: Any, user: Any, password: Any, *, port: int | None = None) -> None:
        # Des fonctions : les Réglages changent. `port` ne sert qu'aux essais (un Samba qui n'écoute pas sur le 445).
        self._host, self._user, self._password, self._port = host, user, password, port

    def available(self) -> bool:
        return shutil.which("smbclient") is not None

    def configured(self) -> bool:
        return bool(self._host())

    async def _run(self, args: list[str]) -> str:
        binary = shutil.which("smbclient")
        if binary is None:
            raise SambaError("smbclient est absent du Pi : sudo apt install smbclient.")
        env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": "/tmp", "LC_ALL": "C.UTF-8"}
        user, password = self._user(), self._password()
        auth = ["-N"]
        if user:
            env["USER"] = (
                f"{user}%{password}"  # le mot de passe ne figure jamais dans les arguments
            )
            auth = []
        command = [
            binary,
            *args,
            *(["-p", str(self._port)] if self._port else []),
            *auth,
            "-s",
            "/dev/null",
            "--option=name resolve order=host",
            "-t",
            str(int(TIMEOUT)),
        ]
        try:
            process = await asyncio.create_subprocess_exec(
                *command,
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                env=env,
            )
        except OSError as exc:
            raise SambaError(f"smbclient ne démarre pas ({exc.strerror}).") from None
        try:
            out, _ = await asyncio.wait_for(process.communicate(), TIMEOUT + 4)
        except TimeoutError:
            with contextlib.suppress(ProcessLookupError):
                process.kill()
            await process.wait()
            raise SambaError("Le serveur de fichiers ne répond pas.") from None
        text = out.decode("utf-8", "replace")
        if process.returncode != 0 or "NT_STATUS_" in text:
            raise SambaError(explain(text, self._host()))
        return text

    async def shares(self) -> list[str]:
        host = self._host()
        if not host:
            raise SambaError(
                "Aucun serveur de fichiers : renseigne-le dans Réglages → Divertissement."
            )
        return parse_shares(await self._run(["-L", f"//{host}", "-g"]))

    async def listing(self, share: str, parts: list[str]) -> list[Entry]:
        host = self._host()
        if not host:
            raise SambaError(
                "Aucun serveur de fichiers : renseigne-le dans Réglages → Divertissement."
            )
        args = [f"//{host}/{share}", "-c", "ls"]
        if parts:
            args += ["-D", "\\".join(parts)]  # un argument à part : jamais dans la commande « ls »
        return parse_listing(await self._run(args))
