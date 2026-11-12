"""L'assistant de lecture : il lance VLC sur l'écran du dock, le commande, et rend compte au dock."""

from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import stat
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from mydock_dock import player_session as ps
from mydock_dock import playerlog
from mydock_dock.player_session import DemoPlayback, Session, VlcPlayback

# --- fonctions pures -------------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("url", "live555", "expected"),
    [
        ("rtsp://mafreebox.freebox.fr/fbxtv_pub/stream?service=1", False, True),
        ("RTSP://h/x", False, True),
        ("rtsp://h/x", True, False),  # VLC sait le lire lui-même
        ("http://h/x.mp3", False, False),
        ("smb://nas/Films/a.mkv", False, False),
        ("udp://@239.0.0.1:1234", False, False),
    ],
)
def test_only_rtsp_needs_the_ffmpeg_bridge_and_only_without_live555(url, live555, expected):
    assert ps.needs_bridge(url, live555) is expected


def test_the_vlc_command_is_a_list_with_the_source_last_after_a_double_dash():
    command = ps.vlc_command(
        "/usr/bin/vlc",
        "smb://nas/a b/-evil.mkv",
        "/run/x.sock",
        volume=70,
        extra=["--vout", "dummy"],
    )
    assert command[0] == "/usr/bin/vlc"
    assert command[-2:] == [
        "--",
        "smb://nas/a b/-evil.mkv",
    ]  # une adresse qui commence par « - » n'est jamais une option
    assert command[1:5] == ["-I", "oldrc", "--rc-unix", "/run/x.sock"]
    assert "--fullscreen" in command
    assert "--play-and-exit" in command
    assert "--volume=179" in command  # 70 % de 256
    assert command.index("--vout") < command.index("--")


def test_the_config_file_comes_first_when_there_is_one():
    command = ps.vlc_command("vlc", "x", "s", volume=0, config_file="/run/vlc.conf")
    assert command[1:3] == ["--config", "/run/vlc.conf"]
    assert "--volume=0" in command


def test_the_bridge_copies_the_stream_without_decoding_it():
    command = ps.ffmpeg_bridge_command("ffmpeg", "rtsp://h/x?a=1&b=2")
    assert command[command.index("-i") + 1] == "rtsp://h/x?a=1&b=2"
    assert command[command.index("-c") + 1] == "copy"
    assert command[-3:] == ["-f", "mpegts", "pipe:1"]
    assert "-nostdin" in command


def test_vlc_answers_are_read_by_their_numbers_only():
    text = "status change: ( new input: fd://0 )\n> 1\n  42  \nrien\n> \n-3\nvolume: returned 0 (no error)\n"
    assert ps.parse_numbers(text) == [1, 42, -3]
    assert ps.parse_numbers("") == []


@pytest.mark.parametrize(
    ("played", "live", "tail", "expected"),
    [
        (True, True, "n'importe quoi", None),
        (
            False,
            False,
            "smb stream error: open failed for 'nas/x' (Permission denied)",
            "Accès refusé",
        ),
        (False, False, "access error: Access denied", "Accès refusé"),
        (False, False, "file stream error: No such file or directory", "introuvable"),
        (False, True, "", "La chaîne ne répond pas"),
        (False, False, "", "n'a pas pu lire cette vidéo"),
    ],
)
def test_a_playback_that_never_started_is_explained(played, live, tail, expected):
    message = ps.explain_exit(played, live, tail)
    if expected is None:
        assert message is None
    else:
        assert expected in message


@pytest.mark.parametrize(
    ("live", "tail", "code", "expected"),
    [
        (
            False,
            "main input error: VLC is unable to open the MRL 'smb://nas/Films/a.mkv'. Check the log for details.",
            None,
            "vlc-plugin-samba",
        ),
        (
            False,
            "main input error: VLC is unable to open the MRL 'smb://nas/a.mkv'. Check the log for details.\nsmb stream error: open failed for 'nas/a.mkv' (Invalid argument)",
            None,
            "Détail : « smb stream error: open failed",
        ),
        (
            False,
            "avcodec decoder error: no suitable decoder module for fourcc `hev1'",
            None,
            "ne sait pas décoder",
        ),
        (False, "main decoder error: Codec not supported", None, "ne sait pas décoder"),
        (False, "main video output error: video output creation failed", None, "afficher la vidéo"),
        (False, "xcb window error: cannot open display :0", None, "xwayland"),
        (False, "", -11, "arrêté brutalement (SIGSEGV)"),
        (
            False,
            "[00aa] smb stream error: read failed (Invalid argument)",
            None,
            "sudo apt install smbclient",
        ),
        (False, "", -9, "arrêté brutalement (SIGKILL)"),
        (False, "", -250, "arrêté brutalement (signal 250)"),
        (True, "ffmpeg : Connection refused", None, "Détail : « ffmpeg : Connection refused »"),
        (False, "", 0, "n'a pas pu lire cette vidéo"),
    ],
)
def test_the_real_reason_is_given_when_vlc_says_it(live, tail, code, expected):
    assert expected in ps.explain_exit(False, live, tail, code)


def test_vlc_details_leave_out_the_memory_address_and_the_usual_noise():
    tail = (
        "[0000563ecf14def0] dummy interface: using the dummy interface module...\n"
        "[00007f65f00015a0] smb stream error: open failed for 'nas/x' (Connection refused)\n"
        "[00007f65f8000c90] main input error: Your input can't be opened\n"
        "[00007f65f8000c90] main input error: VLC is unable to open the MRL 'smb://nas/x'. Check the log for details.\n"
        "[0000563ecf149440] main playlist: end of playlist, exiting\n"
        "[0000563ecf14def0] dbus interface error: Failed to connect to the D-Bus session daemon\n"
    )
    detail = ps.vlc_detail(tail)
    assert detail == "smb stream error: open failed for 'nas/x' (Connection refused)"
    assert "0000" not in detail
    assert "dbus" not in detail.lower()
    assert "dummy" not in detail
    assert ps.vlc_detail("") == ""


def test_vlc_details_are_short_and_start_with_the_first_lines_where_the_cause_is():
    tail = "\n".join(f"main error: ligne {i} " + "x" * 80 for i in range(10))
    detail = ps.vlc_detail(tail)
    assert len(detail) <= 200
    assert detail.startswith("main error: ligne 0")
    assert "ligne 5" not in detail  # deux lignes au plus


def test_vlc_details_put_the_specific_cause_before_the_generic_sentences_and_errors_before_warnings():
    tail = (
        "[00aa] main input error: Your input can't be opened\n"
        "[00aa] main input error: VLC is unable to open the MRL 'smb://nas/a.mkv'. Check the log for details.\n"
        "[00bb] main warning: quelque chose de bénin\n"
        "[00cc] smb stream error: open failed for 'nas/a.mkv' (Connection refused)\n"
    )
    assert (
        ps.vlc_detail(tail) == "smb stream error: open failed for 'nas/a.mkv' (Connection refused)"
    )
    only_generic = (
        "[00aa] main input error: VLC is unable to open the MRL 'x://y'. Check the log for details."
    )
    assert "unable to open the MRL" in ps.vlc_detail(only_generic)  # à défaut de mieux
    assert (
        ps.vlc_detail("[00bb] main warning: bénin") == "main warning: bénin"
    )  # les avertissements, faute d'erreur


