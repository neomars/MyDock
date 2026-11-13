"""MAME sur l'écran du dock : un jeu d'arcade lancé, suivi et arrêté par l'assistant de session.

C'est le pendant de `VlcPlayback` pour `player_session.py` : même contrat (`start`, `status`, `command`, `stop`,
`finished`, `error`), si bien que l'écran du dock n'a qu'un occupant à la fois — la vidéo ou le jeu, jamais les deux —
et que le chien de garde, le retour au dock et le journal servent aux deux.

Ce que l'assistant lance (jamais une commande venue de l'écran : seulement un nom de jeu, qu'il revérifie) :

    mame <jeu> -rompath <dossier des ROMs> -nowindow -video accel -skip_gameinfo
         -homepath … -inipath … -cfg_directory … -nvram_directory … (tout sous le dossier du kiosque : jamais /root, jamais
         le dossier des ROMs)  -ctrlr mydock  -plugin hiscore  -autoboot_script <sortie propre>  + /etc/mydock-kiosk/mame.flags

Arrêter MAME demande de l'obstination, d'où la cascade de `stop()` (essayée sur le vrai MAME 0.264, sous labwc) :
MAME ignore SIGTERM et SIGINT. Il quitte proprement (et écrit alors les meilleurs scores et la mémoire du jeu) par son
propre script Lua, qui regarde un fichier-drapeau ; à défaut, en fermant sa fenêtre (`wlrctl toplevel close`) ; en dernier
recours, par SIGKILL.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import re
import signal
import time
from collections import deque
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from mydock_dock import playerlog

log = logging.getLogger("mydock.player")

#: Le nom d'un jeu pour MAME : l'assistant ne lance rien d'autre, quoi que dise le dock.
GAME_RE = re.compile(r"^[a-z0-9_]{1,24}$")
#: Options supplémentaires de MAME, une par ligne (# = commentaire) ; la variable donne un autre fichier (essais).
FLAGS_FILE = "/etc/mydock-kiosk/mame.flags"
FLAGS_ENV = "MYDOCK_MAME_FLAGS"
HOME_ENV = "MYDOCK_MAME_HOME"
MAX_LOGGED_LINES = 400

#: Le script Lua qui rend la sortie propre : MAME l'exécute (`-autoboot_script`) et s'arrête, comme s'il avait reçu Échap,
#: dès que le fichier-drapeau existe. Il le regarde toutes les 15 images (environ 4 fois par seconde).
QUIT_LUA = """\
-- MyDock : sortie propre demandée de l'extérieur (écrit par l'assistant, remplacé à chaque lancement).
local flag = os.getenv("MYDOCK_QUIT_FLAG")
local n = 0
emu.register_frame_done(function()
  n = n + 1
  if flag and n % 15 == 0 then
    local f = io.open(flag, "r")
    if f then f:close(); manager.machine:exit() end
  end
end)
"""

#: La configuration des commandes : pièce et départ sur « Select » et « Start » de la manette, en plus du clavier.
#: (MAME le fait déjà ainsi par défaut ; le fichier le dit explicitement et sert de point de départ aux réglages.)
CTRLR_CFG = (
    '<?xml version="1.0"?>\n<mameconfig version="10">\n\t<system name="default">\n\t\t<input>\n'
    + "".join(
        f'\t\t\t<port type="{kind}{player}">\n'
        f'\t\t\t\t<newseq type="standard">{keys[player - 1]} OR JOYCODE_{player}_{pad}</newseq>\n'
        "\t\t\t</port>\n"
        for kind, pad, keys in (
            ("COIN", "SELECT", ("KEYCODE_5", "KEYCODE_6", "KEYCODE_7", "KEYCODE_8")),
            ("START", "START", ("KEYCODE_1", "KEYCODE_2", "KEYCODE_3", "KEYCODE_4")),
        )
        for player in (1, 2, 3, 4)
    )
    + "\t\t</input>\n\t</system>\n</mameconfig>\n"
)

#: Le dossier des réglages de la distribution : son `mame.ini` dit où sont les plugins, les polices, les langues… MAME ne lit
#: que le premier `mame.ini` trouvé dans `-inipath` ; le nôtre n'en a pas, donc c'est celui-là (Debian : /etc/mame).
SYSTEM_INI = "/etc/mame"

# Les codes de sortie de MAME (src/emu/main.h : MAMERR_*).
EXIT_MISSING_FILES = 2
EXIT_FATAL = 3
EXIT_DEVICE = 4
EXIT_NO_SUCH_GAME = 5

_MISSING = re.compile(
    r"(\S+) (?:NOT FOUND|WRONG LENGTH|INCORRECT CHECKSUM)(?: \(tried in ([\w.,\s]+?)\))?", re.I
)
_UNKNOWN = re.compile(r"Unknown system '([^']*)'", re.I)


def read_flags(path: str | None = None) -> list[str]:
    """Les options de `mame.flags` : une par ligne, sans les commentaires (40 au plus)."""
    path = path or os.environ.get(FLAGS_ENV) or FLAGS_FILE
    try:
        lines = Path(path).read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    return [line.strip() for line in lines if line.strip() and not line.lstrip().startswith("#")][
        :40
    ]


@dataclass(frozen=True)
class MameDirs:
    """Les dossiers de MAME, tous sous celui du kiosque : MAME n'écrit rien d'autre part."""

    root: Path

    @property
    def home(self) -> Path:
        return self.root / "home"  # le plugin des meilleurs scores écrit dans home/hiscore

    @property
    def ini(self) -> Path:
        return self.root / "ini"

    @property
    def cfg(self) -> Path:
        return self.root / "cfg"

    @property
    def nvram(self) -> Path:
        return self.root / "nvram"

    @property
    def input(self) -> Path:
        return self.root / "inp"

    @property
    def state(self) -> Path:
        return self.root / "sta"

    @property
    def diff(self) -> Path:
        return self.root / "diff"

    @property
    def snap(self) -> Path:
        return self.root / "snap"

    @property
    def comments(self) -> Path:
        return self.root / "comments"

    @property
    def ctrlr(self) -> Path:
        return self.root / "ctrlr"

    @property
    def script(self) -> Path:
        return self.root / "mydock-quit.lua"

    def all(self) -> list[Path]:
        return [
            self.home, self.ini, self.cfg, self.nvram, self.input, self.state, self.diff, self.snap,
            self.comments, self.ctrlr,
        ]  # fmt: skip


def mame_root(env: dict[str, str] | None = None) -> Path:
    """Le dossier de MAME de l'utilisateur du kiosque (`MYDOCK_MAME_HOME` pour les essais)."""
    source = os.environ if env is None else env
    explicit = source.get(HOME_ENV)
    if explicit:
        return Path(explicit)
    return Path(source.get("HOME") or Path.home()) / ".local" / "share" / "mydock" / "mame"


