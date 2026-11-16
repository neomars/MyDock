"""Free TV sur l'écran du dock : tv.free.fr dans une fenêtre de Chromium, lancée et suivie par l'assistant de session.

C'est le troisième occupant de l'écran, après VLC (le Kiosque) et MAME (Retrogame) : même contrat (`start`, `status`,
`command`, `stop`, `finished`, `error`), donc un seul occupant à la fois, le même retour au dock, le même journal.

Pourquoi un navigateur : certaines chaînes manquent au flux local de la Freebox (droits de diffusion) alors que Free TV
les donne, avec l'abonnement de l'utilisateur. Free TV lit des flux protégés (Widevine), ce que VLC ne sait pas faire.

Ce que l'assistant lance (jamais une commande venue de l'écran : le dock ne dit que « ouvre Free TV ») :

    chromium --app=https://tv.free.fr --ozone-platform=wayland --password-store=basic --user-data-dir=<profil>
             --force-device-scale-factor=0.7 --lang=fr-FR --user-agent=<identité de bureau x86_64> + freetv.flags

  * `--app` : une fenêtre sans barre d'adresse. Son identifiant Wayland (`app_id`) vient de l'adresse ouverte : il contient
    toujours `tv.free.fr`, ce qui la distingue de la fenêtre du dock (voir `Windows.classify`) ; `--class` est ignoré en
    mode application, et `wlrctl` compare l'identifiant exactement ;
  * un profil à lui (`~/.config/mydock-freetv`, droits 700), séparé de celui du dock : la connexion à Free y reste d'un
    démarrage à l'autre, et l'interface du dock n'en voit jamais rien (ni cookies ni identifiants) ;
  * `--password-store=basic` : sans lui Chromium propose de créer un trousseau gnome-keyring, qui n'existe pas dans la
    session du kiosque ;
  * l'identité (« user agent ») : Free refuse de lire sur « Linux aarch64 » (erreur Shaka 6001). On présente donc un Chromium
    de bureau x86_64 **de la même version que celui installé**. C'est un contournement fragile : si Free change ses règles
    (ou lit autre chose que cette chaîne, comme `navigator.userAgentData`), il cessera de marcher ; le réglage
    `[freetv] user_agent` de dock.toml permet de le changer ou de le retirer.

Un second Chromium lancé sur le même profil ne fait que transmettre sa demande au premier (et ouvre une autre fenêtre) : on
ne lance donc jamais deux fois, et on referme d'abord un Chromium de Free TV resté ouvert (assistant redémarré).
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import re
import shutil
import signal
import socket
import time
from collections import deque
from collections.abc import Callable, Iterable, Sequence
from pathlib import Path
from typing import Any

from mydock_dock import playerlog

log = logging.getLogger("mydock.player")

FREETV_URL = "https://tv.free.fr"
#: Ce que contient toujours l'identifiant de la fenêtre (voir `--app` plus haut) : « chrome-tv.free.fr__-Default »,
#: « chromium-tv.free.fr__-Default »…
FREETV_HOST = "tv.free.fr"
#: Options supplémentaires de Chromium pour Free TV, une par ligne (# = commentaire) ; la variable donne un autre fichier.
FLAGS_FILE = "/etc/mydock-kiosk/freetv.flags"
FLAGS_ENV = "MYDOCK_FREETV_FLAGS"
#: Pour les essais : un autre dossier de profil.
PROFILE_ENV = "MYDOCK_FREETV_PROFILE"
PROFILE_NAME = "mydock-freetv"  # sous ~/.config : ne commence jamais comme le profil du dock (~/.cache/mydock-chromium)
USER_AGENT_AUTO = "auto"
#: L'identité de bureau que l'on présente à Free : la version de Chromium y est remplacée par celle d'ici.
DESKTOP_USER_AGENT = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/{major}.0.0.0 Safari/537.36"
#: Où Widevine se trouve selon la façon dont il a été installé (paquet libwidevinecdm0 de Raspberry Pi OS, Debian, Chromium).
WIDEVINE_DIRS = (
    "/opt/WidevineCdm",
    "/usr/lib/chromium/WidevineCdm",
    "/usr/lib/chromium-browser/WidevineCdm",
    "/usr/lib/WidevineCdm",
)
MAX_LOGGED_LINES = 300
#: Sans pouvoir lister les fenêtres, Free TV est tenu pour affiché au bout de ce temps.
BLIND_SHOWN_AFTER = 8.0
CLOSE_WAIT = 5.0
TERM_WAIT = 3.0
KILL_WAIT = 2.0

_VERSION = re.compile(r"(\d+)\.\d+\.\d+")


# --- ce que le Pi offre ----------------------------------------------------------------------------------------------


def find_chromium(which: Callable[[str], str | None] = shutil.which) -> str | None:
    return which("chromium") or which("chromium-browser")


def widevine_info(dirs: Iterable[str] = WIDEVINE_DIRS) -> tuple[str, str | None] | None:
    """(le dossier de Widevine, sa version) s'il est installé, `None` sinon : il faut son manifeste ET sa bibliothèque."""
    for directory in dirs:
        root = Path(directory)
        manifest = root / "manifest.json"
        library = (
            next(iter(sorted(root.glob("**/libwidevinecdm.so"))), None) if root.is_dir() else None
        )
        if library is None or not manifest.is_file():
            continue
        try:
            version = (
                str(json.loads(manifest.read_text(encoding="utf-8")).get("version") or "") or None
            )
        except (OSError, ValueError):
            version = None
        return str(root), version
    return None