def test_vlc_never_asks_questions_nor_looks_for_a_keyring():
    command = ps.vlc_command("vlc", "x", "s", volume=0)
    assert (
        "--no-interact" in command
    )  # un mot de passe refusé : VLC échoue tout de suite au lieu d'attendre une saisie
    assert "--keystore=memory" in command  # pas de D-Bus dans la session du kiosque
    assert command.index("--no-interact") < command.index("--")


def test_extra_flags_are_read_without_comments(tmp_path):
    flags = tmp_path / "vlc.flags"
    flags.write_text("# commentaire\n--avcodec-hw=any\n\n  --deinterlace=1  \n")
    assert ps.read_flags(str(flags)) == ["--avcodec-hw=any", "--deinterlace=1"]
    assert ps.read_flags(str(tmp_path / "absent")) == []


def test_the_x11_screen_is_found_for_vlc_when_the_session_does_not_say(monkeypatch):
    monkeypatch.setattr(ps.glob, "glob", lambda pattern: ["/tmp/.X11-unix/X1"])
    assert ps.session_environment({"WAYLAND_DISPLAY": "wayland-0"})["DISPLAY"] == ":1"
    assert (
        ps.session_environment({"DISPLAY": ":7"})["DISPLAY"] == ":7"
    )  # ce que la session donne gagne
    assert "QT_QPA_PLATFORM" not in ps.session_environment({"QT_QPA_PLATFORM": "wayland"})


def test_live555_is_looked_for_in_the_plugin_directories(monkeypatch):
    monkeypatch.setattr(ps.glob, "glob", lambda pattern: [])
    assert ps.has_live555() is False
    monkeypatch.setattr(
        ps.glob, "glob", lambda pattern: ["/usr/lib/x/vlc/plugins/access/liblive555_plugin.so"]
    )
    assert ps.has_live555() is True


# --- un faux VLC et un faux ffmpeg, qui parlent vraiment « oldrc » --------------------------------------------------------

FAKE_VLC = """\
#!{python}
import json, os, socket, sys, threading, time
args = sys.argv[1:]
sock_path = args[args.index("--rc-unix") + 1]
volume = next((int(a.split("=")[1]) for a in args if a.startswith("--volume=")), 256)
config = args[args.index("--config") + 1] if "--config" in args else None
record = {{"args": args, "display": os.environ.get("DISPLAY"), "lc_all": os.environ.get("LC_ALL"), "language": os.environ.get("LANGUAGE"), "config": open(config).read() if config else None}}
if args[-1] == "fd://0":
    import stat
    record["stdin_is_pipe"] = stat.S_ISFIFO(os.fstat(0).st_mode)  # un vrai tube, pas une prise (VLC ne le lirait pas)
    record["stdin"] = os.read(0, 1000).decode("latin-1")
json.dump(record, open(os.environ["FAKE_LOG"], "w"))
if os.environ.get("FAKE_VLC_FAIL") and args[-1].startswith(os.environ.get("FAKE_VLC_FAIL_ON", "")):
    sys.stderr.write(os.environ.get("FAKE_VLC_STDERR") or "smb stream error: open failed for 'nas/x.mkv' (Permission denied)\\n")
    if os.environ.get("FAKE_VLC_SIGNAL"):
        os.kill(os.getpid(), int(os.environ["FAKE_VLC_SIGNAL"]))
        time.sleep(5)
    sys.exit(int(os.environ.get("FAKE_VLC_EXIT", "0")))
live = os.environ.get("FAKE_LIVE") == "1"
state = {{"playing": 1, "base": 0.0, "since": time.monotonic(), "volume": volume}}
def now():
    return state["base"] + ((time.monotonic() - state["since"]) if state["playing"] else 0)
def handle(line):
    word, _, rest = line.strip().partition(" ")
    if word == "is_playing": return "1"  # comme VLC 3 : 1 même en pause
    if word == "status":
        return ("Type 'pause' to continue." if not state["playing"] else "( audio volume: %d )" % state["volume"]) + "\\nstatus: returned 0 (no error)"
    if word == "get_time": return str(int(now()))
    if word == "get_length": return "0" if live else "120"
    if word == "pause":
        state["base"] = now(); state["playing"] ^= 1; state["since"] = time.monotonic(); return ""
    if word == "play":
        state["base"] = now(); state["playing"] = 1; state["since"] = time.monotonic(); return ""
    if word == "seek": state["base"] = float(rest); state["since"] = time.monotonic(); return ""
    if word == "volume":
        state["volume"] = int(rest); open(os.environ["FAKE_LOG"] + ".volume", "w").write(rest); return ""
    return ""
server = socket.socket(socket.AF_UNIX)
server.bind(sock_path)
server.listen(2)
if os.environ.get("FAKE_EXIT_AFTER"):
    threading.Timer(float(os.environ["FAKE_EXIT_AFTER"]), lambda: os._exit(0)).start()
while True:
    conn, _ = server.accept()
    def serve(conn=conn):
        buffer = b""
        while chunk := conn.recv(4096):
            buffer += chunk
            while b"\\n" in buffer:
                line, buffer = buffer.split(b"\\n", 1)
                answer = handle(line.decode())
                conn.sendall((answer + "\\n").encode() if answer else b"")
    threading.Thread(target=serve, daemon=True).start()
"""

FAKE_FFMPEG = """\
#!/bin/sh
printf '%s\\n' "$@" > "$FAKE_LOG.ffmpeg"
if [ -n "$FAKE_FFMPEG_ERROR" ]; then
  printf '%s\\n' "$FAKE_FFMPEG_ERROR" >&2
  exit 1
fi
printf 'TSDATA'
exec sleep 30
"""


FAKE_SMBCLIENT = """\
#!/bin/sh
# Un faux smbclient (il n'hérite que d'un environnement minimal, comme le vrai : le journal est à un chemin fixe).
# Comme le vrai, il écrit ses erreurs sur la sortie standard, c'est-à-dire dans la « vidéo ».
printf '%s\\n' "$@" >> "@LOG@.smbclient"
printf 'USER=%s\\n' "$USER" >> "@LOG@.smbclient.env"
if [ -f "@LOG@.smbslow" ]; then sleep 30; fi
if [ -f "@LOG@.smberror" ]; then cat "@LOG@.smberror"; exit 1; fi
if [ -f "@LOG@.smbsilent" ]; then exit 1; fi
if [ -f "@LOG@.smbbig" ]; then head -c 200000 /dev/zero | tr '\\0' 'A'; exec sleep 30; fi
printf 'SMBDATA'
exec sleep 30
"""


