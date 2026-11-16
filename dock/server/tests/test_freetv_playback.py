"""Free TV : tv.free.fr dans Chromium sur l'écran du dock (identité, ligne de commande, Widevine, cycle de vie de la fenêtre)."""

from __future__ import annotations

import asyncio
import json
import os
import signal
import stat
import subprocess
import sys
import time
from pathlib import Path

import pytest

from mydock_dock import freetv_playback as ft
from mydock_dock import playerlog
from mydock_dock.freetv_playback import DemoFreeTvPlayback, FreeTvPlayback

# --- l'identité (user agent) : Free refuse « Linux aarch64 » -------------------------------------------------------------


def test_the_auto_identity_is_a_desktop_chromium_of_the_same_version():
    agent = ft.compute_user_agent("auto", 156)
    assert agent == (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/156.0.0.0 Safari/537.36"
    )
    assert "aarch64" not in agent
    assert "Chrome/158.0.0.0" in ft.compute_user_agent(
        "auto", 158
    )  # elle suit la version installée


def test_without_the_version_of_chromium_the_original_identity_is_kept_rather_than_a_wrong_one():
    assert ft.compute_user_agent("auto", None) is None


def test_an_empty_setting_means_the_original_identity_and_a_custom_one_is_used_as_it_is():
    assert ft.compute_user_agent("", 156) is None
    assert ft.compute_user_agent("   ", 156) is None
    assert ft.compute_user_agent("Mon Navigateur/1.0", 156) == "Mon Navigateur/1.0"
    assert ft.compute_user_agent("Chrome/{major}.0 X", 156) == "Chrome/156.0 X"
    assert ft.compute_user_agent("Chrome/{major}.0 X", None) is None  # on ne devine pas une version


@pytest.mark.parametrize(
    ("version", "major"),
    [
        ("Chromium 156.0.7339.80 built on Debian GNU/Linux 13 (trixie)", 156),
        ("Chromium 156.0.7339.80 Raspberry Pi OS", 156),
        ("Google Chrome 153.0.7500.1", 153),
        ("n'importe quoi", None),
        ("", None),
        (None, None),
    ],
)
def test_the_major_version_is_read_from_the_version_line(version, major):
    assert ft.major_of(version) == major


# --- la ligne de commande -----------------------------------------------------------------------------------------------


def test_the_chromium_command_is_a_list_with_the_flags_that_make_free_tv_play(tmp_path):
    profile = tmp_path / ".config" / "mydock-freetv"
    command = ft.chromium_command("/usr/bin/chromium", profile, user_agent="Mon UA/1.0", scale=0.7)
    assert command[0] == "/usr/bin/chromium"
    assert "--app=https://tv.free.fr" in command
    assert "--ozone-platform=wayland" in command
    assert "--password-store=basic" in command  # sans lui : « créer un trousseau gnome-keyring »
    assert f"--user-data-dir={profile}" in command
    assert "--force-device-scale-factor=0.7" in command
    assert "--user-agent=Mon UA/1.0" in command  # un seul argument : jamais découpé
    assert "--lang=fr-FR" in command
    assert "--start-fullscreen" in command
    assert "--kiosk" in command  # comme le dock : c'est ce qui le met en plein écran sur ce labwc


def test_free_tv_can_be_a_maximised_window_instead_of_a_full_screen_one(tmp_path):
    """Le clavier à l'écran est une couche du compositeur : labwc la cache derrière une fenêtre en plein écran (voir le rapport)."""
    assert "--start-fullscreen" in ft.chromium_command("chromium", tmp_path, fullscreen=True)
    window = ft.chromium_command("chromium", tmp_path, fullscreen=False)
    assert "--start-fullscreen" not in window
    assert "--kiosk" not in window  # sans quoi la fenêtre serait en plein écran quoi qu'on demande


def test_without_an_identity_chromium_keeps_its_own_and_the_flags_file_comes_last(tmp_path):
    command = ft.chromium_command(
        "chromium",
        tmp_path,
        user_agent=None,
        scale=1.0,
        extra=["--disable-gpu", "--force-device-scale-factor=0.5"],
    )
    assert not any(part.startswith("--user-agent") for part in command)
    assert "--force-device-scale-factor=1" in command
    assert command[-2:] == [
        "--disable-gpu",
        "--force-device-scale-factor=0.5",
    ]  # Chromium garde la dernière valeur
    assert command.index("--force-device-scale-factor=1") < command.index(
        "--force-device-scale-factor=0.5"
    )