def has_widevine(dirs: Iterable[str] = WIDEVINE_DIRS) -> bool:
    return widevine_info(dirs) is not None


async def chromium_version(chromium: str) -> str | None:
    """La première ligne de `chromium --version` (« Chromium 156.0.7339.80 built on Debian… »)."""
    try:
        process = await asyncio.create_subprocess_exec(
            chromium,
            "--version",
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
            env={**os.environ, "LC_ALL": "C.UTF-8"},
        )
        out, _ = await asyncio.wait_for(process.communicate(), 10)
    except (OSError, TimeoutError):
        return None
    lines = out.decode("utf-8", "replace").strip().splitlines()
    return lines[0][:120] if lines else None


def major_of(version: str | None) -> int | None:
    """« Chromium 156.0.7339.80 … » -> 156."""
    match = _VERSION.search(version or "")
    return int(match.group(1)) if match else None


def compute_user_agent(setting: str, major: int | None) -> str | None:
    """L'identité à présenter à Free TV, d'après `[freetv] user_agent` et la version de Chromium ; `None` : celle d'origine.

    « auto » : un Chromium de bureau x86_64 de la même version. Vide : l'identité d'origine (aucune option). Autre chose :
    cette chaîne, où `{major}` devient la version de Chromium."""
    text = (setting or "").strip()
    if not text:
        return None
    if text == USER_AGENT_AUTO:
        return DESKTOP_USER_AGENT.format(major=major) if major else None
    if "{major}" in text:
        return text.replace("{major}", str(major)) if major else None
    return text


def read_flags(path: str | None = None) -> list[str]:
    path = path or os.environ.get(FLAGS_ENV) or FLAGS_FILE
    try:
        lines = Path(path).read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    return [line.strip() for line in lines if line.strip() and not line.lstrip().startswith("#")][
        :40
    ]


def profile_dir(env: dict[str, str]) -> Path | None:
    """Le profil de Free TV : un dossier à lui sous le dossier personnel de l'utilisateur de la session."""
    override = env.get(PROFILE_ENV) or os.environ.get(PROFILE_ENV)
    if override:
        return Path(override)
    home = env.get("HOME") or os.environ.get("HOME")
    return Path(home) / ".config" / PROFILE_NAME if home else None


