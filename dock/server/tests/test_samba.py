"""Kiosque : parcourir les partages Samba de la Freebox (smbclient), avec un vrai serveur Samba local quand il y en a un."""

from __future__ import annotations

import asyncio
import contextlib
import os
import pwd
import shutil
import signal
import socket
import stat
import subprocess
import tempfile
import textwrap
import time
from pathlib import Path

import pytest

from mydock_dock import samba
from mydock_dock.samba import SambaBrowser, SambaError

# --- ce qu'on accepte de l'écran -------------------------------------------------------------------------------------


@pytest.mark.parametrize("path", ["", None])
def test_the_root_of_a_share_is_the_empty_path(path):
    assert samba.clean_path(path) == []


def test_a_path_is_cut_in_folders():
    assert samba.clean_path("Films/Dessins animés/") == ["Films", "Dessins animés"]
    assert samba.clean_path("/Films//Dessins") == ["Films", "Dessins"]


@pytest.mark.parametrize(
    "path",
    [
        "../secret",
        "Films/../..",
        "Films/./x",
        "a\\b",
        "a\x00b",
        "a\nb",
        "x" * 300,
        "a/" + "d" * 300,
        12,
    ],
)
def test_a_path_that_climbs_or_hides_something_is_refused(path):
    with pytest.raises(SambaError, match=r"invalide"):
        samba.clean_path(path)


@pytest.mark.parametrize("share", ["", "  ", "a/b", "a\\b", "..", "a\x07", 3, None])
def test_a_share_name_is_checked(share):
    with pytest.raises(SambaError, match=r"invalide"):
        samba.clean_share(share)


def test_semicolons_and_quotes_are_ordinary_characters_here():
    assert samba.clean_path('Films; put x/"guillemets"') == ["Films; put x", '"guillemets"']


def test_the_address_of_a_file_is_encoded_for_vlc():
    url = samba.smb_url("mafreebox.freebox.fr", "Disque dur", ["Films", "Un film d'été (2020).mkv"])
    assert (
        url
        == "smb://mafreebox.freebox.fr/Disque%20dur/Films/Un%20film%20d%27%C3%A9t%C3%A9%20%282020%29.mkv"
    )


@pytest.mark.parametrize("name", ["a.mkv", "B.MP4", "x.m2ts", "é.avi", "z.ts"])
def test_video_names(name):
    assert samba.is_video(name)


@pytest.mark.parametrize(
    "name", ["notes.txt", "affiche.jpg", "film", "sous-titres.srt", "archive.zip"]
)
def test_other_files_are_not_videos(name):
    assert not samba.is_video(name)


# --- ce que dit smbclient ----------------------------------------------------------------------------------------------

LISTING = """  .                                   D        0  Wed Nov 11 11:46:01 2026
  ..                                  D        0  Wed Nov 11 11:46:01 2026
  Séries                              D        0  Wed Nov 11 11:46:01 2026
  Films 10                            D        0  Wed Nov 11 11:46:01 2026
  Films 2                             D        0  Wed Nov 11 11:46:01 2026
  .cache                              DH       0  Wed Nov 11 11:46:01 2026
  @eaDir                              D        0  Wed Nov 11 11:46:01 2026
  $RECYCLE.BIN                        DHS      0  Wed Nov 11 11:46:01 2026
  Un film d'été (2020).mkv            N 24555840  Wed Nov 11 11:46:01 2026
  a b c.MP4                           A     1234  Thu Nov  5 09:01:02 2026
  lisez-moi.txt                       N        6  Wed Nov 11 11:46:01 2026
  .cache.mkv                          N      999  Wed Nov 11 11:46:01 2026
  secret.mkv                          NH     999  Wed Nov 11 11:46:01 2026

\t\t264212084 blocks of size 1024. 28395300 blocks available
"""