def test_the_flags_file_is_read_without_comments(tmp_path):
    path = tmp_path / "freetv.flags"
    path.write_text("# un commentaire\n--disable-gpu\n\n  --use-gl=angle  \n")
    assert ft.read_flags(str(path)) == ["--disable-gpu", "--use-gl=angle"]
    assert ft.read_flags(str(tmp_path / "absent")) == []


def test_the_environment_of_chromium_is_french_wayland_only_and_not_the_one_forced_on_vlc():
    env = ft.free_tv_environment(
        {
            "WAYLAND_DISPLAY": "wayland-0",
            "DISPLAY": ":0",
            "LC_ALL": "fr_FR.UTF-8",
            "QT_QPA_PLATFORM": "wayland",
        }
    )
    assert env["WAYLAND_DISPLAY"] == "wayland-0"
    assert "DISPLAY" not in env  # Chromium ne retombe jamais sur XWayland
    assert env["LANGUAGE"] == "fr"
    assert env["LC_ALL"] == "fr_FR.UTF-8"  # celui de la session, pas le « C.UTF-8 » de VLC


def test_the_profile_lives_under_the_home_of_the_screen_user_and_not_next_to_the_kiosk_one(
    tmp_path,
):
    assert ft.profile_dir({"HOME": str(tmp_path)}) == tmp_path / ".config" / "mydock-freetv"
    assert ft.profile_dir({"HOME": "/x", ft.PROFILE_ENV: str(tmp_path)}) == tmp_path
    # le lanceur du kiosque ne ferme (pkill -f) que « --user-data-dir=~/.cache/mydock-chromium » : aucun préfixe commun
    kiosk = "--user-data-dir=/home/mydock-kiosk/.cache/mydock-chromium"
    assert not f"--user-data-dir={ft.profile_dir({'HOME': '/home/mydock-kiosk'})}".startswith(kiosk)
    assert not kiosk.startswith(f"--user-data-dir={ft.profile_dir({'HOME': '/home/mydock-kiosk'})}")


# --- Widevine -------------------------------------------------------------------------------------------------------------


def make_widevine(root: Path, *, version="4.10.2662.3", manifest=True, library=True) -> Path:
    directory = root / "WidevineCdm"
    lib = directory / "_platform_specific" / "linux_arm64"
    lib.mkdir(parents=True)
    if manifest:
        (directory / "manifest.json").write_text(
            json.dumps({"name": "WidevineCdm", "version": version})
        )
    if library:
        (lib / "libwidevinecdm.so").write_bytes(b"\x7fELF")
    return directory


def test_widevine_is_there_when_its_manifest_and_its_library_are(tmp_path):
    directory = make_widevine(tmp_path)
    assert ft.widevine_info([str(directory)]) == (str(directory), "4.10.2662.3")
    assert ft.has_widevine([str(directory)]) is True


def test_widevine_is_missing_without_its_library_or_its_manifest_or_its_folder(tmp_path):
    assert ft.has_widevine([str(make_widevine(tmp_path / "a", library=False))]) is False
    assert ft.has_widevine([str(make_widevine(tmp_path / "b", manifest=False))]) is False
    assert ft.has_widevine([str(tmp_path / "absent")]) is False
    assert ft.widevine_info([]) is None


def test_the_first_place_that_has_widevine_wins_and_a_broken_manifest_is_no_obstacle(tmp_path):
    broken = make_widevine(tmp_path / "x")
    (broken / "manifest.json").write_text("pas du json")
    good = make_widevine(tmp_path / "y", version="1.2.3")
    assert ft.widevine_info([str(tmp_path / "none"), str(broken), str(good)]) == (str(broken), None)
    assert ft.widevine_info([str(tmp_path / "none"), str(good)]) == (str(good), "1.2.3")


# --- les Chromium de Free TV restés ouverts --------------------------------------------------------------------------------


