"""Vidéos de l'économiseur d'écran téléchargées par le dock lui-même, sans intervention.

Deux origines, au choix dans Réglages → Économiseur d'écran → Vidéos :

* **Les vues aériennes d'Apple** : dix films « Aerial » (cinq de jour, cinq de nuit) de la liste du projet
  xscreensaver-aerial, à l'adresse publique qu'Apple utilise pour l'Apple TV. Rien n'est livré dans le paquet : ces
  films appartiennent à Apple, le Pi les télécharge pour l'usage de son propriétaire.
* **Les adresses saisies à l'écran** : l'adresse https d'une vidéo, ou d'un fichier texte qui en liste (une adresse par
  ligne, `.txt`, `.m3u` ou `.list`).

Chaque film est téléchargé, examiné par `ffprobe`, puis converti par `ffmpeg` en MP4 H.264 1024×600 sans le son (ce que
le Chromium du Pi lit sans réglage) ; un MP4 H.264 ou un WebM déjà léger est gardé tel quel. Tout se passe à basse
priorité, en tâche de fond. Seul le https est suivi, la carte garde toujours de la place libre, un film en échec
n'arrête pas les suivants, et les échecs se disent dans les Réglages avant d'être retentés.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import re
import shutil
import ssl
import urllib.parse
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from mydock_dock import apple_ca, screensaver
from mydock_dock.net import build_opener, context_with_roots, describe_presented

log = logging.getLogger("mydock.aerial")

APPLE_ID = "apple"
APPLE_LABEL = "Vues aériennes d'Apple"
#: Les seuls serveurs pour lesquels on ajoute l'autorité racine d'Apple aux autorités du Pi (voir apple_ca.py).
APPLE_HOSTS = frozenset({"sylvan.apple.com"})
#: Codes OpenSSL « autorité inconnue » : 19 (certificat auto-signé dans la chaîne), 20 (émetteur introuvable), 21 (premier
#: certificat invérifiable). Un certificat expiré, au mauvais nom ou révoqué n'ouvre jamais cette porte.
UNKNOWN_ISSUER = frozenset({19, 20, 21})
BASE_URL = "https://sylvan.apple.com/Aerials/2x/Videos"
#: (nom du fichier chez Apple, légende). Liste reprise de xscreensaver-aerial (atv4-2k.sh) : cinq films de jour, cinq de nuit.
CATALOG: tuple[tuple[str, str], ...] = (
    ("AK_A004_C012_SDR_20191217_SDR_2K_HEVC.mov", "Vue aérienne jour 1"),
    ("BO_A012_C031_SDR_20190726_SDR_2K_HEVC.mov", "Vue aérienne jour 2"),
    ("BO_A014_C008_SDR_20190719_SDR_2K_HEVC.mov", "Vue aérienne jour 3"),
    ("BO_A014_C023_SDR_20190717_F240F3709_SDR_2K_HEVC.mov", "Vue aérienne jour 4"),
    ("BO_A018_C029_SDR_20190812_SDR_2K_HEVC.mov", "Vue aérienne jour 5"),
    ("DB_D011_C010_2K_SDR_HEVC.mov", "Vue aérienne nuit 1"),
    ("HK_B005_C011_2K_SDR_HEVC.mov", "Vue aérienne nuit 2"),
    ("LA_A009_C009_2K_SDR_HEVC.mov", "Vue aérienne nuit 3"),
    ("LA_A011_C003_2K_SDR_HEVC.mov", "Vue aérienne nuit 4"),
    ("comp_A015_C018_0128ZS_v03_SDR_PS_FINAL_20180709__SDR_2K_HEVC.mov", "Vue aérienne nuit 5"),
)
WORK = ".work"
SAFE_SUFFIX = re.compile(r"\.[a-z0-9]{1,5}")
MAP_FILE = ".sources.json"  # quels films viennent de quelle adresse (pour les retirer avec elle)
START_DELAY = 60.0  # on laisse le Pi finir de démarrer
RETRY_DELAY = 1800.0
MAX_SOURCE = 2 * 1024**3
MAX_LIST_BYTES = 64 * 1024
MAX_LIST_ITEMS = 50
LIST_SUFFIXES = (".txt", ".m3u", ".list")
KEEP_WIDTH = 1280  # un film plus large est ramené à l'écran (1024×600)
CHUNK = 1024 * 256

FFMPEG_ARGS = (
    "-vf", "scale=1024:600:force_original_aspect_ratio=increase,crop=1024:600",
    "-c:v", "libx264", "-preset", "veryfast", "-crf", "24", "-pix_fmt", "yuv420p",
    "-an", "-movflags", "+faststart",
)  # fmt: skip


@dataclass(frozen=True)
class Item:
    """Un film à avoir : l'adresse où le prendre, sa légende (= nom du fichier sans extension), et son origine."""

    url: str
    title: str
    source: str  # APPLE_ID, ou l'adresse saisie dont il vient