def test_folders_come_first_and_numbers_sort_as_numbers():
    entries = samba.parse_listing(LISTING)
    assert [(e.name, e.is_dir) for e in entries] == [
        ("Films 2", True),
        ("Films 10", True),
        ("Séries", True),
        ("a b c.MP4", False),
        ("Un film d'été (2020).mkv", False),
    ]
    assert entries[-1].size == 24555840


def test_hidden_files_nas_junk_and_documents_are_left_out():
    names = {e.name for e in samba.parse_listing(LISTING)}
    assert not names & {
        ".cache",
        "@eaDir",
        "$RECYCLE.BIN",
        "lisez-moi.txt",
        ".cache.mkv",
        "secret.mkv",
        ".",
        "..",
    }


def test_a_listing_is_capped():
    big = "\n".join(
        f"  d{i:04d}                              D        0  Wed Nov 11 11:46:01 2026"
        for i in range(samba.MAX_ENTRIES + 50)
    )
    assert len(samba.parse_listing(big)) == samba.MAX_ENTRIES


def test_shares_are_the_disks_only():
    out = "Disk|Disque dur|\nDisk|Enregistrements|Mes films\nDisk|C$|Partage par défaut\nIPC|IPC$|IPC Service\nPrinter|Imprimante|\nDisk|Disque dur|\n"
    assert samba.parse_shares(out) == ["Disque dur", "Enregistrements"]


@pytest.mark.parametrize(
    ("output", "expected"),
    [
        ("tree connect failed: NT_STATUS_ACCESS_DENIED", "Accès refusé"),
        ("session setup failed: NT_STATUS_LOGON_FAILURE", "Identifiant ou mot de passe refusé"),
        ("tree connect failed: NT_STATUS_BAD_NETWORK_NAME", "partage n'existe pas"),
        ("cd \\Absent\\: NT_STATUS_OBJECT_NAME_NOT_FOUND", "dossier n'existe plus"),
        (
            "do_connect: Connection to 192.168.0.9 failed (Error NT_STATUS_CONNECTION_REFUSED)",
            "refuse la connexion",
        ),
        ("Connection to nas failed (Error something)", "injoignable"),
        ("quelque chose d'inattendu", "autrement que prévu"),
    ],
)
def test_smbclient_errors_are_said_in_french(output, expected):
    assert expected in samba.explain(output, "nas")


# --- le mot de passe ne passe jamais par la ligne de commande ----------------------------------------------------------


@pytest.fixture
def fake_smbclient(tmp_path, monkeypatch):
    """Un faux smbclient qui note ses arguments et son environnement, puis répond comme le vrai."""
    log = tmp_path / "appel.txt"
    script = tmp_path / "smbclient"
    script.write_text(
        textwrap.dedent(
            f"""\
            #!/bin/sh
            {{ printf 'ARG:%s\\n' "$@"; echo "USER=$USER"; }} > {log}
            case "$*" in
              *-L*) echo "Disk|Films|" ;;
              *) printf '  Dessins                             D        0  Wed Nov 11 11:46:01 2026\\n' ;;
            esac
            """
        )
    )
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    real_which = shutil.which
    monkeypatch.setattr(
        shutil,
        "which",
        lambda name, *a, **k: str(script) if name == "smbclient" else real_which(name, *a, **k),
    )
    return log


def called(log):
    """(les arguments, la variable USER) de l'appel noté par le faux smbclient."""
    lines = log.read_text().splitlines()
    return [line[4:] for line in lines if line.startswith("ARG:")], next(
        line[5:] for line in lines if line.startswith("USER=")
    )


async def test_the_password_goes_through_the_environment_not_the_arguments(fake_smbclient):
    browser = SambaBrowser(lambda: "nas.local", lambda: "freebox", lambda: "p@ss;w0rd%1")
    assert await browser.shares() == ["Films"]
    called = fake_smbclient.read_text()
    assert "p@ss" not in called.split("USER=")[0]  # pas dans les arguments
    assert "USER=freebox%p@ss;w0rd%1" in called  # dans l'environnement du seul processus smbclient
    assert (
        "-N" not in called.split("USER=")[0].split()
    )  # un identifiant est donné : pas d'accès anonyme