@pytest.fixture
def fakes(tmp_path, monkeypatch):
    """De faux `vlc`, `ffmpeg` et `smbclient` : ils notent leurs arguments dans FAKE_LOG et répondent comme les vrais."""
    vlc, ffmpeg, smbclient = tmp_path / "vlc", tmp_path / "ffmpeg", tmp_path / "smbclient"
    vlc.write_text(FAKE_VLC.format(python=sys.executable))
    ffmpeg.write_text(FAKE_FFMPEG)
    smbclient.write_text(FAKE_SMBCLIENT.replace("@LOG@", str(tmp_path / "log.json")))
    for script in (vlc, ffmpeg, smbclient):
        script.chmod(script.stat().st_mode | stat.S_IEXEC)
    log = tmp_path / "log.json"
    monkeypatch.setenv("FAKE_LOG", str(log))
    run = tmp_path / "run"
    run.mkdir(mode=0o700)
    return type(
        "Fakes",
        (),
        {
            "vlc": str(vlc),
            "ffmpeg": str(ffmpeg),
            "smbclient": str(smbclient),
            "log": log,
            "run": run,
        },
    )()


def playback(
    fakes,
    url="smb://nas/Films/a.mkv",
    *,
    live=False,
    live555=False,
    smb=None,
    ffmpeg=True,
    volume=70,
    smb_source=None,
):
    return VlcPlayback(
        url,
        live=live,
        volume=volume,
        vlc=fakes.vlc,
        ffmpeg=fakes.ffmpeg if ffmpeg else None,
        live555=live555,
        runtime_dir=fakes.run,
        env={**os.environ, "DISPLAY": ":9"},
        smb=smb,
        job=1,
        smbclient=fakes.smbclient if smb_source is not None else None,
        smb_source=smb_source,
    )


async def until(predicate, timeout=5.0):
    async with asyncio.timeout(timeout):
        while not predicate():
            await asyncio.sleep(0.02)


async def settled(pb, state="playing", timeout=8.0):
    """Attend que le lecteur soit dans cet état (VLC a le temps d'ouvrir son socket) et le rend."""
    async with asyncio.timeout(timeout):
        while True:
            status = await pb.status()
            if status["state"] == state:
                return status
            await asyncio.sleep(0.05)


async def test_vlc_is_started_on_the_x11_screen_and_reports_its_progress(fakes):
    pb = playback(fakes)
    await pb.start()
    try:
        status = await settled(pb)
        assert status["length"] == 120
        await asyncio.sleep(1.1)
        assert (await pb.status())["position"] >= 1
        assert json.loads(fakes.log.read_text())["display"] == ":9"
    finally:
        await pb.stop()


async def test_pause_resume_seek_and_volume_reach_vlc(fakes):
    pb = playback(fakes)
    await pb.start()
    try:
        await settled(pb)
        await pb.command("toggle")
        assert (await pb.status())["state"] == "paused"
        before = (await pb.status())["position"]
        await asyncio.sleep(1.1)
        assert (await pb.status())["position"] == before  # en pause, la position n'avance pas
        await pb.command("resume")
        assert (await pb.status())["state"] == "playing"
        await pb.command("seek", delta=50)
        assert 50 <= (await pb.status())["position"] < 60
        await pb.command("seek", delta=-1000)
        assert (await pb.status())["position"] < 5  # jamais avant le début
        await pb.command("seek", delta=10_000)
        assert (await pb.status())["position"] <= 118  # jamais au-delà de la fin
        await pb.command("volume", value=100)
        await until(lambda: Path(str(fakes.log) + ".volume").exists())
        assert Path(str(fakes.log) + ".volume").read_text() == "256"
    finally:
        await pb.stop()


async def test_a_live_channel_has_neither_position_nor_length_and_cannot_seek(fakes, monkeypatch):
    monkeypatch.setenv("FAKE_LIVE", "1")
    pb = playback(fakes, "http://h/direct", live=True)
    await pb.start()
    try:
        status = await settled(pb)
        assert (status["position"], status["length"]) == (None, None)
        await pb.command("seek", delta=60)  # sans effet sur un direct
        assert (await pb.status())["position"] is None
    finally:
        await pb.stop()


async def test_stopping_kills_vlc_and_removes_its_socket(fakes):
    pb = playback(fakes)
    await pb.start()
    await settled(pb)
    socket_path = Path(pb._socket)
    assert socket_path.exists()
    await pb.stop()
    assert pb.finished
    assert not socket_path.exists()
    assert pb.error() is None  # un arrêt voulu n'est pas une erreur


async def test_the_share_password_is_in_a_private_file_never_in_the_arguments(fakes):
    pb = playback(fakes, smb=("freebox", "p@ss w0rd"))
    await pb.start()
    try:
        await settled(pb)
        record = json.loads(fakes.log.read_text())
        assert "p@ss" not in " ".join(record["args"])
        assert "freebox" not in " ".join(record["args"])
        assert record["config"] == "[smb]\nsmb-user=freebox\nsmb-pwd=p@ss w0rd\n"
        config = Path(pb._config)
        assert stat.S_IMODE(config.stat().st_mode) == 0o600
    finally:
        await pb.stop()
    assert not config.exists()  # effacé avec la lecture


async def test_without_live555_a_channel_goes_through_ffmpeg(fakes):
    url = "rtsp://mafreebox.freebox.fr/fbxtv_pub/stream?namespace=1&service=201&flavour=sd"
    pb = playback(fakes, url, live=True)
    await pb.start()
    try:
        await settled(pb)
        record = json.loads(fakes.log.read_text())
        assert record["args"][-1] == "fd://0"  # VLC lit le tube
        assert record["stdin"] == "TSDATA"  # ce que ffmpeg y a écrit
        assert "--network-caching=1500" in record["args"]
        ffmpeg_args = Path(str(fakes.log) + ".ffmpeg").read_text().split("\n")
        assert url in ffmpeg_args
        assert "copy" in ffmpeg_args
    finally:
        await pb.stop()


async def test_stopping_also_stops_the_bridge(fakes):
    pb = playback(fakes, "rtsp://h/x", live=True)
    await pb.start()
    await settled(pb)
    ffmpeg_pid = pb._procs[0].pid
    await pb.stop()
    with pytest.raises(ProcessLookupError):
        os.kill(ffmpeg_pid, 0)


async def test_with_live555_vlc_reads_the_channel_itself(fakes):
    pb = playback(fakes, "rtsp://h/x", live=True, live555=True)
    await pb.start()
    try:
        await settled(pb)
        assert json.loads(fakes.log.read_text())["args"][-1] == "rtsp://h/x"
        assert not Path(str(fakes.log) + ".ffmpeg").exists()
    finally:
        await pb.stop()


async def test_a_missing_ffmpeg_is_said_when_the_channel_needs_it(fakes):
    pb = playback(fakes, "rtsp://h/x", live=True, ffmpeg=False)
    with pytest.raises(RuntimeError, match=r"sudo apt install ffmpeg"):
        await pb.start()


async def test_a_vlc_that_gives_up_is_explained_from_what_it_said(fakes, monkeypatch):
    monkeypatch.setenv("FAKE_VLC_FAIL", "1")
    pb = playback(fakes, smb=("freebox", "faux"))
    await pb.start()
    await until(lambda: pb.finished)
    await asyncio.sleep(0.1)
    assert "Accès refusé" in pb.error()
    await pb.stop()