def test_stale_processes_are_found_by_their_profile_argument_only(tmp_path):
    proc = tmp_path / "proc"
    for pid, args in {
        "101": ["chromium", "--app=https://tv.free.fr", f"--user-data-dir={tmp_path}/profil"],
        "102": ["chromium", "--kiosk", f"--user-data-dir={tmp_path}/autre"],
        "103": [
            "chromium",
            f"--user-data-dir={tmp_path}/profil-bis",
        ],  # un autre dossier qui commence pareil
        "104": ["vim", f"{tmp_path}/profil"],
    }.items():
        (proc / pid).mkdir(parents=True)
        (proc / pid / "cmdline").write_bytes("\0".join(args).encode() + b"\0")
    (proc / "self").mkdir()
    assert ft.stale_pids(tmp_path / "profil", str(proc)) == [101]
    assert ft.stale_pids(tmp_path / "rien", str(proc)) == []
    assert ft.stale_pids(tmp_path / "profil", str(tmp_path / "pas-de-proc")) == []


async def test_a_chromium_left_on_the_profile_is_really_closed(tmp_path):
    profile = tmp_path / "profil"
    leftover = subprocess.Popen(  # noqa: ASYNC220
        [sys.executable, "-c", "import time; time.sleep(60)", f"--user-data-dir={profile}"]
    )
    try:
        for _ in range(50):
            if ft.stale_pids(profile):
                break
            await asyncio.sleep(0.05)
        assert leftover.pid in ft.stale_pids(profile)
        assert await ft.kill_stale(profile) == 1
        assert leftover.wait(5) == -signal.SIGTERM
        assert ft.stale_pids(profile) == []
        assert await ft.kill_stale(profile) == 0
    finally:
        if leftover.poll() is None:
            leftover.kill()


# --- pourquoi Chromium s'arrête ---------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("shown", "tail", "code", "expected"),
    [
        (True, "", 0, None),  # la fenêtre a été montrée puis fermée : fin normale
        (True, "n'importe quoi", None, None),
        (False, "Missing X server or $DISPLAY", 1, "s'afficher sur l'écran du dock"),
        (False, "The profile appears to be in use by another Chromium process", 0, "déjà utilisé"),
        (False, "No usable sandbox! Update your kernel", 1, "bac à sable"),
        (False, "", -11, "arrêté brutalement (SIGSEGV)"),
        (True, "", -9, "arrêté brutalement (SIGKILL)"),
        (False, "", 0, "avant d'avoir ouvert Free TV"),
        (False, "rien d'utile", 3, "(code 3)"),
    ],
)
def test_the_reason_chromium_stopped_is_told(shown, tail, code, expected):
    message = ft.explain_exit(shown, tail, code)
    if expected is None:
        assert message is None
    else:
        assert expected in message


# --- un faux Chromium et de fausses fenêtres -------------------------------------------------------------------------------

FAKE_CHROMIUM = """\
#!{python}
import json, os, signal, sys, time
if "--version" in sys.argv:
    print(os.environ.get("FAKE_VERSION", "Chromium 156.0.7339.80 built on Debian GNU/Linux 13 (trixie)")); sys.exit(0)
signal.signal(signal.SIGTERM, lambda *_: sys.exit(0) if not os.environ.get("FAKE_IGNORE_TERM") else None)
record = {{"args": sys.argv[1:], "env": {{k: os.environ.get(k) for k in ("WAYLAND_DISPLAY", "DISPLAY", "LANGUAGE", "PULSE_SINK", "PIPEWIRE_NODE", "LC_ALL")}}, "pid": os.getpid()}}
json.dump(record, open(os.environ["FAKE_LOG"], "w"))
if os.environ.get("FAKE_STDERR"):
    sys.stderr.write(os.environ["FAKE_STDERR"] + "\\n"); sys.stderr.flush()
if os.environ.get("FAKE_EXIT"):
    sys.exit(int(os.environ["FAKE_EXIT"]))
while True:
    time.sleep(0.1)
"""


class FakeWindows:
    """Ce que `Windows` sait faire, sans écran : la fenêtre de Free TV apparaît sur commande, se ferme comme par sa croix."""

    def __init__(self, app_id="chrome-tv.free.fr__-Default", title="Free TV"):
        self.listed: list[tuple[str, str]] = []
        self.app_id, self.title = app_id, title
        self.closed: list[str] = []
        self.fullscreened: list[str] = []
        self.pid_file: Path | None = None

    def show(self):
        self.listed = [("chromium", "MyDock"), (self.app_id, self.title)]

    async def toplevels(self):
        return list(self.listed)

    async def close_app(self, word):
        self.closed.append(word)
        if self.pid_file is not None and self.pid_file.exists():
            with contextlib_suppress():
                os.kill(json.loads(self.pid_file.read_text())["pid"], signal.SIGTERM)
        self.listed = [w for w in self.listed if word not in w[0]]
        return 1

    async def fullscreen(self, app_id):
        self.fullscreened.append(app_id)
        return True