def prepare_dirs(dirs: MameDirs) -> None:
    """Crée les dossiers et réécrit les deux fichiers de l'assistant (le script de sortie, les commandes)."""
    for folder in dirs.all():
        folder.mkdir(parents=True, exist_ok=True)
    _write_if_changed(dirs.script, QUIT_LUA)
    _write_if_changed(dirs.ctrlr / "mydock.cfg", CTRLR_CFG)


def _write_if_changed(path: Path, text: str) -> None:
    with contextlib.suppress(OSError):
        if path.read_text(encoding="utf-8") == text:
            return
    path.write_text(text, encoding="utf-8")


def mame_command(
    mame: str, game: str, rompath: str, dirs: MameDirs, flags: Sequence[str] = ()
) -> list[str]:
    return [
        mame, game,
        "-rompath", rompath,
        "-homepath", str(dirs.home),
        "-inipath", f"{dirs.ini};{SYSTEM_INI}",
        "-cfg_directory", str(dirs.cfg),
        "-nvram_directory", str(dirs.nvram),
        "-input_directory", str(dirs.input),
        "-state_directory", str(dirs.state),
        "-diff_directory", str(dirs.diff),
        "-snapshot_directory", str(dirs.snap),
        "-comment_directory", str(dirs.comments),
        "-ctrlrpath", str(dirs.ctrlr), "-ctrlr", "mydock",
        "-plugin", "hiscore",
        "-autoboot_script", str(dirs.script),
        "-skip_gameinfo", "-nowindow", "-video", "accel",
        *flags,
    ]  # fmt: skip