# --- les fenêtres : wlrctl, ou xdotool à défaut ---------------------------------------------------------------------------


def fake_tool(tmp_path, name, body):
    """Un faux outil : il note ses arguments dans <nom>.log et répond comme dit `body` (du shell)."""
    script = tmp_path / name
    script.write_text(f"#!/bin/sh\nprintf '%s\\n' \"$*\" >> {tmp_path}/{name}.log\n{body}\n")
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    return script


def calls(tmp_path, name):
    log = tmp_path / f"{name}.log"
    return log.read_text().splitlines() if log.exists() else []


@pytest.fixture
def only_tools(tmp_path, monkeypatch):
    """Rien d'autre que ces faux outils dans le PATH : on choisit ce qui existe sur le « Pi »."""
    real = shutil.which
    monkeypatch.setattr(
        shutil,
        "which",
        lambda name, *a, **k: str(tmp_path / name) if (tmp_path / name).exists() else None,
    )
    return real


async def test_the_windows_are_ordered_with_wlrctl_by_the_exact_names_it_lists(
    tmp_path, only_tools
):
    fake_tool(
        tmp_path,
        "wlrctl",
        'case "$1 $2" in "toplevel list") printf "chromium: Dock\\nVlc: MyDock\\n";; esac',
    )
    windows = ps.Windows({})
    assert windows.available
    assert await windows.focus("dock")
    assert await windows.focus("video")
    focused = [c for c in calls(tmp_path, "wlrctl") if c.startswith("toplevel focus")]
    assert focused == ["toplevel focus app_id:chromium", "toplevel focus app_id:Vlc"]


async def test_a_dock_window_with_any_other_name_is_still_found(tmp_path, only_tools):
    fake_tool(
        tmp_path,
        "wlrctl",
        'case "$1 $2" in "toplevel list") printf "Vlc: v\\nsuper-kiosque: Dock\\n";; esac',
    )
    assert await ps.Windows({}).focus("dock")
    assert calls(tmp_path, "wlrctl")[-1] == "toplevel focus app_id:super-kiosque"


async def test_without_wlrctl_xdotool_hides_and_shows_the_video_window(tmp_path, only_tools):
    fake_tool(tmp_path, "xdotool", 'case "$1" in search) printf "8388608\\n8388610\\n";; esac')
    windows = ps.Windows({})
    assert windows.available
    assert await windows.focus("dock")  # le dock revient : la vidéo est réduite
    assert await windows.focus("video")  # la vidéo revient : on l'active
    assert calls(tmp_path, "xdotool") == [
        "search --class vlc", "windowminimize 8388608", "windowminimize 8388610",
        "search --class vlc", "windowactivate 8388608", "windowactivate 8388610",
    ]  # fmt: skip


async def test_xdotool_takes_over_when_wlrctl_does_not_see_the_window(tmp_path, only_tools):
    fake_tool(tmp_path, "wlrctl", "exit 0")  # ne liste aucune fenêtre
    fake_tool(tmp_path, "xdotool", 'case "$1" in search) printf "42\\n";; esac')
    assert await ps.Windows({}).focus("dock")
    assert calls(tmp_path, "xdotool")[-1] == "windowminimize 42"


async def test_no_tool_means_no_way_back_and_it_is_said(tmp_path, only_tools):
    windows = ps.Windows({})
    assert not windows.available
    assert not await windows.focus("dock")


async def test_a_tool_that_fails_is_not_a_crash(tmp_path, only_tools):
    fake_tool(tmp_path, "xdotool", "exit 3")
    assert not await ps.Windows({}).focus("dock")


# --- l'assistant entier, avec de faux VLC, fenêtres et toucher ------------------------------------------------------------


class FakeWindows:
    available = True

    def __init__(self):
        self.focused: list[str] = []

    async def focus(self, which):
        self.focused.append(which)
        return True


class FakeTouch:
    available = True

    def __init__(self):
        self.running = False

    async def start(self):
        self.running = True

    async def stop(self):
        self.running = False


@pytest.fixture
def fast(monkeypatch):
    monkeypatch.setattr(ps, "POLL_SECONDS", 0.05)
    monkeypatch.setattr(ps, "TOUCH_GRACE", 0.3)


def session(fakes, **kw):
    s = Session(
        "ws://unused",
        vlc=fakes.vlc,
        ffmpeg=fakes.ffmpeg,
        smbclient=fakes.smbclient,
        live555=False,
        runtime_dir=fakes.run,
        extra_vlc_args=[],
        env={**os.environ, "DISPLAY": ":9"},
        **kw,
    )
    s.windows, s.touch = FakeWindows(), FakeTouch()
    s._loop = asyncio.get_running_loop()
    return s


class Mailbox:
    def __init__(self):
        self.messages: list[dict[str, Any]] = []

    async def __call__(self, message):
        self.messages.append(message)

    def of(self, kind):
        return [m for m in self.messages if m["t"] == kind]

    async def wait(self, predicate, timeout=8.0):
        async with asyncio.timeout(timeout):
            while not any(predicate(m) for m in self.messages):
                await asyncio.sleep(0.02)


async def test_hello_says_what_the_screen_can_do(fakes):
    s = session(fakes)
    assert s.hello() == {
        "t": "hello",
        "v": 1,
        "vlc": True,
        "live555": False,
        "ffmpeg": True,
        "smbclient": True,
        "touch": True,
        "demo": False,
    }
    s.touch = type("T", (), {"available": False})()
    assert s.hello()["touch"] is False  # sans swayidle, pas de vidéo plein écran
    assert Session("ws://x", demo=True).hello()["demo"] is True


async def test_a_play_command_starts_vlc_in_front_and_reports(fakes, fast):
    s, box = session(fakes), Mailbox()
    await s.handle(
        {
            "t": "cmd",
            "name": "play",
            "job": 4,
            "url": "smb://nas/Films/a.mkv",
            "live": False,
            "volume": 50,
        },
        box,
    )
    try:
        await box.wait(lambda m: m["t"] == "state" and m["state"] == "playing")
        state = box.of("state")[-1]
        assert (state["job"], state["length"], state["front"], state["volume"]) == (
            4,
            120,
            "video",
            50,
        )
        assert s.touch.running  # on guette le toucher tant que la vidéo est devant
        assert "video" in s.windows.focused
    finally:
        await s._stop_playback()
    assert not s.touch.running
    assert s.windows.focused[-1] == "dock"


async def test_a_touch_brings_the_dock_back_but_not_the_one_that_started_the_video(fakes, fast):
    s, box = session(fakes), Mailbox()
    await s.handle(
        {"t": "cmd", "name": "play", "job": 1, "url": "smb://nas/a.mkv", "volume": 70}, box
    )
    try:
        s._touched()  # le doigt qui vient de lancer la lecture : ignoré
        await asyncio.sleep(0.05)
        assert s.front == "video"
        await box.wait(lambda m: m["t"] == "state" and m["state"] == "playing")
        await asyncio.sleep(0.4)
        s._touched()
        await box.wait(lambda m: m["t"] == "state" and m["front"] == "dock")
        assert s.windows.focused[-1] == "dock"
        await s.handle(
            {"t": "cmd", "name": "front", "to": "video", "job": 1}, box
        )  # « Revenir à la vidéo »
        assert (s.front, s.windows.focused[-1]) == ("video", "video")
    finally:
        await s._stop_playback()