def contextlib_suppress():
    import contextlib

    return contextlib.suppress(ProcessLookupError)


@pytest.fixture
def fake(tmp_path, monkeypatch):
    chromium = tmp_path / "chromium"
    chromium.write_text(FAKE_CHROMIUM.format(python=sys.executable))
    chromium.chmod(chromium.stat().st_mode | stat.S_IEXEC)
    log = tmp_path / "log.json"
    monkeypatch.setenv("FAKE_LOG", str(log))
    monkeypatch.setattr(ft, "KILL_WAIT", 1.0)
    monkeypatch.setattr(ft, "CLOSE_WAIT", 1.5)
    monkeypatch.setattr(ft, "TERM_WAIT", 1.5)
    windows = FakeWindows()
    windows.pid_file = log
    home = tmp_path / "home"
    home.mkdir()
    return type(
        "Fake", (), {"chromium": str(chromium), "log": log, "windows": windows, "home": home}
    )()


def playback(fake, **kw):
    env = {
        "HOME": str(fake.home),
        "WAYLAND_DISPLAY": "wayland-0",
        "DISPLAY": ":0",
        "XDG_RUNTIME_DIR": "/run/user/1000",
    }
    env.update(
        {k: v for k, v in os.environ.items() if k.startswith("FAKE_")}
    )  # l'environnement de la session, pour le faux Chromium
    env.update(kw.pop("env", {}))
    return FreeTvPlayback(
        chromium=fake.chromium,
        windows=kw.pop("windows", fake.windows),
        env=env,
        runtime_dir=fake.home,
        job=kw.pop("job", 1),
        extra=kw.pop("extra", []),
        **kw,
    )


async def until(predicate, timeout=5.0):
    async with asyncio.timeout(timeout):
        while not predicate():
            await asyncio.sleep(0.02)


async def test_free_tv_starts_chromium_on_the_wayland_screen_with_its_own_private_profile(fake):
    pb = playback(fake, env={"PULSE_SINK": "mydock_pc", "PIPEWIRE_NODE": "mydock_pc"})
    await pb.start()
    try:
        await until(fake.log.exists)
        await asyncio.sleep(0.1)
        record = json.loads(fake.log.read_text())
        assert "--app=https://tv.free.fr" in record["args"]
        assert f"--user-data-dir={fake.home}/.config/mydock-freetv" in record["args"]
        assert (
            "--user-agent=Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/156.0.0.0 Safari/537.36"
            in record["args"]
        )
        assert record["env"]["WAYLAND_DISPLAY"] == "wayland-0"
        assert record["env"]["DISPLAY"] is None
        assert record["env"]["LANGUAGE"] == "fr"
        # le son sort sur le PC : Chromium joue dans le puits virtuel, comme VLC et MAME
        assert (record["env"]["PULSE_SINK"], record["env"]["PIPEWIRE_NODE"]) == (
            "mydock_pc",
            "mydock_pc",
        )
        profile = fake.home / ".config" / "mydock-freetv"
        assert (
            stat.S_IMODE(profile.stat().st_mode) == 0o700
        )  # la connexion à Free n'est lisible que par l'utilisateur de l'écran
        assert pb.user_agent
        assert "Chrome/156.0.0.0" in pb.user_agent
    finally:
        await pb.stop()


async def test_the_identity_follows_the_installed_version_and_the_setting(fake, monkeypatch):
    monkeypatch.setenv("FAKE_VERSION", "Chromium 158.0.1.2 built on Debian")
    pb = playback(fake)
    await pb.start()
    try:
        await until(fake.log.exists)
        assert pb.user_agent is not None
        assert "Chrome/158.0.0.0" in pb.user_agent
    finally:
        await pb.stop()
    pb = playback(fake, user_agent_setting="")  # vide : l'identité d'origine
    await pb.start()
    try:
        await until(fake.log.exists)
        await asyncio.sleep(0.1)
        assert not any(
            a.startswith("--user-agent") for a in json.loads(fake.log.read_text())["args"]
        )
    finally:
        await pb.stop()