async def test_without_an_identifier_the_access_is_anonymous(fake_smbclient):
    browser = SambaBrowser(lambda: "nas.local", lambda: "", lambda: "")
    await browser.shares()
    args, user = called(fake_smbclient)
    assert "-N" in args
    assert user == ""


async def test_the_folder_is_an_argument_of_its_own_never_part_of_the_command(fake_smbclient):
    browser = SambaBrowser(lambda: "nas.local", lambda: "", lambda: "")
    await browser.listing("Films", ["Dessins; del *", 'a"b'])
    args, _ = called(fake_smbclient)
    assert args[args.index("-c") + 1] == "ls"  # la commande reste « ls », sans rien d'autre
    assert (
        args[args.index("-D") + 1] == 'Dessins; del *\\a"b'
    )  # le dossier : un seul argument, tel quel


async def test_no_server_configured_is_said():
    browser = SambaBrowser(lambda: "", lambda: "", lambda: "")
    with pytest.raises(SambaError, match=r"Aucun serveur de fichiers"):
        await browser.shares()
    with pytest.raises(SambaError, match=r"Aucun serveur de fichiers"):
        await browser.listing("Films", [])


async def test_a_missing_smbclient_is_said(monkeypatch):
    monkeypatch.setattr(shutil, "which", lambda *a, **k: None)
    browser = SambaBrowser(lambda: "nas.local", lambda: "", lambda: "")
    assert not browser.available()
    with pytest.raises(SambaError, match=r"sudo apt install smbclient"):
        await browser.shares()


# --- avec un vrai serveur Samba ---------------------------------------------------------------------------------------

needs_samba = pytest.mark.skipif(
    not (shutil.which("smbd") and shutil.which("smbclient")), reason="smbd et smbclient requis"
)


def _free_port() -> int:
    with contextlib.closing(socket.socket()) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@pytest.fixture(scope="module")