def valid_rompath(value: object) -> str | None:
    """Le dossier des ROMs tel que l'assistant accepte de le passer à MAME (chemin absolu, un seul dossier)."""
    if not isinstance(value, str) or not value.startswith("/") or len(value) > 1024:
        return None
    if any(ch in value for ch in ";\0\n\r"):
        return None  # « ; » sépare les dossiers dans -rompath : on n'en laisse passer qu'un
    return value


def explain_exit(game: str, title: str, code: int | None, tail: str) -> str | None:
    """Pourquoi MAME s'est arrêté seul, en une phrase pour l'écran ; None si c'est une sortie normale (Échap, Lua)."""
    if code == 0:
        return None
    name = title or game
    unknown = _UNKNOWN.search(tail)
    if unknown or code == EXIT_NO_SUCH_GAME:
        return f"MAME ne connaît pas le jeu « {game} » : le nom est celui d'une autre version, ou le fichier n'est pas un jeu."
    missing = _MISSING.findall(tail)
    if missing or code == EXIT_MISSING_FILES or "Required files are missing" in tail:
        files = list(dict.fromkeys(file.strip(".,:;") for file, _ in missing))
        sets = {
            s for _, group in missing for s in re.split(r"[,\s]+", group) if s
        }  # « tried in pacman puckman »
        others = sorted(sets - {game})
        text = f"Le jeu « {name} » est incomplet : MAME ne trouve pas toutes ses ROMs"
        if files:
            text += f" ({', '.join(files[:4])}{'…' if len(files) > 4 else ''})"
        text += "."
        if others:
            text += f" Il faut aussi {' et '.join(o + '.zip' for o in others[:2])} dans le même dossier."
        return text + f" Renvoie {game}.zip complet."
    for line in reversed(tail.splitlines()):
        stripped = line.strip()
        if stripped and not _noise(stripped):
            return f"MAME s'est arrêté (code {code}) : {stripped[:160]}"
    if code is not None and code < 0:
        return f"MAME s'est arrêté brutalement (signal {-code}) : mémoire insuffisante ou plantage."
    return f"MAME s'est arrêté (code {code}) sans dire pourquoi : voir le journal de la lecture."


_NOISE = (
    "libegl",
    "mesa",
    "alsa lib",
    "zink",
    "xdg_runtime_dir",
    "average speed",
    "warning: ",
    "pipewire",
    "pulseaudio",
)


def _noise(text: str) -> bool:
    lowered = text.lower()
    return any(lowered.startswith(prefix) or prefix in lowered[:40] for prefix in _NOISE)