async def test_the_window_is_found_by_its_address_in_its_identifier_and_only_then_it_plays(fake):
    pb = playback(fake)
    await pb.start()
    try:
        assert (await pb.status())["state"] == "starting"  # la fenêtre n'est pas là
        fake.windows.listed = [("chromium", "MyDock")]  # le dock seul : ce n'est pas Free TV
        assert (await pb.status())["state"] == "starting"
        fake.windows.show()
        status = await pb.status()
        assert status["state"] == "playing"
        assert (status["position"], status["length"], status["seekable"]) == (None, None, False)
        assert pb.app_id == "chrome-tv.free.fr__-Default"
        assert pb.shown
        assert fake.windows.fullscreened == [
            "chrome-tv.free.fr__-Default"
        ]  # le repli plein écran, dès l'apparition de la fenêtre
        await pb.status()
        assert (
            len(fake.windows.fullscreened) == 1
        )  # pas à chaque question : il faut laisser passer le temps
    finally:
        await pb.stop()


async def test_a_maximised_free_tv_is_maximised_and_not_put_in_full_screen(fake):
    fake.windows.maximized = []

    async def maximize(app_id):
        fake.windows.maximized.append(app_id)
        return True

    fake.windows.maximize = maximize
    pb = playback(fake, fullscreen=False)
    await pb.start()
    try:
        await until(fake.log.exists)
        assert "--start-fullscreen" not in json.loads(fake.log.read_text())["args"]
        fake.windows.show()
        await pb.status()
        await pb.status()
        assert fake.windows.maximized == ["chrome-tv.free.fr__-Default"]
        assert fake.windows.fullscreened == []
    finally:
        await pb.stop()


async def test_the_chromium_prefix_of_the_identifier_does_not_matter(fake):
    fake.windows.app_id = "chromium-tv.free.fr__-Default"
    pb = playback(fake)
    await pb.start()
    try:
        fake.windows.show()
        assert (await pb.status())["state"] == "playing"
    finally:
        await pb.stop()


async def test_when_windows_cannot_be_listed_free_tv_is_assumed_shown_after_a_while(
    fake, monkeypatch
):
    monkeypatch.setattr(ft, "BLIND_SHOWN_AFTER", 0.2)
    pb = playback(fake)
    await pb.start()
    try:
        assert (await pb.status())["state"] == "starting"
        await asyncio.sleep(0.3)
        assert (await pb.status())["state"] == "playing"
    finally:
        await pb.stop()


async def test_stopping_closes_the_window_first_so_that_chromium_keeps_the_login(fake):
    pb = playback(fake)
    await pb.start()
    await until(fake.log.exists)
    fake.windows.show()
    await pb.status()
    await pb.stop()
    assert fake.windows.closed == ["tv.free.fr"]
    assert pb.how_stopped == "fenêtre fermée"
    assert pb.finished
    assert pb.error() is None  # un arrêt voulu n'est pas une erreur
    with pytest.raises(ProcessLookupError):
        os.kill(json.loads(fake.log.read_text())["pid"], 0)


async def test_a_chromium_that_ignores_the_window_closing_is_stopped_then_killed(fake, monkeypatch):
    monkeypatch.setenv("FAKE_IGNORE_TERM", "1")
    fake.windows.pid_file = None  # fermer la fenêtre ne fait rien : Chromium est « figé »
    pb = playback(fake)
    await pb.start()
    await until(fake.log.exists)
    await asyncio.sleep(0.2)
    pid = json.loads(fake.log.read_text())["pid"]
    await pb.stop()
    assert pb.how_stopped == "tué"
    assert pb.finished
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)


async def test_a_chromium_that_dies_before_showing_anything_is_explained(fake, monkeypatch):
    monkeypatch.setenv("FAKE_EXIT", "1")
    monkeypatch.setenv("FAKE_STDERR", "Missing X server or $DISPLAY")
    pb = playback(fake)
    await pb.start()
    await until(lambda: pb.finished)
    await pb.wait_output()
    assert "s'afficher sur l'écran du dock" in pb.error()
    await pb.stop()


async def test_a_second_chromium_that_hands_over_to_the_first_and_exits_is_not_a_success(
    fake, monkeypatch
):
    """Un Chromium déjà ouvert sur ce profil reprend la demande et le nouveau s'arrête de suite, sans fenêtre à lui."""
    monkeypatch.setenv("FAKE_EXIT", "0")
    pb = playback(fake)
    await pb.start()
    await until(lambda: pb.finished)
    assert "avant d'avoir ouvert Free TV" in pb.error()
    await pb.stop()