def nas():
    """Un vrai Samba sur 127.0.0.1 : un partage libre (« Disque dur ») et, si l'on est root, un partage à mot de passe."""
    if not (shutil.which("smbd") and shutil.which("smbclient")):
        pytest.skip("smbd et smbclient requis")
    # Dans /tmp, ouvert à tous : le dossier de pytest est fermé aux autres, or « nobody » (compte invité de Samba
    # quand on est root) doit pouvoir le traverser.
    root = Path(tempfile.mkdtemp(prefix="mydock-nas-"))
    root.chmod(0o755)
    share = root / "share"
    (share / "Films" / "Dessins animés").mkdir(parents=True)
    (share / "Films; put x").mkdir()
    (share / "Enregistrements").mkdir()
    (share / "Films" / "Un film d'été (2020).mkv").write_bytes(b"film")
    (share / "Films" / "Dessins animés" / "petit.mp4").write_bytes(b"petit")
    (share / "Films" / "lisez-moi.txt").write_text("notes")
    (share / "Enregistrements" / "Journal.ts").write_bytes(b"ts")
    private = root / "prive"
    private.mkdir()
    (private / "secret.mkv").write_bytes(b"secret")
    for path in (root, share, private, *share.rglob("*"), *private.rglob("*")):
        path.chmod(0o755 if path.is_dir() else 0o644)
    is_root = os.geteuid() == 0
    guest = "nobody" if is_root else pwd.getpwuid(os.geteuid()).pw_name
    port = _free_port()
    for name in ("lock", "private", "ncalrpc", "cache", "pid"):
        (root / name).mkdir()
    conf = root / "smb.conf"
    protected = (
        f"[Prive]\n   path = {private}\n   guest ok = no\n   valid users = freebox\n   read only = yes\n"
        if is_root
        else ""
    )
    conf.write_text(
        f"""[global]
   workgroup = WORKGROUP
   netbios name = FBXTEST
   interfaces = 127.0.0.1
   bind interfaces only = yes
   smb ports = {port}
   server min protocol = SMB2
   map to guest = Bad User
   guest account = {guest}
   log file = {root}/smbd.log
   pid directory = {root}/pid
   lock directory = {root}/lock
   state directory = {root}/lock
   cache directory = {root}/cache
   private dir = {root}/private
   ncalrpc dir = {root}/ncalrpc
   disable spoolss = yes
   load printers = no
   printing = bsd
   printcap name = /dev/null
[Disque dur]
   path = {share}
   guest ok = yes
   read only = yes
{protected}"""
    )
    if is_root:
        with contextlib.suppress(subprocess.CalledProcessError):
            subprocess.run(
                ["useradd", "-M", "-s", "/usr/sbin/nologin", "freebox"],
                check=False,
                capture_output=True,
            )
        subprocess.run(
            ["smbpasswd", "-a", "-s", "-c", str(conf), "freebox"],
            input="motdepasse\nmotdepasse\n",
            text=True,
            check=True,
            capture_output=True,
        )
    # Dans sa propre session : à son arrêt smbd signale tout son groupe de processus, jamais celui de pytest
    # (sans « --no-process-group » il ne démarre pas dans tous les environnements).
    server = subprocess.Popen(
        ["smbd", "-F", "-s", str(conf), "--no-process-group"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    try:
        for _ in range(60):
            with (
                contextlib.suppress(OSError),
                socket.create_connection(("127.0.0.1", port), timeout=0.5),
            ):
                break
            time.sleep(0.25)
        else:
            pytest.skip("le serveur Samba de l'essai n'a pas démarré")
        yield type("Nas", (), {"port": port, "is_root": is_root, "share": share})()
    finally:
        with contextlib.suppress(ProcessLookupError):
            os.killpg(server.pid, signal.SIGTERM)
        with contextlib.suppress(subprocess.TimeoutExpired):
            server.wait(5)
        with contextlib.suppress(ProcessLookupError):
            os.killpg(server.pid, signal.SIGKILL)
        shutil.rmtree(root, ignore_errors=True)


def browser(nas, user="", password="", host="127.0.0.1"):
    return SambaBrowser(lambda: host, lambda: user, lambda: password, port=nas.port)


@needs_samba
async def test_the_shares_of_a_real_server(nas):
    shares = await browser(nas).shares()
    assert "Disque dur" in shares
    assert not any(s.endswith("$") for s in shares)


@needs_samba
async def test_the_root_of_a_share_lists_its_folders(nas):
    entries = await browser(nas).listing("Disque dur", [])
    assert [(e.name, e.is_dir) for e in entries] == [
        ("Enregistrements", True),
        ("Films", True),
        ("Films; put x", True),
    ]


@needs_samba
async def test_a_subfolder_with_accents_apostrophes_and_spaces(nas):
    entries = await browser(nas).listing("Disque dur", ["Films"])
    assert [(e.name, e.is_dir) for e in entries] == [
        ("Dessins animés", True),
        ("Un film d'été (2020).mkv", False),
    ]  # lisez-moi.txt : pas une vidéo
    assert entries[1].size == 4


@needs_samba
async def test_a_folder_name_with_a_semicolon_is_just_a_folder(nas):
    """« Films; put x » existe : on l'ouvre. Et un dossier au nom pareil mais absent n'exécute rien."""
    assert await browser(nas).listing("Disque dur", ["Films; put x"]) == []
    with pytest.raises(SambaError, match=r"n'existe plus"):
        await browser(nas).listing("Disque dur", ["Films; put y"])
    assert not (nas.share / "y").exists()
    assert not (nas.share / "x").exists()


@needs_samba
async def test_an_unknown_share_or_folder_is_said(nas):
    with pytest.raises(SambaError, match=r"partage n'existe pas"):
        await browser(nas).listing("Nexiste pas", [])
    with pytest.raises(SambaError, match=r"dossier n'existe plus"):
        await browser(nas).listing("Disque dur", ["Absent"])


@needs_samba
async def test_a_closed_port_is_said(nas):
    closed = SambaBrowser(lambda: "127.0.0.1", lambda: "", lambda: "", port=_free_port())
    with pytest.raises(SambaError, match=r"refuse la connexion|injoignable"):
        await closed.shares()


@needs_samba
async def test_a_protected_share_asks_for_an_identifier_then_opens(nas):
    if not nas.is_root:
        pytest.skip("le partage à mot de passe demande un Samba lancé par root")
    with pytest.raises(SambaError, match=r"Accès refusé"):
        await browser(nas).listing("Prive", [])
    entries = await browser(nas, "freebox", "motdepasse").listing("Prive", [])
    assert [e.name for e in entries] == ["secret.mkv"]
    with pytest.raises(SambaError, match=r"refusé"):
        await browser(nas, "freebox", "faux").listing("Prive", [])


# --- lire un fichier par smbclient (quand le module Samba de VLC ne l'ouvre pas) ------------------------------------


def test_only_names_that_cannot_break_the_smbclient_command_line_are_streamed():
    assert samba.stream_name_is_safe(["Films", "Un film d'été (2020).mkv"])
    assert samba.stream_name_is_safe(
        ["Films; put x", "a.mkv"]
    )  # un dossier passe par -D : jamais dans la commande
    assert not samba.stream_name_is_safe(['a";rm "b.mkv'])
    assert not samba.stream_name_is_safe(["a;b.mkv"])
    assert not samba.stream_name_is_safe(['a"b.mkv'])
    assert not samba.stream_name_is_safe([])


def test_the_stream_command_never_carries_the_password_nor_the_folder_in_the_command():
    command = samba.stream_command(
        "smbclient", "nas", "Disque dur", ["Films", "Dessins", "a.mkv"], user="ligon"
    )
    assert command[0] == "smbclient"
    assert command[1] == "//nas/Disque dur"
    assert command[command.index("-D") + 1] == "Films\\Dessins"
    assert command[command.index("-c") + 1] == 'get "a.mkv" -'
    assert "-N" not in command
    assert command[command.index("-s") + 1] == "/dev/null"  # aucun smb.conf de la machine
    anonymous = samba.stream_command("smbclient", "nas", "S", ["a.mkv"], user="")
    assert "-N" in anonymous
    assert "-D" not in anonymous  # fichier à la racine du partage
    with pytest.raises(SambaError):
        samba.stream_command("smbclient", "nas", "S", ["x;y.mkv"], user="")


def test_the_password_goes_in_the_environment_only():
    env = samba.smbclient_env("ligon", "p@ss;w0rd%1")
    assert env["USER"] == "ligon%p@ss;w0rd%1"
    assert env["LC_ALL"] == "C.UTF-8"
    assert "USER" not in samba.smbclient_env("", "")
    assert not any(
        "p@ss" in a for a in samba.stream_command("smbclient", "nas", "S", ["a.mkv"], user="ligon")
    )


def _fake_smbclient(tmp_path, body):
    script = tmp_path / "smbclient"
    script.write_text("#!/bin/sh\n" + body)
    script.chmod(0o755)
    return str(script)


async def test_a_file_smbclient_reaches_is_accepted(tmp_path):
    binary = _fake_smbclient(tmp_path, "printf 'altname: X\\ncreate_time: Thu Nov 12 2026\\n'\n")
    assert await samba.check_file(binary, "nas", "S", ["a.mkv"], user="", password="") is None


@pytest.mark.parametrize(
    ("output", "expected"),
    [
        ("tree connect failed: NT_STATUS_ACCESS_DENIED", "Accès refusé"),
        ("NT_STATUS_LOGON_FAILURE", "refusé"),
        ("NT_STATUS_OBJECT_NAME_NOT_FOUND getting alt name for \\\\a.mkv", "n'existe"),
        ("tree connect failed: NT_STATUS_BAD_NETWORK_NAME", "partage n'existe pas"),
        ("", "autrement que prévu"),
    ],
)
async def test_a_file_smbclient_cannot_reach_is_explained_before_the_pipe_is_started(
    tmp_path, output, expected
):
    """smbclient écrit ses erreurs sur la sortie standard (la vidéo) : on les lit avant de lancer le tube."""
    binary = _fake_smbclient(tmp_path, f"printf '%s\\n' '{output}'\nexit 0\n")
    message = await samba.check_file(binary, "nas", "S", ["a.mkv"], user="ligon", password="x")
    assert message is not None
    assert expected in message


async def test_the_file_check_refuses_a_name_that_would_break_the_command_line(tmp_path):
    called = tmp_path / "appele"
    binary = _fake_smbclient(tmp_path, f"touch '{called}'\n")
    message = await samba.check_file(binary, "nas", "S", ['a";rm "b.mkv'], user="", password="")
    assert message is not None
    assert not called.exists()  # smbclient n'a même pas été lancé


async def test_the_file_check_does_not_wait_for_a_silent_server(tmp_path):
    binary = _fake_smbclient(tmp_path, "sleep 30\n")
    started = time.monotonic()
    message = await samba.check_file(
        binary, "nas", "S", ["a.mkv"], user="", password="", timeout=1.0
    )
    assert message == "Le serveur de fichiers ne répond pas."
    assert time.monotonic() - started < 8


async def test_the_file_check_says_when_smbclient_cannot_start(tmp_path):
    message = await samba.check_file(
        str(tmp_path / "absent"), "nas", "S", ["a.mkv"], user="", password=""
    )
    assert message is not None
    assert "smbclient ne démarre pas" in message


@needs_samba
async def test_a_real_server_gives_its_file_through_the_pipe_and_check_file_agrees(nas):
    binary = shutil.which("smbclient")
    assert binary
    assert (
        await samba.check_file(
            binary,
            "127.0.0.1",
            "Disque dur",
            ["Films", "Un film d'été (2020).mkv"],
            user="",
            password="",
            port=nas.port,
        )
        is None
    )
    absent = await samba.check_file(
        binary,
        "127.0.0.1",
        "Disque dur",
        ["Films", "Absent.mkv"],
        user="",
        password="",
        port=nas.port,
    )
    assert absent is not None
    assert "n'existe" in absent
    command = samba.stream_command(
        binary,
        "127.0.0.1",
        "Disque dur",
        ["Films", "Un film d'été (2020).mkv"],
        user="",
        port=nas.port,
    )
    process = await asyncio.create_subprocess_exec(
        *command, stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
        env=samba.smbclient_env("", ""),
    )  # fmt: skip
    data, _ = await process.communicate()
    assert data == b"film"  # le fichier, octet pour octet : la sortie standard est la vidéo


@needs_samba
async def test_a_real_protected_share_is_checked_with_its_identifier(nas):
    if not nas.is_root:
        pytest.skip("le partage à mot de passe demande un Samba lancé par root")
    binary = shutil.which("smbclient")
    assert binary
    bad = await samba.check_file(
        binary, "127.0.0.1", "Prive", ["secret.mkv"], user="freebox", password="faux", port=nas.port
    )
    assert bad is not None
    assert "refusé" in bad
    assert (
        await samba.check_file(
            binary,
            "127.0.0.1",
            "Prive",
            ["secret.mkv"],
            user="freebox",
            password="motdepasse",
            port=nas.port,
        )
        is None
    )


@needs_samba
async def test_a_name_with_a_semicolon_cannot_make_smbclient_run_another_order(nas):
    """`get "a";put x` : sans la vérification du nom, smbclient exécuterait le second ordre sur le partage."""
    binary = shutil.which("smbclient")
    assert binary
    with pytest.raises(SambaError):
        samba.stream_command(
            binary, "127.0.0.1", "Disque dur", ['x";mkdir "pwned.mkv'], user="", port=nas.port
        )
    assert not (nas.share / "pwned.mkv").exists()