def chromium_command(
    chromium: str,
    profile: Path,
    *,
    url: str = FREETV_URL,
    user_agent: str | None = None,
    scale: float = 0.7,
    fullscreen: bool = True,
    extra: Sequence[str] = (),
) -> list[str]:
    """La ligne de commande de Chromium : une liste, construite ici, avec l'adresse en dernier (jamais une option)."""
    command = [
        chromium,
        f"--app={url}",
        "--ozone-platform=wayland",
        "--password-store=basic",
        f"--user-data-dir={profile}",
        f"--force-device-scale-factor={scale:g}",
        "--lang=fr-FR",
        "--no-first-run",
        "--no-default-browser-check",
        "--noerrdialogs",
        "--hide-crash-restore-bubble",
        "--autoplay-policy=no-user-gesture-required",
    ]
    if fullscreen:
        command.append("--start-fullscreen")
    if user_agent:
        command.append(f"--user-agent={user_agent}")
    # Les options de freetv.flags en dernier : Chromium garde la dernière valeur d'une option répétée.
    command.extend(extra)
    return command


def free_tv_environment(base: dict[str, str]) -> dict[str, str]:
    """L'environnement de Chromium : celui de la session (pas le « C.UTF-8 » imposé à VLC), en français."""
    env = dict(base)
    env.pop("QT_QPA_PLATFORM", None)
    env.pop("DISPLAY", None)  # Chromium doit parler Wayland : jamais retomber sur XWayland
    env["LANGUAGE"] = "fr"
    return env


# --- les Chromium de Free TV restés ouverts -------------------------------------------------------------------------


def stale_pids(profile: Path, proc_root: str = "/proc", *, own: int | None = None) -> list[int]:
    """Les processus de Chromium qui tournent sur ce profil (leur ligne de commande porte `--user-data-dir=<profil>`)."""
    wanted = f"--user-data-dir={profile}"
    mine = os.getpid() if own is None else own
    found: list[int] = []
    try:
        entries = os.listdir(proc_root)
    except OSError:
        return found
    for name in entries:
        if not name.isdigit() or int(name) == mine:
            continue
        try:
            raw = Path(proc_root, name, "cmdline").read_bytes()
        except OSError:
            continue
        if wanted in raw.decode("utf-8", "replace").split("\0"):
            found.append(int(name))
    return found


async def kill_stale(profile: Path, proc_root: str = "/proc") -> int:
    """Ferme (SIGTERM, puis SIGKILL) un Chromium resté sur le profil de Free TV. Rend combien de processus étaient là."""
    pids = stale_pids(profile, proc_root)
    found = len(pids)
    if not pids:
        return 0
    for sig, wait in ((signal.SIGTERM, TERM_WAIT), (signal.SIGKILL, KILL_WAIT)):
        for pid in pids:
            with contextlib.suppress(ProcessLookupError, PermissionError):
                os.kill(pid, sig)
        deadline = time.monotonic() + wait
        while time.monotonic() < deadline and stale_pids(profile, proc_root):  # noqa: ASYNC110
            await asyncio.sleep(0.1)
        pids = stale_pids(profile, proc_root)
        if not pids:
            break
    return found


# --- pourquoi Chromium s'est arrêté ---------------------------------------------------------------------------------

_NOISE = (
    "dbus",
    "bus.cc",
    "gcm",
    "viz_main",
    "libva",
    "vaapi",
    "gpu_",
    "angle",
    "mesa",
    "egl",
    "ozone_platform",
    "xdg_",
)


def explain_exit(shown: bool, tail: str, code: int | None = None) -> str | None:
    """Pourquoi Chromium s'est arrêté sans avoir ouvert Free TV ; `None` : fin normale (la fenêtre a été montrée, puis fermée)."""
    if shown and (code is None or code >= 0):
        return None
    lowered = tail.lower()
    if code is not None and code < 0:
        try:
            name = signal.Signals(-code).name
        except ValueError:
            name = f"signal {-code}"
        return (
            f"Chromium s'est arrêté brutalement ({name}) : voir « chromium » dans /var/log/mydock/player.log. "
            "Des options à essayer dans /etc/mydock-kiosk/freetv.flags : --disable-gpu, ou --use-gl=angle."
        )
    if any(
        word in lowered
        for word in ("missing x server", "failed to open display", "cannot open display")
    ):
        return "Chromium n'arrive pas à s'afficher sur l'écran du dock (Wayland indisponible dans cette session)."
    if "profile appears to be in use" in lowered or "singletonlock" in lowered:
        return "Le profil de Free TV est déjà utilisé par un autre Chromium : ferme Free TV, puis réessaie."
    if "sandbox" in lowered and ("fail" in lowered or "no usable" in lowered or "error" in lowered):
        return "Chromium refuse de démarrer (son bac à sable n'est pas utilisable pour cet utilisateur)."
    return (
        "Chromium s'est arrêté avant d'avoir ouvert Free TV"
        + (f" (code {code})" if code else "")
        + " : voir /var/log/mydock/player.log."
    )