async def test_the_window_closed_by_hand_after_it_was_shown_is_a_normal_end(fake, monkeypatch):
    pb = playback(fake)
    await pb.start()
    await until(fake.log.exists)
    fake.windows.show()
    await pb.status()
    os.kill(json.loads(fake.log.read_text())["pid"], signal.SIGTERM)
    await until(lambda: pb.finished)
    assert pb.error() is None
    await pb.stop()


async def test_a_chromium_left_over_from_before_is_closed_before_the_new_one_starts(fake, tmp_path):
    profile = fake.home / ".config" / "mydock-freetv"
    leftover = subprocess.Popen(  # noqa: ASYNC220
        [sys.executable, "-c", "import time; time.sleep(60)", f"--user-data-dir={profile}"]
    )
    try:
        await until(lambda: leftover.pid in ft.stale_pids(profile))
        pb = playback(fake)
        await pb.start()
        try:
            assert leftover.wait(5) == -signal.SIGTERM
        finally:
            await pb.stop()
    finally:
        if leftover.poll() is None:
            leftover.kill()


async def test_the_start_is_refused_with_a_reason_when_the_profile_cannot_be_made(
    fake, monkeypatch
):
    blocker = fake.home / "fichier"
    blocker.write_text("x")
    pb = playback(fake, profile=blocker / "dedans")
    with pytest.raises(RuntimeError, match=r"ne peut pas écrire son profil"):
        await pb.start()
    monkeypatch.delenv("HOME", raising=False)
    pb = FreeTvPlayback(chromium=fake.chromium, windows=fake.windows, env={}, runtime_dir=fake.home)
    with pytest.raises(RuntimeError, match=r"dossier personnel"):
        await pb.start()


async def test_the_journal_tells_the_identity_and_the_command_but_nothing_secret(
    fake, tmp_path, monkeypatch
):
    path = tmp_path / "player.log"
    monkeypatch.setattr(playerlog, "current_path", lambda: path)
    lines: list[str] = []
    monkeypatch.setattr(playerlog, "event", lines.append)
    monkeypatch.setattr(playerlog, "line", lambda label, text: lines.append(f"{label}| {text}"))
    pb = playback(fake)
    await pb.start()
    try:
        await until(fake.log.exists)
    finally:
        await pb.stop()
    text = "\n".join(lines)
    assert "lancement de Chromium 156" in text
    assert "Chrome/156.0.0.0" in text
    assert "--user-data-dir=" in text


async def test_the_demo_window_shows_up_after_a_second_and_stops_on_request():
    pb = DemoFreeTvPlayback()
    await pb.start()
    assert (await pb.status())["state"] == "starting"
    await asyncio.sleep(1.05)
    assert (await pb.status())["state"] == "playing"
    assert not pb.finished
    await pb.stop()
    assert pb.finished
    assert pb.error() is None
    assert time.monotonic() > 0


async def test_the_dns_check_is_only_advice(monkeypatch):
    async def fail(*_a, **_k):
        raise OSError("pas de réseau")

    loop = asyncio.get_running_loop()
    monkeypatch.setattr(loop, "getaddrinfo", fail)
    assert await ft.host_resolves("tv.free.fr") is False


async def test_full_screen_is_asked_again_a_few_times_then_left_alone(fake):
    """La demande est idempotente : la répéter rattrape une fenêtre qui n'avait pas fini de s'ouvrir, sans la faire à chaque seconde."""
    pb = playback(fake)
    await pb.start()
    try:
        fake.windows.show()
        await pb.status()
        assert len(fake.windows.fullscreened) == 1
        shown = pb._shown_at
        assert shown is not None
        for waited, expected in ((1.0, 1), (1.6, 2), (4.1, 3), (9.1, 4), (60.0, 4)):
            pb._shown_at = time.monotonic() - waited
            await pb.status()
            await pb.status()
            assert len(fake.windows.fullscreened) == expected, (waited, fake.windows.fullscreened)
        assert (
            len(set(fake.windows.fullscreened)) == 1
        )  # toujours la même fenêtre : celle de Free TV
    finally:
        await pb.stop()