async def test_the_remote_commands_drive_the_playback(fakes, fast):
    s, box = session(fakes), Mailbox()
    await s.handle(
        {"t": "cmd", "name": "play", "job": 1, "url": "smb://nas/a.mkv", "volume": 70}, box
    )
    try:
        await box.wait(lambda m: m["t"] == "state" and m["state"] == "playing")
        await s.handle({"t": "cmd", "name": "toggle", "job": 1}, box)
        await box.wait(lambda m: m["t"] == "state" and m["state"] == "paused")
        await s.handle({"t": "cmd", "name": "resume", "job": 1}, box)
        await s.handle({"t": "cmd", "name": "seek", "delta": 30.0, "job": 1}, box)
        await s.handle({"t": "cmd", "name": "volume", "value": 20, "job": 1}, box)
        await box.wait(
            lambda m: (
                m["t"] == "state"
                and m["volume"] == 20
                and m["state"] == "playing"
                and m["position"] >= 30
            )
        )
    finally:
        await s._stop_playback()


async def test_the_end_of_the_film_brings_the_dock_back(fakes, fast, monkeypatch):
    monkeypatch.setenv("FAKE_EXIT_AFTER", "2.5")
    s, box = session(fakes), Mailbox()
    await s.handle(
        {"t": "cmd", "name": "play", "job": 7, "url": "smb://nas/a.mkv", "volume": 70}, box
    )
    await box.wait(lambda m: m["t"] == "ended")
    assert box.of("ended") == [{"t": "ended", "job": 7, "error": None}]
    assert s.front == "dock"
    assert s.windows.focused[-1] == "dock"
    assert not s.touch.running


async def test_a_refused_file_is_reported_as_an_error_not_an_end(fakes, fast, monkeypatch):
    monkeypatch.setenv("FAKE_VLC_FAIL", "1")
    s, box = session(fakes), Mailbox()
    await s.handle(
        {
            "t": "cmd",
            "name": "play",
            "job": 2,
            "url": "smb://nas/x.mkv",
            "volume": 70,
            "smb_user": "freebox",
            "smb_password": "faux",
        },
        box,
    )
    await box.wait(lambda m: m["t"] == "ended")
    assert "Accès refusé" in box.of("ended")[0]["error"]


@pytest.fixture
def journal(tmp_path, monkeypatch):
    """Le journal de débogage dans un fichier de test (jamais /var/log/mydock)."""
    path = tmp_path / "journal" / "player.log"
    path.parent.mkdir()
    monkeypatch.setenv(playerlog.ENV_PATH, str(path))
    playerlog.detach()
    assert playerlog.attach() == path
    yield path
    playerlog.detach()


async def test_the_reason_vlc_gives_is_shown_and_written_to_the_journal_without_the_password(
    fakes, fast, monkeypatch, journal
):
    monkeypatch.setenv("FAKE_VLC_FAIL", "1")
    monkeypatch.setenv(
        "FAKE_VLC_STDERR",
        "[0000aa] avcodec decoder error: no suitable decoder module for fourcc `hev1'\n",
    )
    s, box = session(fakes), Mailbox()
    await s.handle(
        {
            "t": "cmd",
            "name": "play",
            "job": 5,
            "url": "smb://nas/Films/a.mkv",
            "volume": 70,
            "smb_user": "ligon",
            "smb_password": "mot-de-passe-secret",
        },
        box,
    )
    await box.wait(lambda m: m["t"] == "ended")
    error = box.of("ended")[0]["error"]
    assert "ne sait pas décoder" in error
    assert "no suitable decoder module" in error  # le message de VLC lui-même, pour comprendre
    text = journal.read_text(encoding="utf-8")
    assert "demandée par le dock" in text
    assert "adresse smb://nas/Films/a.mkv" in text
    assert "commande VLC : " in text
    assert "vlc| [0000aa] avcodec decoder error: no suitable decoder module" in text
    assert f"{playerlog.FAILURE_MARK} lecture 5 : " in text
    assert "mot-de-passe-secret" not in text  # jamais dans le journal
    assert "mot-de-passe-secret" not in error
    assert "ligon" not in text  # pas même l'identifiant : on dit seulement qu'il y en a un
    assert "identifiant du partage : oui" in text


async def test_a_crash_of_vlc_names_the_signal(fakes, fast, monkeypatch):
    monkeypatch.setenv("FAKE_VLC_FAIL", "1")
    monkeypatch.setenv("FAKE_VLC_STDERR", "main libvlc error: plantage\n")
    monkeypatch.setenv("FAKE_VLC_SIGNAL", "9")  # tué par un signal : le code de sortie est négatif
    s, box = session(fakes), Mailbox()
    await s.handle(
        {"t": "cmd", "name": "play", "job": 6, "url": "smb://nas/Films/a.mkv", "volume": 70}, box
    )
    await box.wait(lambda m: m["t"] == "ended")
    error = box.of("ended")[0]["error"]
    assert "arrêté brutalement (SIGKILL)" in error
    assert "--avcodec-hw=none" in error


async def test_what_ffmpeg_says_when_the_channel_does_not_answer_is_shown(fakes, fast, monkeypatch):
    monkeypatch.setenv(
        "FAKE_FFMPEG_ERROR", "rtsp://mafreebox.freebox.fr/fbxtv_pub/stream: Connection timed out"
    )
    monkeypatch.setenv("FAKE_VLC_FAIL", "1")
    monkeypatch.setenv("FAKE_VLC_STDERR", "main input error: Your input can't be opened\n")
    s, box = session(fakes), Mailbox()
    await s.handle(
        {
            "t": "cmd",
            "name": "play",
            "job": 7,
            "url": "rtsp://mafreebox.freebox.fr/fbxtv_pub/stream?namespace=1&service=201",
            "live": True,
            "volume": 70,
        },
        box,
    )
    await box.wait(lambda m: m["t"] == "ended")
    error = box.of("ended")[0]["error"]
    assert "La chaîne ne répond pas" in error
    assert "ffmpeg : rtsp://mafreebox.freebox.fr/fbxtv_pub/stream: Connection timed out" in error


# --- les tentatives : VLC d'abord, puis une autre façon de lire quand VLC n'ouvre rien ------------------------------


SMB_SOURCE = {"host": "nas", "share": "Freebox", "path": "Films/Un film d'été.mkv"}


def smb_message(job, **extra):
    return {
        "t": "cmd",
        "name": "play",
        "job": job,
        "url": "smb://nas/Freebox/Films/Un%20film%20d%27%C3%A9t%C3%A9.mkv",
        "volume": 70,
        "smb": SMB_SOURCE,
        "smb_user": "ligon",
        "smb_password": "motdepasse-de-ligon",
        **extra,
    }