class MamePlayback:
    """Un jeu de MAME en plein écran."""

    #: Les trois temps de la sortie (voir `stop`) : le script Lua, la fenêtre fermée, puis SIGKILL.
    FLAG_WAIT = 2.5
    CLOSE_WAIT = 4.0
    KILL_WAIT = 2.0
    #: Sans pouvoir lister les fenêtres, le jeu est tenu pour affiché au bout de ce temps.
    BLIND_SHOWN_AFTER = 6.0

    live = False
    volume: int | None = None

    def __init__(
        self,
        game: str,
        *,
        rompath: str,
        mame: str,
        windows: Any,
        env: dict[str, str],
        runtime_dir: Path,
        job: int = 0,
        title: str = "",
        extra: Sequence[str] | None = None,
        dirs: MameDirs | None = None,
    ) -> None:
        self.game, self.title = game, title
        self._rompath, self._mame, self._windows = rompath, mame, windows
        self._env = env
        self._job = job
        self._extra = list(read_flags() if extra is None else extra)
        self._dirs = dirs or MameDirs(mame_root(env))
        self._flag = runtime_dir / f"mydock-mame-quit-{job}"
        self._process: asyncio.subprocess.Process | None = None
        self._drains: list[asyncio.Task[None]] = []
        self._tail: deque[str] = deque(maxlen=60)
        self._logged = 0
        self._started_at = 0.0
        self._shown_at: float | None = None
        self._stopped = False
        self.how_stopped: str | None = None

    # --- lancement -----------------------------------------------------------------------------------------

    @property
    def finished(self) -> bool:
        return self._process is not None and self._process.returncode is not None

    @property
    def shown(self) -> bool:
        return self._shown_at is not None

    def command_line(self) -> list[str]:
        return mame_command(self._mame, self.game, self._rompath, self._dirs, self._extra)

    async def start(self) -> None:
        if not GAME_RE.fullmatch(self.game):
            raise RuntimeError("Nom de jeu refusé.")
        if valid_rompath(self._rompath) is None:
            raise RuntimeError("Dossier des ROMs refusé.")
        try:
            prepare_dirs(self._dirs)
        except OSError as exc:
            raise RuntimeError(
                f"MAME ne peut pas écrire dans {self._dirs.root} ({exc.strerror or exc})."
            ) from exc
        with contextlib.suppress(OSError):
            self._flag.unlink()
        env = dict(self._env)
        env["MYDOCK_QUIT_FLAG"] = str(self._flag)
        if env.get("WAYLAND_DISPLAY"):
            env.setdefault("SDL_VIDEODRIVER", "wayland")
        env["SDL_VIDEO_WAYLAND_WMCLASS"] = env["SDL_VIDEO_X11_WMCLASS"] = "mame"
        command = self.command_line()
        playerlog.event(
            f"jeu {self._job} : lancement de « {self.game} » — {' '.join(command)} "
            f"(SDL_VIDEODRIVER={env.get('SDL_VIDEODRIVER', '-')}, WAYLAND_DISPLAY={env.get('WAYLAND_DISPLAY', '-')})"
        )
        self._process = await asyncio.create_subprocess_exec(
            *command,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
            start_new_session=True,  # SIGKILL à tout le groupe : MAME n'est jamais laissé derrière
        )
        self._started_at = time.monotonic()
        self._drains = [
            asyncio.create_task(self._drain(self._process.stdout, "mame")),
            asyncio.create_task(self._drain(self._process.stderr, "mame")),
        ]

    async def _drain(self, stream: asyncio.StreamReader | None, label: str) -> None:
        if stream is None:
            return
        with contextlib.suppress(Exception):
            while raw := await stream.readline():
                text = raw.decode("utf-8", "replace").rstrip("\r\n")
                if not text.strip():
                    continue
                self._tail.append(text)
                if self._logged < MAX_LOGGED_LINES:
                    self._logged += 1
                    playerlog.line(label, text)
                    if self._logged == MAX_LOGGED_LINES:
                        playerlog.event(
                            f"mame : la suite est omise du journal ({MAX_LOGGED_LINES} lignes)"
                        )

    # --- suivi ---------------------------------------------------------------------------------------------

    async def _window(self) -> tuple[bool, list[tuple[str, str]]]:
        listed = await self._windows.toplevels() if hasattr(self._windows, "toplevels") else []
        found = any("mame" in app_id.lower() or "mame" in title.lower() for app_id, title in listed)
        return found, listed

    async def status(self) -> dict[str, Any]:
        """« starting » tant que la fenêtre du jeu n'est pas là, « playing » ensuite (et le dock arrête un jeu qui n'apparaît pas)."""
        if self._shown_at is None and not self.finished:
            found, listed = await self._window()
            blind = not listed and time.monotonic() - self._started_at >= self.BLIND_SHOWN_AFTER
            if found or blind:
                self._shown_at = time.monotonic()
                playerlog.event(
                    f"jeu {self._job} : « {self.game} » est affiché après {self._shown_at - self._started_at:.1f} s"
                    + (" (fenêtres illisibles : on le suppose)" if blind and not found else "")
                )
        return {
            "state": "playing" if self._shown_at is not None else "starting",
            "position": None,
            "length": None,
            "seekable": False,
        }

    async def command(self, name: str, **params: Any) -> None:
        """Rien à piloter à distance : un jeu se joue à la manette (et se quitte par `stop`)."""

    # --- arrêt ---------------------------------------------------------------------------------------------

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
        """Ferme la fenêtre de MAME comme le ferait un clic sur sa croix : le jeu s'arrête alors proprement."""
        closer = getattr(self._windows, "close_app", None)
        if closer is None:
            return 0
        return int(await closer("mame"))

    async def stop(self) -> None:
        """Arrête MAME : script Lua, puis fermeture de la fenêtre, puis SIGKILL. Répond quand le jeu a fini."""
        if self._stopped:
            return
        self._stopped = True
        process = self._process
        if process is not None and process.returncode is None:
            playerlog.event(f"jeu {self._job} : arrêt demandé, sortie propre (script de MAME)")
            with contextlib.suppress(OSError):
                self._flag.write_text("quit\n", encoding="utf-8")
            if await self._wait_exit(self.FLAG_WAIT):
                self.how_stopped = "propre (script)"
            else:
                closed = await self._close_windows()
                playerlog.event(
                    f"jeu {self._job} : MAME n'a pas quitté seul en {self.FLAG_WAIT:g} s, fenêtre fermée ({closed} fenêtre)"
                )
                if await self._wait_exit(self.CLOSE_WAIT):
                    self.how_stopped = "fenêtre fermée"
                else:
                    with contextlib.suppress(ProcessLookupError, PermissionError):
                        os.killpg(process.pid, signal.SIGKILL)
                    playerlog.event(f"jeu {self._job} : MAME ne répond pas, tué (SIGKILL)")
                    await self._wait_exit(self.KILL_WAIT)
                    self.how_stopped = "tué"
            playerlog.event(f"jeu {self._job} : arrêté ({self.how_stopped})")
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
        for task in self._drains:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
        self._drains = []
        with contextlib.suppress(OSError):
            self._flag.unlink()

    async def wait_output(self) -> None:
        """Laisse finir la lecture de ce que MAME a écrit avant de dire pourquoi il s'est arrêté."""
        pending = [task for task in self._drains if not task.done()]
        if pending:
            await asyncio.wait(pending, timeout=0.5)

    def error(self) -> str | None:
        if self._stopped:
            return None
        code = self._process.returncode if self._process is not None else None
        message = explain_exit(self.game, self.title, code, "\n".join(self._tail))
        playerlog.event(
            f"jeu {self._job} : fin du jeu « {self.game} », code de MAME : {code}, "
            + ("sortie normale." if message is None else "arrêt anormal.")
        )
        if message is not None:
            playerlog.failure(self._job, message)
        return message
        # (le nettoyage des tâches de lecture se fait dans `stop`, que la session appelle toujours ensuite)


