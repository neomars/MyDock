"""L'assistant de lecture : il tourne dans la session de l'écran du dock et y pilote VLC.

Le service du dock est isolé (aucun accès à l'écran) : VLC doit donc être lancé depuis la session graphique du Pi, par
le compositeur (labwc, voir deploy/pi/labwc/autostart). Cet assistant s'y connecte au dock par `/ws/player` et :

  * lance VLC en plein écran sur l'adresse que le dock lui donne, et le commande par son interface « oldrc » (un socket
    Unix propre à l'assistant : rien n'écoute sur le réseau) ;
  * lit les chaînes de la Freebox (`rtsp://…`) par ffmpeg quand ce VLC n'a pas le module RTSP (Debian et Ubuntu ne
    l'ont pas toujours) : ffmpeg recopie le flux tel quel dans un tube que VLC lit, sans le décoder ;
  * ramène le dock au premier plan quand on touche l'écran pendant la vidéo (`swayidle` voit le toucher, `wlrctl`
    change la fenêtre active) : le dock montre alors la télécommande, et la vidéo continue derrière ;
  * rend compte au dock : en lecture, position, durée, fin, ou ce qui a échoué.

Jamais de shell : des listes d'arguments, construites ici. Le mot de passe d'un partage ne passe pas par la ligne de
commande de VLC mais par un petit fichier de configuration (droits 600, dans le dossier d'exécution de la session),
effacé à la fin de la lecture.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import glob
import json
import logging
import os
import re
import shutil
import signal
import sys
import tempfile
import time
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any, Protocol

log = logging.getLogger("mydock.player")

DEFAULT_URL = "ws://127.0.0.1:8080/ws/player"
#: Options supplémentaires de VLC, une par ligne (# = commentaire) ; la variable donne un autre fichier (essais).
FLAGS_FILE = "/etc/mydock-kiosk/vlc.flags"
FLAGS_ENV = "MYDOCK_VLC_FLAGS"
LOG_NAME = "mydock-player.log"  # dans le dossier d'exécution de la session : la dernière lecture qui a échoué
POLL_SECONDS = 1.0
RC_WAIT = 20.0  # temps laissé à VLC pour ouvrir son socket de commande
#: Après être passé à la vidéo, un toucher n'est pas pris pour une demande de retour au dock (c'est celui qui l'a lancée).
TOUCH_GRACE = 1.5
VLC_VOLUME_MAX = 256  # 100 % pour VLC

_NUMBER_LINE = re.compile(r"^\s*(?:>\s*)?(-?\d+)\s*$")


# --- ce que VLC et la session offrent ------------------------------------------------------------------------------


def find_vlc() -> str | None:
    return shutil.which("vlc") or shutil.which("cvlc")


def has_live555() -> bool:
    """Ce VLC sait-il lire le RTSP lui-même ? (le module « live555 » : absent d'Ubuntu, présent de Debian selon la version)"""
    patterns = (
        "/usr/lib/*/vlc/plugins/access/liblive555_plugin.so",
        "/usr/lib/vlc/plugins/access/liblive555_plugin.so",
    )
    return any(glob.glob(p) for p in patterns)


def has_vlc_plugin(name: str) -> bool:
    """Ce VLC a-t-il le module `name` (le fichier `lib<name>_plugin.so`) ? Chaque module vient d'un paquet Debian."""
    patterns = (
        f"/usr/lib/*/vlc/plugins/*/lib{name}_plugin.so",
        f"/usr/lib/vlc/plugins/*/lib{name}_plugin.so",
    )
    return any(glob.glob(p) for p in patterns)


def needs_bridge(url: str, live555: bool) -> bool:
    """Faut-il passer par ffmpeg ? Seulement pour le RTSP, quand VLC ne le lit pas lui-même."""
    return url.lower().startswith("rtsp://") and not live555


def read_flags(path: str | None = None) -> list[str]:
    path = path or os.environ.get(FLAGS_ENV) or FLAGS_FILE
    try:
        lines = Path(path).read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    return [line.strip() for line in lines if line.strip() and not line.lstrip().startswith("#")][
        :40
    ]


def vlc_command(
    vlc: str,
    source: str,
    socket_path: str,
    *,
    volume: int,
    config_file: str | None = None,
    extra: Sequence[str] = (),
) -> list[str]:
    """La ligne de commande de VLC : plein écran, sans interface graphique, pilotable par `socket_path`."""
    command = [vlc]
    if config_file:
        command += ["--config", config_file]
    command += [
        "-I",
        "oldrc",
        "--rc-unix",
        socket_path,
        "--rc-fake-tty",
        "--fullscreen",
        "--no-video-title-show",
        "--no-osd",
        "--mouse-hide-timeout=300",
        "--play-and-exit",
        # Jamais de boîte de dialogue (VLC attendrait un mot de passe que personne ne peut taper : il échoue tout de
        # suite, « Permission denied »), et pas de trousseau de clés (il cherche D-Bus, absent de la session du kiosque).
        "--no-interact",
        "--keystore=memory",
        "--no-snapshot-preview",
        f"--volume={round(volume * VLC_VOLUME_MAX / 100)}",
        *extra,
        "--",
        source,
    ]
    return command


def ffmpeg_bridge_command(ffmpeg: str, url: str) -> list[str]:
    """Recopie un flux RTSP en MPEG-TS sur la sortie standard (sans le décoder) : VLC le lit comme un fichier."""
    return [
        ffmpeg,
        "-nostdin",
        "-hide_banner",
        "-loglevel",
        "error",
        "-i",
        url,
        "-map",
        "0",
        "-c",
        "copy",
        "-f",
        "mpegts",
        "pipe:1",
    ]


def parse_numbers(text: str) -> list[int]:
    """Les lignes qui ne sont qu'un nombre dans la réponse de VLC (le reste : « status change », invites…)."""
    return [int(m.group(1)) for line in text.splitlines() if (m := _NUMBER_LINE.match(line))]


#: Les lignes de VLC qui n'apprennent rien sur un échec (son interface sans fenêtre, la fin de la liste de lecture…).
_VLC_NOISE = (
    "dummy interface",
    "dbus",
    "globalhotkeys",
    "no suitable interface",
    "end of playlist",
    "your input can't be opened",
    "check the log for details",
)
_VLC_PREFIX = re.compile(r"^\[[0-9a-fA-F]+\]\s*")


def vlc_detail(tail: str, limit: int = 200) -> str:
    """Les dernières lignes utiles de ce que VLC a écrit (sans l'adresse mémoire de tête ni le bruit habituel), en une phrase."""
    lines: list[str] = []
    for raw in tail.splitlines():
        line = _VLC_PREFIX.sub("", raw).strip()
        lowered = line.lower()
        if line and not any(noise in lowered for noise in _VLC_NOISE) and line not in lines:
            lines.append(line)
    text = " ; ".join(lines[-2:])
    return text if len(text) <= limit else text[: limit - 1] + "…"


def explain_exit(played: bool, live: bool, tail: str, code: int | None = None) -> str | None:
    """Pourquoi VLC s'est arrêté sans avoir jamais joué ; `None` : fin normale. `tail` : ce que VLC (et ffmpeg) ont écrit."""
    if played:
        return None
    lowered = tail.lower()
    detail = vlc_detail(tail)
    more = f" Détail : « {detail} »." if detail else ""
    if "permission denied" in lowered or "access denied" in lowered or "logon" in lowered:
        return (
            "Accès refusé au partage : vérifie l'identifiant et le mot de passe (Réglages → Divertissement)."
            + more
        )
    if "no such file" in lowered or "not found" in lowered:
        return (
            "Fichier introuvable sur le partage (le nom, les droits d'accès, ou le serveur qui ne répond pas)."
            + more
        )
    if "unable to open the mrl 'smb://" in lowered and "smb stream" not in lowered:
        return (
            "VLC ne sait pas lire le Samba : sudo apt install vlc-plugin-samba (puis redémarrer le Pi)."
            + more
        )
    if (
        "no suitable decoder" in lowered
        or "codec not supported" in lowered
        or "unsupported codec" in lowered
    ):
        return (
            "VLC ne sait pas décoder cette vidéo (codec absent, ou trop lourd pour le Pi)." + more
        )
    if any(
        word in lowered
        for word in (
            "no suitable vout",
            "video output creation failed",
            "cannot open display",
            "unable to open display",
            "failed to open display",
        )
    ):
        return (
            "VLC n'arrive pas à afficher la vidéo sur l'écran du dock : sudo apt install xwayland vlc-plugin-video-output, "
            "puis redémarrer le Pi." + more
        )
    if code is not None and code < 0:
        try:
            name = signal.Signals(-code).name
        except ValueError:
            name = f"signal {-code}"
        return (
            f"VLC s'est arrêté brutalement ({name}) : souvent le décodage matériel. Essaie --avcodec-hw=none dans "
            "/etc/mydock-kiosk/vlc.flags." + more
        )
    if live:
        return (
            "La chaîne ne répond pas (la Freebox est-elle allumée, et le Pi sur son réseau ?)."
            + more
        )
    return "VLC n'a pas pu lire cette vidéo (format, ou accès au partage)." + more


# --- VLC ---------------------------------------------------------------------------------------------------------------


class RcClient:
    """Le socket de commande « oldrc » de VLC : on écrit des commandes, on lit les nombres qu'il répond."""

    def __init__(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        self._reader, self._writer = reader, writer
        self._lock = asyncio.Lock()

    @classmethod
    async def connect(cls, path: str, timeout: float) -> RcClient:
        deadline = time.monotonic() + timeout
        while True:
            try:
                reader, writer = await asyncio.open_unix_connection(path)
                return cls(reader, writer)
            except OSError:
                if time.monotonic() > deadline:
                    raise
                await asyncio.sleep(0.2)

    async def ask(self, command: str, quiet: float = 0.2, limit: float = 1.5) -> str:
        """La réponse de VLC (vide si la liaison est coupée : VLC vient de s'arrêter, la fin de lecture le dira)."""
        async with self._lock:
            chunks: list[bytes] = []
            try:
                self._writer.write((command + "\n").encode())
                await self._writer.drain()
                deadline = time.monotonic() + limit
                while time.monotonic() < deadline:
                    try:
                        chunk = await asyncio.wait_for(self._reader.read(4096), quiet)
                    except TimeoutError:
                        break
                    if not chunk:
                        break
                    chunks.append(chunk)
            except OSError:  # ConnectionResetError, BrokenPipeError
                pass
            return b"".join(chunks).decode("utf-8", "replace")

    def close(self) -> None:
        with contextlib.suppress(Exception):
            self._writer.close()


class Playback(Protocol):
    async def start(self) -> None: ...
    async def status(self) -> dict[str, Any]: ...
    async def command(self, name: str, **params: Any) -> None: ...
    async def stop(self) -> None: ...
    @property
    def finished(self) -> bool: ...
    def error(self) -> str | None: ...


class VlcPlayback:
    """Une lecture : VLC (et ffmpeg si la chaîne passe par le pont), jusqu'à la fin ou l'arrêt."""

    def __init__(
        self,
        url: str,
        *,
        live: bool,
        volume: int,
        vlc: str,
        ffmpeg: str | None,
        live555: bool,
        runtime_dir: Path,
        env: dict[str, str],
        extra: Sequence[str] = (),
        smb: tuple[str, str] | None = None,
        job: int = 0,
    ) -> None:
        self.url, self.live, self.volume = url, live, volume
        self._vlc, self._ffmpeg, self._live555 = vlc, ffmpeg, live555
        self._dir, self._env, self._extra, self._smb = runtime_dir, env, list(extra), smb
        self._job = job
        self._procs: list[asyncio.subprocess.Process] = []
        self._rc: RcClient | None = None
        self._drains: list[asyncio.Task[None]] = []
        self._stderr_tail = ""
        self._played = False
        self._paused = False
        self._stopped = False
        self._started = time.monotonic()
        self._socket = str(runtime_dir / f"vlc-{os.getpid()}-{job}.sock")
        self._config: Path | None = None

    @property
    def finished(self) -> bool:
        return bool(self._procs) and self._procs[-1].returncode is not None

    async def start(self) -> None:
        source = self.url
        extra = list(self._extra)
        stdin: int | None = None
        bridge_fd: int | None = None
        if needs_bridge(self.url, self._live555):
            if not self._ffmpeg:
                raise RuntimeError(
                    "ffmpeg manque : sudo apt install ffmpeg (VLC ne lit pas les chaînes de la Freebox sans lui)."
                )
            read_fd, bridge_fd = os.pipe()
            stdin, source = read_fd, "fd://0"
            extra += ["--network-caching=1500"]
        elif self.live:
            extra += ["--network-caching=1500", "--live-caching=1500"]
        if self._smb is not None:
            self._config = self._dir / f"vlc-{os.getpid()}-{self._job}.conf"
            fd = os.open(self._config, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(f"[smb]\nsmb-user={self._smb[0]}\nsmb-pwd={self._smb[1]}\n")
        command = vlc_command(
            self._vlc,
            source,
            self._socket,
            volume=self.volume,
            config_file=str(self._config) if self._config else None,
            extra=extra,
        )
        try:
            if bridge_fd is not None and self._ffmpeg:
                bridge = await asyncio.create_subprocess_exec(
                    *ffmpeg_bridge_command(self._ffmpeg, self.url),
                    stdin=asyncio.subprocess.DEVNULL,
                    stdout=bridge_fd,
                    stderr=asyncio.subprocess.PIPE,
                    start_new_session=True,
                )
                self._procs.append(bridge)
                self._drains.append(asyncio.create_task(self._drain_stderr(bridge, "ffmpeg : ")))
            vlc = await asyncio.create_subprocess_exec(
                *command,
                stdin=stdin if stdin is not None else asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.PIPE,
                env=self._env,
                start_new_session=True,
            )
            self._procs.append(vlc)
        finally:
            for fd in (stdin, bridge_fd):
                if fd is not None:
                    os.close(fd)
        self._drains.append(asyncio.create_task(self._drain_stderr(vlc)))

    async def _drain_stderr(self, process: asyncio.subprocess.Process, label: str = "") -> None:
        """Garde la fin de ce que le programme écrit : c'est elle qui dit pourquoi une lecture n'a pas démarré."""
        assert process.stderr is not None
        with contextlib.suppress(Exception):
            while line := await process.stderr.readline():
                text = line.decode("utf-8", "replace")
                self._stderr_tail = (self._stderr_tail + (label + text if label else text))[-4000:]

    async def _connect_rc(self) -> RcClient | None:
        if self._rc is None and not self.finished:
            try:
                self._rc = await RcClient.connect(self._socket, 0.3)
            except OSError:
                return None
        return self._rc

    @staticmethod
    async def _is_paused(rc: RcClient) -> bool:
        """VLC 3 garde `is_playing` à 1 en pause : c'est sa commande `status` qui dit « Type 'pause' to continue »."""
        return "to continue" in await rc.ask("status")

    async def _set_playing(self, rc: RcClient, want: bool) -> None:
        """Met la lecture en marche ou en pause : « pause » de VLC bascule, on attend donc qu'il ait fini de basculer."""
        if await self._is_paused(rc) == want:  # en pause alors qu'on veut jouer, ou l'inverse
            await rc.ask("pause")
            for _ in range(15):
                if await self._is_paused(rc) != want:
                    break
                await asyncio.sleep(0.1)
        self._paused = not want

    async def status(self) -> dict[str, Any]:
        rc = await self._connect_rc()
        if rc is None:
            if time.monotonic() - self._started > RC_WAIT:
                return {"state": "starting", "position": None, "length": None, "stalled": True}
            return {"state": "starting", "position": None, "length": None}
        reply = await rc.ask("is_playing\nget_time\nget_length\nstatus")
        running, position, length = [*parse_numbers(reply), None, None, None][:3]  # type: ignore[list-item]
        paused = "to continue" in reply
        if running == 1 and paused:
            self._played, state = True, "paused"
        elif running == 1 and (self._played or (position or 0) > 0):
            # Les images (le temps de lecture avance) : avant cela VLC ouvre le flux, la chaîne n'a encore rien montré.
            self._played, state = True, "playing"
        else:
            state = "starting"
        self._paused = paused
        return {
            "state": state,
            "position": None if self.live else position,
            "length": None if self.live or not length else length,
        }

    async def command(self, name: str, **params: Any) -> None:
        rc = await self._connect_rc()
        if rc is None:
            return
        if name in ("pause", "toggle", "resume"):
            want = {"pause": False, "resume": True}.get(name)
            if want is None:  # « toggle »
                want = await self._is_paused(rc)
            await self._set_playing(rc, want)
        elif name == "seek" and not self.live:
            numbers = parse_numbers(await rc.ask("get_time\nget_length"))
            if numbers:
                target = max(0, numbers[0] + int(params.get("delta", 0)))
                if len(numbers) > 1 and numbers[1]:
                    target = min(target, max(0, numbers[1] - 2))
                await rc.ask(f"seek {target}")
        elif name == "volume":
            self.volume = int(params.get("value", self.volume))
            await rc.ask(f"volume {round(self.volume * VLC_VOLUME_MAX / 100)}")

    async def stop(self) -> None:
        self._stopped = True
        if self._rc is not None:
            self._rc.close()
        for process in reversed(self._procs):
            if process.returncode is None:
                with contextlib.suppress(ProcessLookupError, PermissionError):
                    os.killpg(process.pid, signal.SIGTERM)
        for process in self._procs:
            try:
                await asyncio.wait_for(process.wait(), 3)
            except TimeoutError:
                with contextlib.suppress(ProcessLookupError, PermissionError):
                    os.killpg(process.pid, signal.SIGKILL)
                await process.wait()
        for task in self._drains:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
        for path in (self._config, Path(self._socket)):
            if path is not None:
                with contextlib.suppress(OSError):
                    path.unlink()

    async def wait_output(self) -> None:
        """Laisse finir la lecture de ce que VLC a écrit avant de s'arrêter (sans attendre un ffmpeg qui s'éterniserait)."""
        pending = [task for task in self._drains if not task.done()]
        if pending:
            await asyncio.wait(pending, timeout=0.5)

    def error(self) -> str | None:
        if self._stopped:
            return None
        code = self._procs[-1].returncode if self._procs else None
        message = explain_exit(self._played, self.live, self._stderr_tail, code)
        if message is not None:
            self._write_log(message, code)
        return message

    def _write_log(self, message: str, code: int | None) -> None:
        """La dernière lecture ratée, en entier, pour qui voudrait comprendre : `cat $XDG_RUNTIME_DIR/mydock-player.log`."""
        text = (
            f"{time.strftime('%Y-%m-%d %H:%M:%S')} : {message}\n"
            f"Adresse : {self.url}\nCode de sortie de VLC : {code}\n\n{self._stderr_tail}\n"
        )
        try:
            fd = os.open(self._dir / LOG_NAME, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(text)
        except OSError:
            log.debug("Journal de la lecture illisible : %s", self._dir)


class DemoPlayback:
    """Une lecture simulée (aucun VLC) : pour l'interface de développement et les essais de bout en bout."""

    def __init__(self, url: str, *, live: bool, volume: int, **_: Any) -> None:
        self.url, self.live, self.volume = url, live, volume
        self.length = 0 if live else 5400
        self._position = 0.0
        self._paused = False
        self._tick = time.monotonic()
        self._stopped = False

    @property
    def finished(self) -> bool:
        return self._stopped or (not self.live and self._position >= self.length)

    async def start(self) -> None:
        self._tick = time.monotonic()

    def _advance(self) -> None:
        now = time.monotonic()
        if not self._paused:
            self._position = min(self.length or 10**9, self._position + (now - self._tick))
        self._tick = now

    async def status(self) -> dict[str, Any]:
        self._advance()
        return {
            "state": "paused" if self._paused else "playing",
            "position": None if self.live else int(self._position),
            "length": None if self.live else self.length,
        }

    async def command(self, name: str, **params: Any) -> None:
        self._advance()
        if name == "pause" or (name == "toggle" and not self._paused):
            self._paused = True
        elif name in ("resume", "toggle"):
            self._paused = False
        elif name == "seek" and not self.live:
            self._position = max(
                0, min(self.length, self._position + float(params.get("delta", 0)))
            )
        elif name == "volume":
            self.volume = int(params.get("value", self.volume))

    async def stop(self) -> None:
        self._stopped = True

    def error(self) -> str | None:
        return None


# --- fenêtres et toucher -----------------------------------------------------------------------------------------------


class Windows:
    """Quelle fenêtre est devant : la vidéo (VLC) ou le dock (Chromium).

    Par `wlrctl` (le protocole « foreign-toplevel » de labwc) ; à défaut, par `xdotool` sur la fenêtre de VLC, qui passe
    par XWayland : le dock revient devant en la réduisant, la vidéo revient en l'activant.
    """

    def __init__(self, env: dict[str, str]) -> None:
        self._env = env
        self._wlrctl = shutil.which("wlrctl")
        self._xdotool = shutil.which("xdotool")

    @property
    def available(self) -> bool:
        return self._wlrctl is not None or self._xdotool is not None

    async def _run(self, tool: str | None, *args: str) -> tuple[int, str]:
        if tool is None:
            return 1, ""
        try:
            process = await asyncio.create_subprocess_exec(
                tool, *args, stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL, env=self._env,
            )  # fmt: skip
            out, _ = await asyncio.wait_for(process.communicate(), 4)
        except (OSError, TimeoutError):
            return 1, ""
        return process.returncode or 0, out.decode("utf-8", "replace")

    async def toplevels(self) -> list[tuple[str, str]]:
        code, out = await self._run(self._wlrctl, "toplevel", "list")
        found = []
        for line in out.splitlines():
            app_id, _, title = line.partition(": ")
            if app_id:
                found.append((app_id.strip(), title.strip()))
        return found if code == 0 else []

    async def focus(self, which: str) -> bool:
        """`video` : la fenêtre de VLC ; `dock` : celle du navigateur du dock."""
        if self._wlrctl is not None and await self._focus_wayland(which):
            return True
        return self._xdotool is not None and await self._focus_x11(which)

    async def _focus_wayland(self, which: str) -> bool:
        windows = await self.toplevels()
        if which == "video":
            wanted = next((a for a, _ in windows if a.lower() in ("vlc", "org.videolan.vlc")), None)
        else:
            wanted = next((a for a, _ in windows if "chrom" in a.lower()), None) or next(
                (a for a, _ in windows if a.lower() not in ("vlc", "org.videolan.vlc")), None
            )
        if wanted is None:
            return False
        code, _ = await self._run(self._wlrctl, "toplevel", "focus", f"app_id:{wanted}")
        return code == 0

    async def _focus_x11(self, which: str) -> bool:
        code, out = await self._run(self._xdotool, "search", "--class", "vlc")
        ids = [i for i in out.split() if i.isdigit()]
        if code != 0 or not ids:
            return False
        action = "windowactivate" if which == "video" else "windowminimize"
        results = [await self._run(self._xdotool, action, i) for i in ids]
        return all(c == 0 for c, _ in results)


class TouchWatcher:
    """Prévient quand l'écran est touché : swayidle dit « resume » au premier toucher après une seconde de calme."""

    def __init__(self, env: dict[str, str], on_touch: Callable[[], None]) -> None:
        self._env, self._on_touch = env, on_touch
        self._process: asyncio.subprocess.Process | None = None
        self._task: asyncio.Task[None] | None = None
        self._binary = shutil.which("swayidle")

    @property
    def available(self) -> bool:
        return self._binary is not None

    async def start(self) -> None:
        if self._binary is None or self._process is not None:
            return
        self._process = await asyncio.create_subprocess_exec(
            self._binary, "-w", "timeout", "1", "echo idle", "resume", "echo resume",
            stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL, env=self._env,
        )  # fmt: skip
        self._task = asyncio.create_task(self._read(self._process))

    async def _read(self, process: asyncio.subprocess.Process) -> None:
        assert process.stdout is not None
        with contextlib.suppress(Exception):
            while line := await process.stdout.readline():
                if line.strip() == b"resume":
                    self._on_touch()

    async def stop(self) -> None:
        process, self._process = self._process, None
        if process is not None and process.returncode is None:
            with contextlib.suppress(ProcessLookupError):
                process.terminate()
            with contextlib.suppress(Exception):
                await asyncio.wait_for(process.wait(), 2)
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None


# --- l'assistant ---------------------------------------------------------------------------------------------------


class _NoWindows:
    available = True

    async def focus(self, which: str) -> bool:
        return True


class _NoTouch:
    available = True

    async def start(self) -> None: ...

    async def stop(self) -> None: ...


def session_environment(base: dict[str, str] | None = None) -> dict[str, str]:
    """L'environnement de la session graphique : l'écran Wayland et, pour VLC (qui passe par XWayland), l'écran X11."""
    env = dict(os.environ if base is None else base)
    if not env.get("DISPLAY"):
        sockets = sorted(glob.glob("/tmp/.X11-unix/X*"))
        if sockets:
            env["DISPLAY"] = ":" + sockets[0].rsplit("X", 1)[1]
    env.pop("QT_QPA_PLATFORM", None)
    return env


class Session:
    def __init__(
        self,
        url: str = DEFAULT_URL,
        *,
        demo: bool = False,
        env: dict[str, str] | None = None,
        runtime_dir: Path | None = None,
        extra_vlc_args: Sequence[str] | None = None,
        vlc: str | None = None,
        ffmpeg: str | None = None,
        live555: bool | None = None,
    ) -> None:
        self.url = url
        self.demo = demo
        self.env = session_environment(env)
        self.runtime_dir = runtime_dir or Path(
            self.env.get("XDG_RUNTIME_DIR") or tempfile.gettempdir()
        )
        self.extra = list(extra_vlc_args) if extra_vlc_args is not None else read_flags()
        self.vlc = vlc if vlc is not None else find_vlc()
        self.ffmpeg = ffmpeg if ffmpeg is not None else shutil.which("ffmpeg")
        self.live555 = has_live555() if live555 is None else live555
        self.windows: Any = Windows(self.env)
        self.touch: Any = TouchWatcher(self.env, self._touched)
        if demo:  # pas d'écran réel : ni fenêtres à ordonner ni toucher à guetter
            self.windows, self.touch = _NoWindows(), _NoTouch()
        self.front = "dock"
        self._front_since = 0.0
        self._playback: Playback | None = None
        self._job = 0
        self._monitor: asyncio.Task[None] | None = None
        self._send: Callable[[dict[str, Any]], Any] | None = None
        self._loop: asyncio.AbstractEventLoop | None = None

    def hello(self) -> dict[str, Any]:
        return {
            "t": "hello",
            "v": 1,
            "vlc": True if self.demo else self.vlc is not None,
            "live555": self.live555,
            "ffmpeg": self.ffmpeg is not None,
            # Sans de quoi revenir au dock d'un toucher, la vidéo plein écran garderait l'écran : on ne la lance pas.
            "touch": True if self.demo else (self.touch.available and self.windows.available),
            "demo": self.demo,
        }

    # --- messages du dock ------------------------------------------------------------------------------------

    async def handle(self, message: dict[str, Any], send: Callable[[dict[str, Any]], Any]) -> None:
        self._send = send
        if message.get("t") != "cmd":
            return
        name = message.get("name")
        job = message.get("job")
        if name == "play":
            await self._play(message)
        elif name == "stop":
            if job == self._job:
                await self._stop_playback()
        elif name == "front":
            await self._set_front(str(message.get("to")))
        elif name in ("pause", "resume", "toggle", "seek", "volume") and self._playback is not None:
            params = {k: v for k, v in message.items() if k not in ("t", "name", "job")}
            await self._playback.command(str(name), **params)

    async def _play(self, message: dict[str, Any]) -> None:
        await self._stop_playback()
        self._job = int(message.get("job", 0))
        url = str(message.get("url", ""))
        live = bool(message.get("live"))
        volume = int(message.get("volume", 70))
        if self.demo:
            playback: Playback = DemoPlayback(url, live=live, volume=volume)
        else:
            if self.vlc is None:
                await self._ended(
                    self._job,
                    "VLC est absent du Pi : sudo apt install vlc-bin vlc-plugin-base vlc-plugin-video-output.",
                )
                return
            smb = (
                (str(message["smb_user"]), str(message.get("smb_password", "")))
                if message.get("smb_user")
                else None
            )
            playback = VlcPlayback(
                url, live=live, volume=volume, vlc=self.vlc, ffmpeg=self.ffmpeg, live555=self.live555,
                runtime_dir=self.runtime_dir, env=session_environment(self.env), extra=self.extra, smb=smb, job=self._job,
            )  # fmt: skip
        try:
            await playback.start()
        except (OSError, RuntimeError) as exc:
            await playback.stop()
            await self._ended(
                self._job,
                str(exc)
                if isinstance(exc, RuntimeError)
                else f"VLC ne démarre pas ({exc.strerror}).",
            )
            return
        self._playback = playback
        self.front = "video"
        self._front_since = time.monotonic()
        await self.touch.start()
        self._monitor = asyncio.create_task(self._watch(playback, self._job))

    async def _watch(self, playback: Playback, job: int) -> None:
        focused = False
        try:
            while True:
                if playback.finished:
                    if (wait_output := getattr(playback, "wait_output", None)) is not None:
                        await wait_output()
                    await self._ended(job, playback.error(), playback)
                    return
                status = await playback.status()
                if status.get("stalled"):
                    await playback.stop()
                    await self._ended(job, "VLC ne répond pas.", playback)
                    return
                if not focused and status["state"] == "playing" and self.front == "video":
                    focused = True
                    await self.windows.focus("video")
                await self._report(job, status)
                await asyncio.sleep(POLL_SECONDS)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("Suivi de la lecture interrompu")
            await playback.stop()
            await self._ended(job, "L'assistant de lecture a rencontré une erreur.", playback)

    async def _report(self, job: int, status: dict[str, Any]) -> None:
        playback = self._playback
        await self._emit(
            {
                "t": "state",
                "job": job,
                "state": status["state"],
                "position": status.get("position"),
                "length": status.get("length"),
                "volume": getattr(playback, "volume", None),
                "front": self.front,
            }
        )

    async def _ended(self, job: int, error: str | None, playback: Playback | None = None) -> None:
        if playback is not None and playback is not self._playback:
            return
        self._playback = None
        self.front = "dock"
        await self.touch.stop()
        await self.windows.focus("dock")
        await self._emit({"t": "ended", "job": job, "error": error})

    async def _stop_playback(self) -> None:
        playback, monitor = self._playback, self._monitor
        self._playback = self._monitor = None
        if monitor is not None and monitor is not asyncio.current_task():
            monitor.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await monitor
        if playback is not None:
            await playback.stop()
            self.front = "dock"
            await self.touch.stop()
            await self.windows.focus("dock")

    async def _set_front(self, to: str) -> None:
        if to not in ("video", "dock"):
            return
        if to == "video" and self._playback is None:
            return
        self.front = to
        if to == "video":
            self._front_since = time.monotonic()
        await self.windows.focus(to)

    def _touched(self) -> None:
        """Un toucher pendant la vidéo : le dock revient devant (la vidéo continue derrière)."""
        if (
            self.front == "video"
            and time.monotonic() - self._front_since > TOUCH_GRACE
            and self._loop is not None
        ):
            self._loop.create_task(self._set_front("dock"))

    async def _emit(self, message: dict[str, Any]) -> None:
        if self._send is not None:
            result = self._send(message)
            if asyncio.iscoroutine(result):
                await result

    # --- la liaison avec le dock -------------------------------------------------------------------------------

    async def serve(self) -> None:
        from websockets.asyncio.client import (
            connect,
        )  # importé ici : inutile pour les tests des fonctions pures

        self._loop = asyncio.get_running_loop()
        delay = 1.0
        while True:
            try:
                async with connect(
                    self.url, open_timeout=3, ping_interval=20, proxy=None, max_size=64 * 1024
                ) as ws:
                    delay = 1.0

                    async def send(message: dict[str, Any], ws: Any = ws) -> None:
                        await ws.send(json.dumps(message, separators=(",", ":")))

                    self._send = send
                    await send(self.hello())
                    async for raw in ws:
                        try:
                            message = json.loads(raw)
                        except ValueError:
                            continue
                        if isinstance(message, dict):
                            await self.handle(message, send)
            except asyncio.CancelledError:
                await self._stop_playback()
                raise
            except (
                Exception
            ) as exc:  # le dock redémarre à chaque mise à jour : on le rejoint dès qu'il revient
                log.debug("Liaison avec le dock perdue (%s)", exc)
            self._send = None
            await self._stop_playback()
            await asyncio.sleep(delay)
            delay = min(delay * 2, 10.0)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="mydock-dock player-session", description=__doc__.split("\n")[0]
    )
    parser.add_argument(
        "--url",
        default=os.environ.get("MYDOCK_PLAYER_URL", DEFAULT_URL),
        help="adresse du dock (ws://127.0.0.1:8080/ws/player)",
    )
    parser.add_argument("--demo", action="store_true", help="lecture simulée, sans VLC (essais)")
    parser.add_argument("-v", "--verbose", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s %(name)s: %(message)s",
    )
    session = Session(args.url, demo=args.demo)
    try:
        asyncio.run(session.serve())
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