def test_the_ways_of_reading_are_planned_in_order():
    src = ps.SmbSource("nas", "Freebox", ("Films", "a.mkv"))
    plan = ps.plan_attempts
    assert plan(
        "smb://nas/a", live555=False, ffmpeg=None, smbclient="/usr/bin/smbclient", smb=src
    ) == ["direct", "smbclient"]
    assert plan("smb://nas/a", live555=False, ffmpeg=None, smbclient=None, smb=src) == ["direct"]
    assert plan(
        "smb://nas/a", live555=False, ffmpeg=None, smbclient="/usr/bin/smbclient", smb=None
    ) == ["direct"]
    unsafe = ps.SmbSource("nas", "Freebox", ("Films", 'a";rm "b.mkv'))
    assert plan(
        "smb://nas/a", live555=False, ffmpeg=None, smbclient="/usr/bin/smbclient", smb=unsafe
    ) == ["direct"]
    assert plan(
        "rtsp://box/x", live555=False, ffmpeg="/usr/bin/ffmpeg", smbclient=None, smb=None
    ) == ["ffmpeg"]
    assert plan(
        "rtsp://box/x", live555=True, ffmpeg="/usr/bin/ffmpeg", smbclient=None, smb=None
    ) == ["direct", "ffmpeg"]
    assert plan("rtsp://box/x", live555=True, ffmpeg=None, smbclient=None, smb=None) == ["direct"]
    assert plan("rtsp://box/x", live555=False, ffmpeg=None, smbclient=None, smb=None) == [
        "ffmpeg"
    ]  # le message dira « ffmpeg manque »
    assert plan("/tmp/a.mkv", live555=False, ffmpeg=None, smbclient=None, smb=None) == ["direct"]


def test_the_share_the_dock_names_is_checked_again_by_the_helper():
    ok = ps.smb_source_from({"smb": SMB_SOURCE})
    assert ok == ps.SmbSource("nas", "Freebox", ("Films", "Un film d'été.mkv"))
    for bad in (
        {},
        {"smb": "nas"},
        {"smb": {**SMB_SOURCE, "host": "nas; rm -rf /"}},
        {"smb": {**SMB_SOURCE, "host": ""}},
        {"smb": {**SMB_SOURCE, "share": "a/b"}},
        {"smb": {**SMB_SOURCE, "share": ""}},
        {"smb": {**SMB_SOURCE, "path": "../secret.mkv"}},
        {"smb": {**SMB_SOURCE, "path": ""}},
        {"smb": {**SMB_SOURCE, "path": 3}},
    ):
        assert ps.smb_source_from(bad) is None, bad


async def test_when_vlc_cannot_open_a_share_file_smbclient_gives_it_to_vlc_through_a_pipe(
    fakes, fast, monkeypatch, journal
):
    monkeypatch.setenv("FAKE_VLC_FAIL", "1")
    monkeypatch.setenv(
        "FAKE_VLC_FAIL_ON", "smb://"
    )  # le module Samba de VLC échoue ; la lecture d'un tube marche
    monkeypatch.setenv(
        "FAKE_VLC_STDERR",
        "[00aa] smb stream error: open failed for 'nas/x' (No such file or directory)\n",
    )
    s, box = session(fakes), Mailbox()
    await s.handle(smb_message(8), box)
    await box.wait(
        lambda m: m["t"] == "state" and m["job"] == 8 and m["state"] == "playing", timeout=15
    )
    assert not box.of("ended")  # aucune erreur : la lecture s'est faite par le repli
    states = [m for m in box.of("state") if m["state"] == "playing"]
    assert states[-1].get("seekable") is False  # un tube : pas d'avance rapide

    record = json.loads(fakes.log.read_text())
    assert record["args"][-2:] == ["--", "fd://0"]
    assert record["stdin"].startswith("SMBDATA")
    assert (
        record["stdin_is_pipe"] is True
    )  # asyncio donnerait une « socketpair » : VLC n'y lirait rien (constaté)
    assert "--file-caching=3000" in record["args"]
    assert "--config" not in record["args"]  # le mot de passe n'a plus à passer par VLC

    arguments = Path(str(fakes.log) + ".smbclient").read_text().splitlines()
    assert "//nas/Freebox" in arguments
    assert arguments[arguments.index("-D") + 1] == "Films"  # le dossier est un argument à part
    assert 'get "Un film d\'été.mkv" -' in arguments
    assert "-N" not in arguments  # il y a un identifiant
    assert not any("motdepasse-de-ligon" in a for a in arguments)  # jamais dans les arguments
    assert "allinfo" not in " ".join(
        arguments
    )  # pas de contrôle préalable : le premier bloc dit tout
    assert "USER=ligon%motdepasse-de-ligon" in Path(str(fakes.log) + ".smbclient.env").read_text()

    text = journal.read_text(encoding="utf-8")
    assert "tentative 1/2 « direct »" in text
    assert "la tentative « direct » a échoué" in text
    assert "tentative 2/2 « smbclient »" in text
    assert "commande smbclient : " in text
    assert "la lecture a démarré (mode « smbclient »)" in text
    assert "motdepasse-de-ligon" not in text
    assert playerlog.FAILURE_MARK not in text  # tout s'est bien terminé


async def test_an_error_smbclient_writes_into_the_stream_is_explained_not_given_to_vlc(
    fakes, fast, monkeypatch, journal
):
    monkeypatch.setenv("FAKE_VLC_FAIL", "1")
    monkeypatch.setenv("FAKE_VLC_FAIL_ON", "smb://")
    monkeypatch.setenv(
        "FAKE_VLC_STDERR", "[00aa] smb stream error: read failed (Invalid argument)\n"
    )
    Path(str(fakes.log) + ".smberror").write_text("tree connect failed: NT_STATUS_ACCESS_DENIED\n")
    s, box = session(fakes), Mailbox()
    await s.handle(smb_message(9), box)
    await box.wait(lambda m: m["t"] == "ended", timeout=15)
    error = box.of("ended")[0]["error"]
    assert "Accès refusé" in error  # la raison que smbclient a écrite dans le flux
    assert "VLC et smbclient ont tous deux essayé" in error
    assert (
        "direct : smb stream error: read failed (Invalid argument)" in error
    )  # ce que VLC avait dit d'abord
    assert "motdepasse-de-ligon" not in error
    record = json.loads(fakes.log.read_text())
    assert record["args"][-1].startswith(
        "smb://"
    )  # le second VLC n'a jamais été lancé : le texte d'erreur ne lui a pas été donné
    text = journal.read_text(encoding="utf-8")
    assert "smbclient| tree connect failed: NT_STATUS_ACCESS_DENIED" in text
    assert "smbclient n'a pas donné le fichier" in text
    assert "motdepasse-de-ligon" not in text