class AerialError(Exception):
    """Un film qui n'a pas pu être pris ; le message est affichable. `fatal` : inutile d'essayer les suivants."""

    def __init__(self, message: str, *, fatal: bool = False) -> None:
        super().__init__(message)
        self.fatal = fatal


class CertificateError(AerialError):
    """Le Pi refuse le certificat d'un serveur ; `code` est le code OpenSSL, `url` l'adresse demandée."""

    def __init__(self, message: str, *, code: int, url: str) -> None:
        super().__init__(message)
        self.code = code
        self.url = url


class _Redirects(screensaver.HttpsOnly):
    """Les redirections vers du https seulement, qui retient la dernière adresse suivie : un refus de certificat doit
    nommer le serveur réellement joint, pas celui d'où l'on vient."""

    def __init__(self) -> None:
        self.last: str | None = None

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # type: ignore[no-untyped-def]
        request = super().redirect_request(req, fp, code, msg, headers, newurl)
        if request is not None:
            self.last = request.full_url
        return request


def certificate_message(url: str, error: ssl.SSLCertVerificationError) -> str:
    """Le refus d'un certificat, dit simplement : quel serveur, pourquoi, et ce qu'il présente (pour savoir quoi corriger)."""
    parts = urllib.parse.urlsplit(url)
    host = parts.hostname or "?"
    shown = describe_presented(host, parts.port or 443)
    detail = f" Il présente : {shown}." if shown else ""
    return f"Certificat de {host} refusé par le Pi ({error.verify_message}).{detail}"


def target_name(title: str) -> str:
    return f"{title}.mp4"


def apple_items() -> list[Item]:
    return [Item(f"{BASE_URL}/{name}", title, APPLE_ID) for name, title in CATALOG]


def is_list_url(url: str) -> bool:
    """Un fichier texte qui liste des vidéos, d'après la fin de l'adresse (avant tout « ? »)."""
    return urllib.parse.urlsplit(url).path.lower().endswith(LIST_SUFFIXES)


def title_from_url(url: str) -> str:
    """La légende d'un film venu d'une adresse : le nom du fichier, sans extension ni caractère douteux."""
    parts = urllib.parse.urlsplit(url)
    base = os.path.basename(urllib.parse.unquote(parts.path).rstrip("/"))
    stem = (base.rpartition(".")[0] if "." in base else base) if base else parts.hostname or "video"
    cleaned = "".join(c if c.isalnum() or c in " ._()+-" else "_" for c in stem).strip(" ._")
    return (cleaned or "video")[:100]


def parse_list(text: str) -> list[str]:
    """Les adresses https valides d'un fichier texte (une par ligne ; « # » commence un commentaire)."""
    found: list[str] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if screensaver.valid_source_url(line) and line not in found:
            found.append(line)
        if len(found) >= MAX_LIST_ITEMS:
            break
    return found


def short_label(url: str) -> str:
    """L'adresse telle qu'on l'affiche : sans « https:// », coupée au milieu si elle est longue."""
    bare = url.removeprefix("https://")
    return bare if len(bare) <= 46 else f"{bare[:26]}…{bare[-18:]}"


class _SourceState:
    def __init__(self) -> None:
        self.items: list[Item] | None = (
            None  # None : pas encore résolue (une liste qu'on n'a pas encore lue)
        )
        self.error: str | None = None