def _noise(text: str) -> bool:
    lowered = text.lower()
    return any(word in lowered[:80] for word in _NOISE)


# --- la lecture ---------------------------------------------------------------------------------------------------


class FreeTvPlayback:
    """Free TV dans Chromium, en plein écran sur l'écran du dock."""

    volume: int | None = None

    def __init__(
        self,
        *,
        chromium: str,
        windows: Any,
        env: dict[str, str],
        runtime_dir: Path,
        job: int = 0,
        user_agent_setting: str = USER_AGENT_AUTO,
        scale: float = 0.7,
        fullscreen: bool = True,
        url: str = FREETV_URL,
        extra: Sequence[str] | None = None,
        profile: Path | None = None,
        major: int | None = None,
        host: str = FREETV_HOST,
    ) -> None:
        self._chromium, self._windows = chromium, windows
        self._env = free_tv_environment(env)
        self._job = job
        self._setting, self._scale, self._url = user_agent_setting, scale, url
        self._fullscreen = fullscreen
        self._extra = list(read_flags() if extra is None else extra)
        self._profile = profile or profile_dir(env)
        self._major = major
        self._host = host
        self._process: asyncio.subprocess.Process | None = None
        self._drains: list[asyncio.Task[None]] = []
        self._tail: deque[str] = deque(maxlen=60)
        self._logged = 0
        self._started_at = 0.0
        self._shown_at: float | None = None
        self._app_id: str | None = None
        self._fullscreened = False
        self._stopped = False
        self.how_stopped: str | None = None
        self.user_agent: str | None = None

    # --- lancement ---------------------------------------------------------------------------------------------

    @property
    def finished(self) -> bool:
        return self._process is not None and self._process.returncode is not None

    @property
    def shown(self) -> bool:
        return self._shown_at is not None

    @property
    def app_id(self) -> str | None:
        """L'identifiant de la fenêtre de Free TV, tel que labwc le liste (connu quand elle est affichée)."""
        return self._app_id

    def command_line(self) -> list[str]:
        assert self._profile is not None
        return chromium_command(
            self._chromium,
            self._profile,
            url=self._url,
            user_agent=self.user_agent,
            scale=self._scale,
            fullscreen=self._fullscreen,
            extra=self._extra,
        )

    async def start(self) -> None:
        if self._profile is None:
            raise RuntimeError(
                "Free TV ne trouve pas le dossier personnel de l'utilisateur de l'écran (HOME)."
            )
        try:
            self._profile.mkdir(parents=True, exist_ok=True, mode=0o700)
            os.chmod(
                self._profile, 0o700
            )  # jamais lisible par les autres : il garde la connexion à Free
        except OSError as exc:
            raise RuntimeError(
                f"Free TV ne peut pas écrire son profil dans {self._profile} ({exc.strerror or exc})."
            ) from exc
        stale = await kill_stale(self._profile)
        if stale:
            playerlog.event(
                f"free tv {self._job} : un Chromium de Free TV était resté ouvert, refermé ({stale} processus)"
            )
        if self._major is None:
            self._major = major_of(await chromium_version(self._chromium))
        self.user_agent = compute_user_agent(self._setting, self._major)
        command = self.command_line()
        playerlog.event(
            f"free tv {self._job} : lancement de Chromium {self._major or '?'} — identité : "
            f"{self.user_agent or 'celle d’origine'} — profil {self._profile} — {' '.join(command)} "
            f"(WAYLAND_DISPLAY={self._env.get('WAYLAND_DISPLAY', '-')}, PULSE_SINK={self._env.get('PULSE_SINK', '-')})"
        )
        self._process = await asyncio.create_subprocess_exec(
            *command,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=self._env,
            start_new_session=True,  # SIGKILL à tout le groupe : ni le GPU ni les moteurs de rendu ne restent derrière
        )
        self._started_at = time.monotonic()
        self._drains = [
            asyncio.create_task(self._drain(self._process.stdout)),
            asyncio.create_task(self._drain(self._process.stderr)),
        ]

    async def _drain(self, stream: asyncio.StreamReader | None) -> None:
        if stream is None:
            return
        with contextlib.suppress(Exception):
            while raw := await stream.readline():
                text = raw.decode("utf-8", "replace").rstrip("\r\n")
                if not text.strip():
                    continue
                if not _noise(text):
                    self._tail.append(text)
                if self._logged < MAX_LOGGED_LINES:
                    self._logged += 1
                    playerlog.line("chromium", text)
                    if self._logged == MAX_LOGGED_LINES:
                        playerlog.event(
                            f"chromium : la suite est omise du journal ({MAX_LOGGED_LINES} lignes)"
                        )

    # --- suivi -------------------------------------------------------------------------------------------------

    async def _window(self) -> tuple[str | None, list[tuple[str, str]]]:
        listed = await self._windows.toplevels() if hasattr(self._windows, "toplevels") else []
        found = next((app_id for app_id, _ in listed if self._host in app_id.lower()), None)
        return found, listed

    async def status(self) -> dict[str, Any]:
        """« starting » tant que la fenêtre de Free TV n'est pas là, « playing » ensuite (le dock arrête un Chromium qui n'apparaît pas)."""
        if self._shown_at is None and not self.finished:
            found, listed = await self._window()
            blind = not listed and time.monotonic() - self._started_at >= BLIND_SHOWN_AFTER
            if found or blind:
                self._shown_at = time.monotonic()
                self._app_id = found
                playerlog.event(
                    f"free tv {self._job} : la fenêtre est affichée après {self._shown_at - self._started_at:.1f} s "
                    f"(identifiant « {found or '?'} »)"
                    + (" (fenêtres illisibles : on le suppose)" if blind and not found else "")
                )
        if self._shown_at is not None and not self._fullscreened and self._app_id:
            self._fullscreened = True
            # Plein écran : repli si `--start-fullscreen` n'a pas été respecté en mode application. Sinon (fullscreen = false
            # dans dock.toml) la fenêtre est seulement maximisée : le clavier à l'écran, une couche du compositeur, s'affiche
            # alors au-dessus d'elle (labwc le cache derrière une fenêtre en plein écran quand le dock l'est aussi).
            place = getattr(self._windows, "fullscreen" if self._fullscreen else "maximize", None)
            if place is not None:
                await place(self._app_id)
        return {
            "state": "playing" if self._shown_at is not None else "starting",
            "position": None,
            "length": None,
            "seekable": False,
        }

    async def command(self, name: str, **params: Any) -> None:
        """Rien à piloter d'ici : on touche la page (pause, volume, plein écran sont ceux de Free TV)."""

    # --- arrêt -------------------------------------------------------------------------------------------------

    async def _wait_exit(self, seconds: float) -> bool:
        process = self._process
        if process is None or process.returncode is not None:
            return True
        try:
            await asyncio.wait_for(process.wait(), seconds)
        except TimeoutError:
            return False
        return True

    async def _close_windows(self) -> int:
        """Ferme la fenêtre de Free TV comme le ferait un clic sur sa croix : Chromium s'arrête alors proprement."""
        closer = getattr(self._windows, "close_app", None)
        if closer is None:
            return 0
        return int(await closer(self._host))

    async def stop(self) -> None:
        """Ferme Free TV : sa fenêtre d'abord (Chromium garde alors la connexion), puis SIGTERM, puis SIGKILL."""
        if self._stopped:
            return
        self._stopped = True
        process = self._process
        if process is not None and process.returncode is None:
            playerlog.event(f"free tv {self._job} : arrêt demandé, fenêtre fermée")
            closed = await self._close_windows()
            if await self._wait_exit(CLOSE_WAIT):
                self.how_stopped = "fenêtre fermée"
            else:
                playerlog.event(
                    f"free tv {self._job} : Chromium n'a pas quitté en {CLOSE_WAIT:g} s ({closed} fenêtre fermée), SIGTERM"
                )
                with contextlib.suppress(ProcessLookupError, PermissionError):
                    os.killpg(process.pid, signal.SIGTERM)
                if await self._wait_exit(TERM_WAIT):
                    self.how_stopped = "SIGTERM"
                else:
                    with contextlib.suppress(ProcessLookupError, PermissionError):
                        os.killpg(process.pid, signal.SIGKILL)
                    playerlog.event(f"free tv {self._job} : Chromium ne répond pas, tué (SIGKILL)")
                    await self._wait_exit(KILL_WAIT)
                    self.how_stopped = "tué"
            playerlog.event(f"free tv {self._job} : arrêté ({self.how_stopped})")
        elif process is not None:
            self.how_stopped = "déjà fini"
        await self._cleanup()

    async def _cleanup(self) -> None:
        process = self._process
        if (
            process is not None and process.returncode is None
        ):  # jamais laissé derrière, quoi qu'il arrive
            with contextlib.suppress(ProcessLookupError, PermissionError):
                os.killpg(process.pid, signal.SIGKILL)
        if process is not None and self._profile is not None:
            # Les enfants de Chromium (GPU, moteurs de rendu) peuvent survivre à leur parent : on les cherche par le profil.
            await kill_stale(self._profile)
        for task in self._drains:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
        self._drains = []

    async def wait_output(self) -> None:
        """Laisse finir la lecture de ce que Chromium a écrit avant de dire pourquoi il s'est arrêté."""
        pending = [task for task in self._drains if not task.done()]
        if pending:
            await asyncio.wait(pending, timeout=0.5)

    def error(self) -> str | None:
        if self._stopped:
            return None
        code = self._process.returncode if self._process is not None else None
        message = explain_exit(self.shown, "\n".join(self._tail), code)
        playerlog.event(
            f"free tv {self._job} : fin de Chromium, code {code}, "
            + ("fenêtre fermée normalement." if message is None else "arrêt anormal.")
        )
        if message is not None:
            playerlog.failure(self._job, message)
        return message