async def test_an_error_the_table_does_not_know_is_quoted_instead_of_called_unexpected(
    fakes, fast, monkeypatch
):
    monkeypatch.setenv("FAKE_VLC_FAIL", "1")
    monkeypatch.setenv("FAKE_VLC_FAIL_ON", "smb://")
    monkeypatch.setenv(
        "FAKE_VLC_STDERR", "[00aa] smb stream error: read failed (Invalid argument)\n"
    )
    Path(str(fakes.log) + ".smberror").write_text(
        "NT_STATUS_INVALID_PARAMETER opening remote file \\x.mkv\n"
    )
    s, box = session(fakes), Mailbox()
    await s.handle(smb_message(11), box)
    await box.wait(lambda m: m["t"] == "ended", timeout=15)
    error = box.of("ended")[0]["error"]
    assert "NT_STATUS_INVALID_PARAMETER" in error
    assert "autrement que prévu" not in error


async def test_a_smbclient_that_says_nothing_is_reported(fakes, fast, monkeypatch):
    monkeypatch.setenv("FAKE_VLC_FAIL", "1")
    monkeypatch.setenv("FAKE_VLC_FAIL_ON", "smb://")
    monkeypatch.setenv(
        "FAKE_VLC_STDERR", "[00aa] smb stream error: read failed (Invalid argument)\n"
    )
    Path(str(fakes.log) + ".smbsilent").write_text("")
    s, box = session(fakes), Mailbox()
    await s.handle(smb_message(12), box)
    await box.wait(lambda m: m["t"] == "ended", timeout=15)
    assert "n'a rien lu" in box.of("ended")[0]["error"]


async def test_a_smbclient_that_does_not_answer_ends_in_a_sentence_not_a_hang(
    fakes, fast, monkeypatch
):
    monkeypatch.setattr(ps, "SMB_FIRST_BLOCK_WAIT", 0.6)
    monkeypatch.setenv("FAKE_VLC_FAIL", "1")
    monkeypatch.setenv("FAKE_VLC_FAIL_ON", "smb://")
    monkeypatch.setenv(
        "FAKE_VLC_STDERR", "[00aa] smb stream error: read failed (Invalid argument)\n"
    )
    Path(str(fakes.log) + ".smbslow").write_text("")
    s, box = session(fakes), Mailbox()
    await s.handle(smb_message(13), box)
    await box.wait(lambda m: m["t"] == "ended", timeout=15)
    assert "ne répond pas" in box.of("ended")[0]["error"]


async def test_the_startup_report_says_what_the_pi_is_and_what_it_found(fakes, journal):
    s = session(fakes)
    await s.log_startup()
    text = journal.read_text(encoding="utf-8")
    assert "=== assistant de lecture démarré ===" in text
    assert re.search(
        r"machine : \S+, Python en (32|64) bits, noyau ", text
    )  # un Pi en 32 bits lit mal les gros fichiers
    assert f"VLC : {fakes.vlc}" in text
    assert f"smbclient : {fakes.smbclient}" in text
    assert "modules de VLC : " in text
    assert "langue de la session" in text


async def test_the_whole_file_is_relayed_to_vlc_not_only_the_first_block(fakes, fast, monkeypatch):
    monkeypatch.setenv("FAKE_VLC_FAIL", "1")
    monkeypatch.setenv("FAKE_VLC_FAIL_ON", "smb://")
    monkeypatch.setenv(
        "FAKE_VLC_STDERR", "[00aa] smb stream error: read failed (Invalid argument)\n"
    )
    Path(str(fakes.log) + ".smbbig").write_text("")
    s, box = session(fakes), Mailbox()
    await s.handle(smb_message(14), box)
    await box.wait(
        lambda m: m["t"] == "state" and m["job"] == 14 and m["state"] == "playing", timeout=15
    )
    record = json.loads(fakes.log.read_text())
    assert (
        record["stdin"] == "A" * 1000
    )  # VLC a reçu les octets du fichier, pas le texte d'un message
    await s._stop_playback()


async def test_stopping_a_relayed_playback_stops_smbclient_too(fakes, fast, monkeypatch):
    monkeypatch.setenv("FAKE_VLC_FAIL", "1")
    monkeypatch.setenv("FAKE_VLC_FAIL_ON", "smb://")
    monkeypatch.setenv(
        "FAKE_VLC_STDERR", "[00aa] smb stream error: read failed (Invalid argument)\n"
    )
    s, box = session(fakes), Mailbox()
    await s.handle(smb_message(15), box)
    await box.wait(
        lambda m: m["t"] == "state" and m["job"] == 15 and m["state"] == "playing", timeout=15
    )
    pids = [process.pid for process in s._playback._procs]
    assert len(pids) == 2  # smbclient puis VLC
    await s._stop_playback()
    for pid in pids:
        with pytest.raises(ProcessLookupError):
            os.kill(pid, 0)  # plus aucun des deux


async def test_no_second_attempt_when_the_share_file_is_unknown_to_the_helper(
    fakes, fast, monkeypatch
):
    monkeypatch.setenv("FAKE_VLC_FAIL", "1")
    monkeypatch.setenv(
        "FAKE_VLC_STDERR", "[00aa] smb stream error: open failed for 'nas/x' (Permission denied)\n"
    )
    s, box = session(fakes), Mailbox()
    message = smb_message(10)
    del message["smb"]
    await s.handle(message, box)
    await box.wait(lambda m: m["t"] == "ended", timeout=15)
    assert "Accès refusé" in box.of("ended")[0]["error"]
    assert not Path(str(fakes.log) + ".smbclient").exists()  # smbclient n'a jamais été lancé


async def test_vlc_and_ffmpeg_speak_english_whatever_the_language_of_the_session(fakes, fast):
    """VLC traduit ses messages et sa réponse « status » : l'assistant ne reconnaît que l'anglais."""
    s = Session(
        "ws://unused",
        vlc=fakes.vlc,
        ffmpeg=fakes.ffmpeg,
        smbclient=fakes.smbclient,
        live555=False,
        runtime_dir=fakes.run,
        extra_vlc_args=[],
        env={
            **os.environ,
            "DISPLAY": ":9",
            "LANG": "fr_FR.UTF-8",
            "LC_ALL": "fr_FR.UTF-8",
            "LANGUAGE": "fr",
        },
    )
    s.windows, s.touch = FakeWindows(), FakeTouch()
    s._loop = asyncio.get_running_loop()
    box = Mailbox()
    await s.handle({"t": "cmd", "name": "play", "job": 4, "url": "/tmp/a.mkv", "volume": 70}, box)
    await box.wait(lambda m: m["t"] == "state" and m["state"] == "playing")
    try:
        record = json.loads(fakes.log.read_text())
        assert record["lc_all"] == "C.UTF-8"
        assert record["language"] == "C"
    finally:
        await s._stop_playback()
    assert ps.session_environment({"LANG": "fr_FR.UTF-8"})["LC_ALL"] == "C.UTF-8"


def test_the_bridge_copies_only_the_picture_and_the_sound():
    command = ps.ffmpeg_bridge_command(
        "ffmpeg", "rtsp://mafreebox.freebox.fr/fbxtv_pub/stream?namespace=1&service=201"
    )
    maps = [command[i + 1] for i, word in enumerate(command) if word == "-map"]
    assert maps == [
        "0:v?",
        "0:a?",
    ]  # ni télétexte ni données (un copie brut ne sait pas les écrire)
    assert "0" not in maps
    assert (
        command[command.index("-loglevel") + 1] == "info"
    )  # le journal garde tout ce que ffmpeg dit