#: En démonstration, ces jeux « n'ont pas toutes leurs ROMs » : de quoi montrer l'erreur à l'écran.
DEMO_BROKEN = frozenset({"mslug"})


class DemoGamePlayback:
    """Un faux jeu pour le mode démo : « affiché » au bout d'une seconde, jusqu'à ce qu'on l'arrête."""

    live = False
    volume: int | None = None

    def __init__(self, game: str, *, title: str = "", **_: Any) -> None:
        self.game, self.title = game, title
        self._started = time.monotonic()
        self._stopped = False
        self.how_stopped: str | None = None

    @property
    def finished(self) -> bool:
        if self.game in DEMO_BROKEN and time.monotonic() - self._started >= 0.8:
            return True
        return self._stopped

    async def start(self) -> None:
        self._started = time.monotonic()

    async def status(self) -> dict[str, Any]:
        playing = time.monotonic() - self._started >= 1.0
        return {
            "state": "playing" if playing else "starting",
            "position": None,
            "length": None,
            "seekable": False,
        }

    async def command(self, name: str, **params: Any) -> None:
        return None

    async def stop(self) -> None:
        self._stopped = True
        self.how_stopped = "propre (démo)"

    def error(self) -> str | None:
        if self.game in DEMO_BROKEN and not self._stopped:
            return explain_exit(
                self.game, self.title, 2, f"{self.game}.zip NOT FOUND (tried in {self.game})"
            )
        return None