class DemoFreeTvPlayback:
    """Une fausse fenêtre de Free TV pour le mode démo : « affichée » au bout d'une seconde, jusqu'à ce qu'on l'arrête."""

    volume: int | None = None

    def __init__(self, **_: Any) -> None:
        self._started = time.monotonic()
        self._stopped = False
        self.how_stopped: str | None = None
        self.user_agent: str | None = compute_user_agent(USER_AGENT_AUTO, 156)

    @property
    def finished(self) -> bool:
        return self._stopped

    @property
    def shown(self) -> bool:
        return time.monotonic() - self._started >= 1.0

    async def start(self) -> None:
        self._started = time.monotonic()

    async def status(self) -> dict[str, Any]:
        return {
            "state": "playing" if self.shown else "starting",
            "position": None,
            "length": None,
            "seekable": False,
        }

    async def command(self, name: str, **params: Any) -> None:
        return None

    async def stop(self) -> None:
        self._stopped = True
        self.how_stopped = "démonstration"

    def error(self) -> str | None:
        return None


async def host_resolves(host: str = FREETV_HOST, timeout: float = 4.0) -> bool:
    """Le Pi sait-il trouver tv.free.fr ? (un conseil pour le journal : Chromium montre lui-même sa page d'erreur)"""
    loop = asyncio.get_running_loop()
    try:
        await asyncio.wait_for(loop.getaddrinfo(host, 443, type=socket.SOCK_STREAM), timeout)
    except (OSError, TimeoutError):
        return False
    return True