async def test_a_new_play_replaces_the_current_one(fakes, fast):
    s, box = session(fakes), Mailbox()
    await s.handle(
        {"t": "cmd", "name": "play", "job": 1, "url": "smb://nas/a.mkv", "volume": 70}, box
    )
    await box.wait(lambda m: m["t"] == "state" and m["job"] == 1 and m["state"] == "playing")
    first = s._playback._procs[-1].pid
    await s.handle(
        {"t": "cmd", "name": "play", "job": 2, "url": "smb://nas/b.mkv", "volume": 70}, box
    )
    try:
        await box.wait(lambda m: m["t"] == "state" and m["job"] == 2 and m["state"] == "playing")
        with pytest.raises(ProcessLookupError):
            os.kill(first, 0)
        assert not box.of("ended")  # l'ancienne lecture a été remplacée, pas terminée
    finally:
        await s._stop_playback()


async def test_stop_for_an_old_job_does_not_stop_the_new_one(fakes, fast):
    s, box = session(fakes), Mailbox()
    await s.handle(
        {"t": "cmd", "name": "play", "job": 5, "url": "smb://nas/a.mkv", "volume": 70}, box
    )
    try:
        await box.wait(lambda m: m["t"] == "state" and m["state"] == "playing")
        await s.handle({"t": "cmd", "name": "stop", "job": 4}, box)
        assert s._playback is not None
        await s.handle({"t": "cmd", "name": "stop", "job": 5}, box)
        assert s._playback is None
    finally:
        await s._stop_playback()


async def test_without_vlc_the_dock_is_told(fakes):
    s, box = session(fakes), Mailbox()
    s.vlc = None
    await s.handle({"t": "cmd", "name": "play", "job": 1, "url": "smb://nas/a.mkv"}, box)
    assert "VLC est absent" in box.of("ended")[0]["error"]


async def test_junk_commands_are_ignored(fakes):
    s, box = session(fakes), Mailbox()
    for message in (
        {"t": "hello"},
        {"t": "cmd", "name": "rm -rf", "job": 1},
        {"t": "cmd", "name": "front", "to": "mars"},
        {"t": "cmd", "name": "toggle"},
    ):
        await s.handle(message, box)
    assert box.messages == []
    assert s.front == "dock"


# --- la lecture simulée (interface de développement et essais de bout en bout) -----------------------------------------------


async def test_the_demo_playback_advances_pauses_and_seeks():
    pb = DemoPlayback("x", live=False, volume=70)
    await pb.start()
    await asyncio.sleep(0.05)
    assert (await pb.status())["state"] == "playing"
    await pb.command("seek", delta=100)
    assert (await pb.status())["position"] >= 100
    await pb.command("toggle")
    paused = await pb.status()
    assert paused["state"] == "paused"
    await asyncio.sleep(0.05)
    assert (await pb.status())["position"] == paused["position"]
    await pb.command("resume")
    assert (await pb.status())["state"] == "playing"
    await pb.stop()
    assert pb.finished


async def test_the_demo_channel_is_live():
    pb = DemoPlayback("x", live=True, volume=70)
    await pb.start()
    status = await pb.status()
    assert (status["position"], status["length"]) == (None, None)


# --- avec le vrai VLC (si l'on n'est pas root : VLC refuse de tourner en root) ---------------------------------------------------

needs_real_vlc = pytest.mark.skipif(
    not (ps.find_vlc() and shutil.which("ffmpeg")) or os.geteuid() == 0,
    reason="VLC et ffmpeg requis, et un utilisateur ordinaire (VLC refuse de tourner en root)",
)
DUMMY = ["--vout", "dummy", "--aout", "dummy"]


@pytest.fixture(scope="module")
def clip(tmp_path_factory):
    path = tmp_path_factory.mktemp("clip") / "essai.mp4"
    if shutil.which("ffmpeg"):
        subprocess.run(
            [
                "ffmpeg",
                "-loglevel",
                "error",
                "-y",
                "-f",
                "lavfi",
                "-i",
                "testsrc2=size=320x180:rate=12:duration=6",
                "-f",
                "lavfi",
                "-i",
                "sine=duration=6",
                "-c:v",
                "libx264",
                "-pix_fmt",
                "yuv420p",
                "-c:a",
                "aac",
                "-shortest",
                str(path),
            ],
            check=True,
        )
    return path


def real_playback(tmp_path, url, *, live=False, live555=False):
    run = tmp_path / "run"
    run.mkdir(exist_ok=True, mode=0o700)
    return VlcPlayback(
        url,
        live=live,
        volume=0,
        vlc=ps.find_vlc(),
        ffmpeg=shutil.which("ffmpeg"),
        live555=live555,
        runtime_dir=run,
        env=dict(os.environ),
        extra=DUMMY,
        job=1,
    )


@needs_real_vlc
async def test_the_real_vlc_plays_pauses_seeks_and_ends(tmp_path, clip):
    pb = real_playback(tmp_path, str(clip))
    await pb.start()
    try:
        status = await settled(pb, timeout=20)
        assert 4 <= status["length"] <= 7
        await pb.command("toggle")
        assert (await pb.status())["state"] == "paused"
        await pb.command("resume")
        assert (await pb.status())["state"] == "playing"
        await pb.command("seek", delta=2)
        assert (await pb.status())["position"] >= 2
        await until(lambda: pb.finished, timeout=20)
        assert pb.error() is None  # il a joué jusqu'au bout : une fin normale
    finally:
        await pb.stop()


@needs_real_vlc
async def test_the_real_vlc_reports_a_missing_file(tmp_path):
    pb = real_playback(tmp_path, str(tmp_path / "absent.mkv"))
    await pb.start()
    try:
        await until(lambda: pb.finished, timeout=20)
        await asyncio.sleep(0.2)
        assert pb.error() is not None
    finally:
        await pb.stop()


@needs_real_vlc
async def test_the_real_vlc_reads_an_rtsp_channel_through_the_bridge(tmp_path, clip):
    """Un flux RTSP comme celui de la Freebox (du MPEG-TS sur RTP), servi par un autre VLC : le pont le recopie."""
    import socket

    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    server = subprocess.Popen(  # noqa: ASYNC220
        [
            ps.find_vlc(),
            "-I",
            "dummy",
            "--no-video",
            "--no-audio",
            "--loop",
            str(clip),
            "--sout",
            f"#rtp{{mux=ts,sdp=rtsp://127.0.0.1:{port}/fbxtv_pub/stream}}",
            "--sout-keep",
        ],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        await asyncio.sleep(2.0)
        pb = real_playback(
            tmp_path,
            f"rtsp://127.0.0.1:{port}/fbxtv_pub/stream?namespace=1&service=201&flavour=sd",
            live=True,
        )
        await pb.start()
        try:
            status = await settled(pb, timeout=25)
            assert (status["position"], status["length"]) == (None, None)
            await asyncio.sleep(1.5)
            assert not pb.finished
        finally:
            await pb.stop()
    finally:
        server.terminate()
        server.wait(5)