class AerialFetcher:
    def __init__(
        self,
        directory: Path,
        enabled: Callable[[], bool],
        *,
        apple: Callable[[], bool] = lambda: True,
        sources: Callable[[], list[str]] = lambda: [],
        trust_dir: Path | None = None,
        which: Callable[[str], str | None] = shutil.which,
        start_delay: float = START_DELAY,
        retry_delay: float = RETRY_DELAY,
    ) -> None:
        self.directory = directory
        self._enabled = enabled
        self._apple = apple
        self._sources = sources
        self._trust_dir = trust_dir
        self._apple_root: tuple[bytes, str] | None = None  # (racine DER, d'où elle vient)
        self._which = which
        self._start_delay = start_delay
        self._retry_delay = retry_delay
        self._states: dict[str, _SourceState] = {}
        self._counter = 0
        self.current: str | None = None
        self.error: str | None = None

    # --- ce qu'on sait, pour l'écran ---------------------------------------------------------------

    def _state(self, source: str) -> _SourceState:
        return self._states.setdefault(source, _SourceState())

    def _have(self) -> set[str]:
        return {v.name for v in screensaver.videos(self.directory)}

    @staticmethod
    def _present(title: str, have: set[str]) -> bool:
        return f"{title}.mp4" in have or f"{title}.webm" in have

    def _items_of(self, source: str) -> list[Item] | None:
        """Les films d'une source ; `None` si c'est une liste qu'on n'a pas encore lue."""
        if source == APPLE_ID:
            return apple_items()
        if not is_list_url(source):
            return [Item(source, title_from_url(source), source)]
        return self._state(source).items

    def status(self) -> dict[str, Any]:
        have = self._have()
        rows: list[dict[str, Any]] = []
        if self._apple():
            rows.append(self._row(APPLE_ID, APPLE_LABEL, None, have))
        for url in self._sources():
            rows.append(self._row(url, short_label(url), url, have))
        return {
            "total": sum(r["total"] or 0 for r in rows),
            "done": sum(r["done"] for r in rows),
            "active": self.current is not None,
            "current": self.current,
            "error": self.error,
            "enabled": self._enabled(),
            "apple_root": self._apple_root_info(),
            "sources": rows,
        }

    def _row(self, source: str, label: str, url: str | None, have: set[str]) -> dict[str, Any]:
        items = self._items_of(source)
        known = self._owned().get(source, []) if items is None else None
        done = (
            sum(self._present(i.title, have) for i in items)
            if items is not None
            else sum(self._present(t, have) for t in known or [])
        )
        return {
            "id": source,
            "label": label,
            "url": url,
            "builtin": source == APPLE_ID,
            "total": len(items) if items is not None else None,
            "done": done,
            "error": self._state(source).error,
        }

    def _apple_root_info(self) -> dict[str, str] | None:
        """Si l'autorité racine d'Apple a été ajoutée pour son serveur : son empreinte et d'où elle vient."""
        if self._apple_root is None or not self._apple():
            return None
        der, origin = self._apple_root
        return {
            "sha256": apple_ca.short_fingerprint(der),
            "origin": origin,
            "known": "yes" if apple_ca.fingerprint(der) == apple_ca.REMEMBERED_SHA256 else "no",
        }

    def missing(self) -> list[Item]:
        """Les films qui manquent, parmi ceux qu'on connaît."""
        have = self._have()
        wanted: list[Item] = []
        if self._apple():
            wanted += apple_items()
        for url in self._sources():
            wanted += self._items_of(url) or []
        return [i for i in wanted if not self._present(i.title, have)]

    # --- la boucle -------------------------------------------------------------------------------

    async def run(self) -> None:
        await asyncio.sleep(self._start_delay)
        while True:
            try:
                if not self._enabled():
                    self.current = None
                    await asyncio.sleep(30)
                    continue
                await self.fetch_missing()
                self.error = None
                await asyncio.sleep(
                    120
                )  # de nouvelles adresses saisies sont prises dans les deux minutes
            except asyncio.CancelledError:
                raise
            except AerialError as exc:
                self.current = None
                self.error = str(exc)
                log.warning("Vidéos de l'économiseur : %s", exc)
                await asyncio.sleep(self._retry_delay)
            except Exception:
                self.current = None
                self.error = "Erreur inattendue (voir le journal du dock)."
                log.exception("Vidéos de l'économiseur : erreur inattendue")
                await asyncio.sleep(self._retry_delay)

    async def fetch_missing(self) -> None:
        await self._resolve_lists()
        todo = self.missing()
        if not todo:
            return
        for tool in ("ffmpeg", "ffprobe"):
            if not self._which(tool):
                raise AerialError(
                    "ffmpeg n'est pas installé sur le Pi (sudo apt install ffmpeg).", fatal=True
                )
        failures: list[str] = []
        for source in {i.source for i in todo}:
            self._state(
                source
            ).error = None  # on repart d'un état net : seuls les échecs de ce tour s'afficheront
        for item in todo:
            if not self._enabled() or not self._still_wanted(item):
                continue
            self.current = item.title
            try:
                await self._process(item)
            except AerialError as exc:
                if exc.fatal:
                    raise
                # Un film en échec (adresse introuvable, fichier abîmé) n'arrête pas les autres : on passe au suivant.
                self._state(item.source).error = f"{item.title} : {exc}"
                failures.append(f"{item.title} : {exc}")
                log.warning("Vidéos de l'économiseur : %s : %s", item.title, exc)
            finally:
                self.current = None
        if failures:
            raise AerialError(
                f"{len(failures)} film(s) en échec, nouvel essai plus tard : {failures[-1]}"
            )

    def _still_wanted(self, item: Item) -> bool:
        """La source n'a pas été retirée depuis qu'on a fait la liste (retirer une adresse arrête son téléchargement)."""
        return self._apple() if item.source == APPLE_ID else item.source in self._sources()

    async def _resolve_lists(self) -> None:
        """Lit les fichiers texte de liste saisis dans Réglages (une erreur se dit sur leur ligne, sans rien arrêter)."""
        wanted = set(self._sources())
        for stale in [s for s in self._states if s not in wanted]:
            del self._states[stale]
        for url in wanted:
            if not is_list_url(url):
                state = self._state(url)
                state.items = [Item(url, title_from_url(url), url)]
                continue
            state = self._state(url)
            try:
                text = await asyncio.to_thread(self._fetch_list, url)
            except AerialError as exc:
                state.error = str(exc)
                continue
            addresses = parse_list(text)
            if not addresses:
                state.error = "La liste ne contient aucune adresse https."
                state.items = []
                continue
            state.items = [Item(a, title_from_url(a), url) for a in addresses]
            state.error = None
        self._remember(wanted)

    # --- un film -------------------------------------------------------------------------------

    async def _process(self, item: Item) -> None:
        work = self.directory / WORK
        self._counter += 1
        suffix = Path(urllib.parse.urlsplit(item.url).path).suffix.lower()
        if not SAFE_SUFFIX.fullmatch(suffix):
            suffix = ""
        source_file = work / f"dl-{self._counter}{suffix}"
        try:
            await self._fetch_file(item, source_file)
            info = await self._probe(source_file)
            keep = self._keep_extension(info, suffix)
            if not self._still_wanted(item):
                return
            if keep:
                final = self.directory / f"{item.title}{keep}"
                source_file.chmod(0o644)
                os.replace(source_file, final)
            else:
                await self._convert(source_file, item)
        finally:
            with contextlib.suppress(OSError):
                source_file.unlink()

    async def _fetch_file(self, item: Item, destination: Path) -> None:
        """Télécharge un film. Si le serveur d'Apple présente une autorité que le Pi ne connaît pas, ajoute la racine
        d'Apple (pour ce serveur seulement) et recommence une fois."""
        roots = self._roots_for(item)
        try:
            await asyncio.to_thread(self._download, item, destination, roots)
        except CertificateError as exc:
            if roots:
                raise self._despite_root(exc) from None
            if not self._needs_apple_root(item, exc):
                raise
        else:
            return
        roots = await asyncio.to_thread(self._obtain_apple_root)
        try:
            await asyncio.to_thread(self._download, item, destination, roots)
        except CertificateError as again:
            raise self._despite_root(again) from None

    @staticmethod
    def _despite_root(error: CertificateError) -> AerialError:
        """Une autorité encore inconnue d'un serveur d'Apple alors que sa racine est ajoutée : on le dit. Tout autre refus
        (certificat périmé, mauvais nom) et celui d'un autre serveur atteint par une redirection restent tels quels."""
        if (
            urllib.parse.urlsplit(error.url).hostname in APPLE_HOSTS
            and error.code in UNKNOWN_ISSUER
        ):
            return AerialError(f"{error} L'autorité racine d'Apple a pourtant été ajoutée.")
        return error

    def _roots_for(self, item: Item) -> tuple[str, ...]:
        """Les racines à ajouter pour cette adresse : celle d'Apple, une fois obtenue, pour les serveurs d'Apple seulement."""
        if self._apple_root is None or item.source != APPLE_ID:
            return ()
        if urllib.parse.urlsplit(item.url).hostname not in APPLE_HOSTS:
            return ()
        return (apple_ca.to_pem(self._apple_root[0]),)

    @staticmethod
    def _needs_apple_root(item: Item, error: CertificateError) -> bool:
        """Le refus vient de l'autorité inconnue d'un serveur d'Apple de la liste : c'est la porte qui mène à sa racine."""
        return (
            item.source == APPLE_ID
            and urllib.parse.urlsplit(error.url).hostname in APPLE_HOSTS
            and error.code in UNKNOWN_ISSUER
        )

    def _obtain_apple_root(self) -> tuple[str, ...]:
        try:
            der, origin = apple_ca.obtain_root(self._trust_dir)
        except apple_ca.AppleRootError as exc:
            raise AerialError(str(exc)) from None
        self._apple_root = (der, origin)
        log.info(
            "Autorité racine d'Apple ajoutée pour sylvan.apple.com (%s, empreinte %s)",
            origin, apple_ca.short_fingerprint(der),
        )  # fmt: skip
        return (apple_ca.to_pem(der),)

    @staticmethod
    def _keep_extension(info: dict[str, Any], suffix: str) -> str | None:
        """L'extension sous laquelle garder le fichier tel quel, ou None s'il faut le convertir."""
        if info["width"] > KEEP_WIDTH:
            return None
        if "webm" in info["format"] and info["codec"] in ("vp8", "vp9", "av1"):
            return ".webm"
        if "mp4" in info["format"] and suffix in (".mp4", ".m4v") and info["codec"] == "h264":
            return ".mp4"
        return None

    def _download(self, item: Item, destination: Path, roots: tuple[str, ...] = ()) -> None:
        work = destination.parent
        try:
            work.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise AerialError(
                f"Impossible d'écrire dans {self.directory} ({exc.strerror}).", fatal=True
            ) from None
        if shutil.disk_usage(work).free < screensaver.FREE_MARGIN + 2 * 400 * 1024**2:
            raise AerialError("Pas assez de place libre sur la carte du Pi.", fatal=True)
        request = urllib.request.Request(item.url, headers={"User-Agent": "mydock-dock"})
        redirects = _Redirects()
        # La racine d'Apple ne vérifie que les serveurs d'Apple de la liste : une redirection vers un autre serveur
        # est vérifiée par les seules autorités du Pi.
        trusted = context_with_roots(*roots) if roots else None
        opener = build_opener(
            redirects, host_contexts=dict.fromkeys(APPLE_HOSTS, trusted) if trusted else None
        )
        written = 0
        try:
            with opener.open(request, timeout=screensaver.TIMEOUT) as response:
                kind = response.headers.get("Content-Type", "").split(";")[0].strip().lower()
                if kind.startswith("text/") or kind in (
                    "application/json",
                    "application/xhtml+xml",
                ):
                    raise AerialError(
                        "Cette adresse ne donne pas une vidéo (le serveur envoie une page)."
                    )
                length = _int(response.headers.get("Content-Length"))
                if length > MAX_SOURCE:
                    raise AerialError("Ce film est trop gros (2 Go au plus).")
                if length and shutil.disk_usage(work).free < screensaver.FREE_MARGIN + 2 * length:
                    raise AerialError("Pas assez de place libre sur la carte du Pi.", fatal=True)
                with destination.open("wb") as out:
                    while chunk := response.read(CHUNK):
                        written += len(chunk)
                        if written > MAX_SOURCE:
                            raise AerialError("Ce film est trop gros (2 Go au plus).")
                        out.write(chunk)
        except screensaver.SaverError as exc:
            raise AerialError(str(exc)) from None
        except urllib.error.HTTPError as exc:
            raise AerialError(f"Le serveur répond « {exc.code} {exc.reason} ».") from None
        except (OSError, ValueError) as exc:
            reason = getattr(exc, "reason", exc)
            if isinstance(reason, ssl.SSLCertVerificationError):
                refused = (
                    redirects.last or item.url
                )  # le serveur réellement joint, redirections suivies
                raise CertificateError(
                    certificate_message(refused, reason), code=reason.verify_code, url=refused
                ) from None
            raise AerialError(f"Téléchargement impossible : {reason}.") from None
        if written == 0:
            raise AerialError("Fichier vide.")

    def _fetch_list(self, url: str) -> str:
        request = urllib.request.Request(url, headers={"User-Agent": "mydock-dock"})
        redirects = _Redirects()
        opener = build_opener(redirects)
        try:
            with opener.open(request, timeout=screensaver.TIMEOUT) as response:
                body = response.read(MAX_LIST_BYTES + 1)
        except screensaver.SaverError as exc:
            raise AerialError(str(exc)) from None
        except urllib.error.HTTPError as exc:
            raise AerialError(f"La liste ne répond pas : « {exc.code} {exc.reason} ».") from None
        except (OSError, ValueError) as exc:
            reason = getattr(exc, "reason", exc)
            if isinstance(reason, ssl.SSLCertVerificationError):
                raise AerialError(certificate_message(redirects.last or url, reason)) from None
            raise AerialError(f"Liste injoignable : {reason}.") from None
        if len(body) > MAX_LIST_BYTES:
            raise AerialError("La liste est trop longue (64 Ko au plus).")
        return body.decode("utf-8", "replace")

    async def _probe(self, path: Path) -> dict[str, Any]:
        command = ["ffprobe", "-v", "error", "-protocol_whitelist", "file", "-select_streams", "v:0",
                   "-show_entries", "stream=codec_name,width,height:format=format_name",
                   "-of", "json", str(path)]  # fmt: skip
        try:
            process = await asyncio.create_subprocess_exec(
                *command, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL
            )
            out, _ = await process.communicate()
        except OSError as exc:
            raise AerialError(f"ffprobe ne démarre pas ({exc.strerror}).", fatal=True) from None
        try:
            data = json.loads(out or b"{}")
            stream = (data.get("streams") or [None])[0]
            return {
                "codec": str(stream["codec_name"]),
                "width": int(stream.get("width") or 0),
                "format": str((data.get("format") or {}).get("format_name", "")),
            }
        except (ValueError, TypeError, KeyError, AttributeError):
            raise AerialError("Ce fichier n'est pas une vidéo que ffmpeg sait lire.") from None

    async def _convert(self, source_file: Path, item: Item) -> None:
        work = source_file.parent
        out = work / f"conv-{self._counter}.mp4"
        command = ["nice", "-n", "15", "ffmpeg", "-nostdin", "-loglevel", "error", "-y",
                   "-protocol_whitelist", "file", "-i", str(source_file), *FFMPEG_ARGS, str(out)]  # fmt: skip
        try:
            process = await asyncio.create_subprocess_exec(
                *command, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE
            )
            _, err = await process.communicate()
        except OSError as exc:
            raise AerialError(f"ffmpeg ne démarre pas ({exc.strerror}).", fatal=True) from None
        try:
            if process.returncode != 0 or not out.is_file() or out.stat().st_size == 0:
                tail = err.decode("utf-8", "replace").strip().splitlines()[-1:] or ["?"]
                raise AerialError(f"Conversion impossible : {tail[0][:120]}")
            if not self._still_wanted(item):
                return
            out.chmod(0o644)
            os.replace(out, self.directory / target_name(item.title))
            log.info("Vidéo de l'économiseur prête : %s", item.title)
        finally:
            with contextlib.suppress(OSError):
                out.unlink()

    # --- retirer une source avec ses films ---------------------------------------------------------

    def _owned(self) -> dict[str, list[str]]:
        try:
            data = json.loads((self.directory / MAP_FILE).read_text())
            owned = data.get("sources", {})
            return {
                k: [t for t in v if isinstance(t, str)]
                for k, v in owned.items()
                if isinstance(v, list)
            }
        except (OSError, ValueError, AttributeError):
            return {}

    def _remember(self, wanted: set[str]) -> None:
        """Note, pour chaque adresse, les films qu'elle donne : c'est ce qu'on efface en la retirant."""
        owned = {k: v for k, v in self._owned().items() if k in wanted}
        for url in wanted:
            items = self._state(url).items
            if items is not None:
                owned[url] = [i.title for i in items]
        try:
            self.directory.mkdir(parents=True, exist_ok=True)
            part = self.directory / f"{MAP_FILE}.part"
            part.write_text(json.dumps({"version": 1, "sources": owned}))
            os.replace(part, self.directory / MAP_FILE)
        except OSError:
            log.warning("Impossible de noter l'origine des vidéos dans %s", self.directory)

    def forget(self, source: str) -> int:
        """Efface les films d'une source (Apple ou une adresse) ; retourne leur nombre. Les réglages sont déjà changés."""
        titles = (
            [t for _, t in CATALOG]
            if source == APPLE_ID
            else (self._owned().get(source) or [i.title for i in self._items_of(source) or []])
        )
        removed = 0
        for title in titles:
            for extension in (".mp4", ".webm"):
                with contextlib.suppress(screensaver.SaverError):
                    screensaver.remove(self.directory, f"{title}{extension}")
                    removed += 1
        self._states.pop(source, None)
        mapping = self._owned()
        if source in mapping:
            del mapping[source]
            with contextlib.suppress(OSError):
                (self.directory / MAP_FILE).write_text(
                    json.dumps({"version": 1, "sources": mapping})
                )
        return removed


def _int(value: str | None) -> int:
    try:
        return int(value) if value else 0
    except ValueError:
        return 0
