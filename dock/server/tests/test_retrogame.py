"""Retrogame : la liste des jeux d'après MAME (vérification, description), son cache, les miniatures."""

from __future__ import annotations

import asyncio
import json
import shutil
import zipfile
from pathlib import Path

import pytest

from mydock_dock import retrogame
from mydock_dock.config import DockConfig, clean_gpio, clean_roms_dir
from mydock_dock.retrogame import GameLibrary, parse_listxml, parse_verify
from mydock_dock.validation import ValidationFailure

VERIFY = """\
romset pong is best available
romset pacman [puckman] is good
romset brokengame is bad
romset neogeo is good
3 romsets found, 3 were OK.
"""

LISTXML = b"""<?xml version="1.0"?>
<!DOCTYPE mame [
<!ELEMENT mame (machine+)>
<!ELEMENT machine (description, year?, manufacturer?, driver?)>
<!ATTLIST machine name CDATA #REQUIRED>
<!ATTLIST machine isbios (yes|no) "no">
<!ATTLIST machine isdevice (yes|no) "no">
<!ATTLIST machine runnable (yes|no) "yes">
<!ATTLIST machine cloneof CDATA #IMPLIED>
<!ELEMENT description (#PCDATA)>
<!ELEMENT year (#PCDATA)>
<!ELEMENT manufacturer (#PCDATA)>
<!ELEMENT driver EMPTY>
<!ATTLIST driver status CDATA #REQUIRED>
]>
<mame>
\t<machine name="pong" sourcefile="atari/pong.cpp">
\t\t<description>Pong (Rev E)</description><year>1972</year><manufacturer>Atari</manufacturer>
\t\t<driver status="good"/>
\t</machine>
\t<machine name="pacman" cloneof="puckman">
\t\t<description>Pac-Man (Midway)</description><year>1980</year><manufacturer>Namco (Midway license)</manufacturer>
\t\t<driver status="imperfect"/>
\t</machine>
\t<machine name="neogeo" isbios="yes">
\t\t<description>Neo-Geo MV-6</description><year>1990</year><manufacturer>SNK</manufacturer>
\t</machine>
\t<machine name="dac_device" isdevice="yes" runnable="no">
\t\t<description>A DAC</description>
\t</machine>
</mame>
"""


class FakeMame:
    """Un faux MAME : il répond comme le vrai à -verifyroms et -listxml, et note ce qu'on lui a demandé."""

    def __init__(self) -> None:
        self.calls: list[list[str]] = []
        self.verify, self.listxml, self.fail = VERIFY, LISTXML, False

    async def __call__(self, argv: list[str], timeout: float) -> tuple[int, bytes, bytes]:
        self.calls.append(argv)
        if self.fail:
            raise OSError("MAME ne démarre pas")
        if "-verifyroms" in argv:
            asked = argv[argv.index("-verifyroms") + 1 :]
            shown = "".join(
                f"{row}\n" for row in self.verify.splitlines() if row.split(" ")[1] in asked
            )
            return 0, shown.encode(), b""
        return 0, self.listxml, b""


def library(tmp_path, runner=None, *, mame="/usr/games/mame"):
    roms = tmp_path / "roms"
    roms.mkdir(exist_ok=True)
    changes: list[int] = []
    lib = GameLibrary(
        lambda: roms,
        tmp_path / "retro",
        on_change=lambda: changes.append(1),
        mame=lambda: mame,
        runner=runner or FakeMame(),
    )
    lib.changes = changes  # type: ignore[attr-defined]
    return lib, roms


def put(roms: Path, *names: str) -> None:
    for name in names:
        path = roms / name
        if name.endswith((".zip", ".7z")):
            with zipfile.ZipFile(path, "w") as archive:
                archive.writestr("x.bin", b"x")
        else:
            path.mkdir()


async def scanned(lib: GameLibrary, **kw):
    lib.refresh(**kw)
    await lib.wait()
    return lib.payload()


# --- ce que MAME écrit ----------------------------------------------------------------------------------------------------


def test_the_verdicts_of_mame_are_read_and_the_missing_games_are_just_absent():
    verdicts = parse_verify(VERIFY)
    assert verdicts == {
        "pong": "best available",
        "pacman": "good",
        "brokengame": "bad",
        "neogeo": "good",
    }
    assert parse_verify("") == {}
    assert parse_verify("romset pong is amazing\nn'importe quoi") == {}


def test_the_description_of_a_game_comes_from_mame_and_bios_and_devices_are_not_games():
    machines = parse_listxml(LISTXML)
    pacman = machines["pacman"]
    assert (pacman.title, pacman.year, pacman.maker) == (
        "Pac-Man (Midway)",
        "1980",
        "Namco (Midway license)",
    )
    assert pacman.parent == "puckman"
    assert pacman.status == "imperfect"
    assert machines["pong"].playable
    assert not machines["neogeo"].playable  # un BIOS
    assert not machines["dac_device"].playable  # un périphérique
    assert parse_listxml(b"pas du xml") == {}
    assert "pong" in parse_listxml(
        LISTXML[: LISTXML.index(b'\t<machine name="pacman"')]
    )  # XML coupé


# --- la liste -------------------------------------------------------------------------------------------------------------


async def test_only_complete_playable_games_are_listed_with_their_full_name(tmp_path):
    lib, roms = library(tmp_path)
    put(roms, "pong.zip", "pacman.zip", "brokengame.zip", "neogeo.zip", "unknown.zip", "notes.txt")
    payload = await scanned(lib)
    assert [g["id"] for g in payload["games"]] == ["pacman", "pong"]  # classés par nom complet
    pacman = payload["games"][0]
    assert pacman["title"] == "Pac-Man (Midway)"
    assert (pacman["year"], pacman["maker"], pacman["status"]) == (
        "1980",
        "Namco (Midway license)",
        "imperfect",
    )
    assert payload["state"] == "idle"
    assert payload["error"] is None
    assert payload["mame"] is True


async def test_folders_and_7z_archives_are_candidates_too(tmp_path):
    fake = FakeMame()
    lib, roms = library(tmp_path, fake)
    put(roms, "pong", "pacman.7z")
    await scanned(lib)
    asked = {
        name
        for call in fake.calls
        if "-verifyroms" in call
        for name in call[call.index("-verifyroms") + 1 :]
    }
    assert asked == {"pong", "pacman"}


async def test_names_that_are_not_game_names_are_set_aside_and_said(tmp_path):
    fake = FakeMame()
    lib, roms = library(tmp_path, fake)
    put(roms, "pong.zip", "Pac Man.zip", "UPPER.zip", "a" * 30 + ".zip")
    payload = await scanned(lib)
    assert sorted(payload["skipped"]) == sorted(["Pac Man.zip", "UPPER.zip", "a" * 30 + ".zip"])
    asked = {n for c in fake.calls if "-verifyroms" in c for n in c[c.index("-verifyroms") + 1 :]}
    assert asked == {"pong"}


async def test_mame_is_started_without_a_shell_in_the_docks_own_folders(tmp_path):
    fake = FakeMame()
    lib, roms = library(tmp_path, fake)
    put(roms, "pong.zip")
    await scanned(lib)
    argv = fake.calls[0]
    assert argv[0] == "/usr/games/mame"
    assert argv[argv.index("-rompath") + 1] == str(roms)
    assert argv[argv.index("-homepath") + 1].startswith(str(tmp_path / "retro"))
    assert argv[argv.index("-inipath") + 1].startswith(str(tmp_path / "retro"))


async def test_the_list_is_kept_between_runs_and_only_changed_files_are_checked_again(tmp_path):
    fake = FakeMame()
    lib, roms = library(tmp_path, fake)
    put(roms, "pong.zip", "pacman.zip")
    await scanned(lib)
    assert len(fake.calls) == 2  # une vérification, une description
    # un autre démarrage du dock : la liste est là tout de suite, sans MAME
    again, _ = library(tmp_path, fake)
    assert [g.name for g in again.games()] == ["pacman", "pong"]
    assert not again.is_stale()
    await scanned(again)
    assert len(fake.calls) == 2  # rien n'a changé : MAME n'est pas relancé
    # un jeu de plus : seul lui est vérifié
    put(roms, "neogeo.zip")
    assert again.is_stale()
    await scanned(again)
    asked = fake.calls[-2][fake.calls[-2].index("-verifyroms") + 1 :]
    assert asked == ["neogeo"]
    # un jeu retiré disparaît
    (roms / "pong.zip").unlink()
    await scanned(again)
    assert [g.name for g in again.games()] == ["pacman"]


async def test_the_refresh_button_checks_everything_again(tmp_path):
    fake = FakeMame()
    lib, roms = library(tmp_path, fake)
    put(roms, "pong.zip")
    await scanned(lib)
    before = len(fake.calls)
    await scanned(lib, force=True)
    assert len(fake.calls) == before + 2


async def test_a_game_that_was_incomplete_is_checked_again_so_a_new_parent_can_fix_it(tmp_path):
    fake = FakeMame()
    lib, roms = library(tmp_path, fake)
    put(roms, "brokengame.zip")
    await scanned(lib)
    assert lib.games() == []
    fake.verify = "romset brokengame is good\n"
    fake.listxml = LISTXML.replace(b'name="pacman"', b'name="brokengame"')
    put(roms, "other.zip")  # le dossier a changé
    await scanned(lib)
    assert [g.name for g in lib.games()] == ["brokengame"]


async def test_without_mame_the_screen_is_told_and_the_old_list_is_kept(tmp_path):
    lib, roms = library(tmp_path, mame=None)
    put(roms, "pong.zip")
    payload = await scanned(lib)
    assert payload["mame"] is False
    assert payload["state"] == "error"
    assert "sudo apt install mame" in payload["error"]


async def test_a_mame_that_cannot_run_is_an_error_not_a_crash(tmp_path):
    fake = FakeMame()
    fake.fail = True
    lib, roms = library(tmp_path, fake)
    put(roms, "pong.zip")
    payload = await scanned(lib)
    assert payload["state"] == "error"
    assert "MAME" in payload["error"]


async def test_the_scan_reports_its_progress_in_chunks(tmp_path, monkeypatch):
    monkeypatch.setattr(retrogame, "CHUNK", 2)
    fake = FakeMame()
    lib, roms = library(tmp_path, fake)
    put(roms, "a.zip", "b.zip", "c.zip", "d.zip", "e.zip")
    await scanned(lib)
    assert len([c for c in fake.calls if "-verifyroms" in c]) == 3
    assert lib.done == lib.total == 5


async def test_a_missing_or_unreadable_folder_is_just_an_empty_list(tmp_path):
    lib, roms = library(tmp_path)
    shutil.rmtree(roms)
    payload = await scanned(lib)
    assert payload["games"] == []
    assert payload["readable"] is False


async def test_a_damaged_cache_is_ignored(tmp_path):
    (tmp_path / "retro").mkdir()
    (tmp_path / "retro" / "games.json").write_text("{pas du json", encoding="utf-8")
    lib, _roms = library(tmp_path)
    assert lib.games() == []
    (tmp_path / "retro" / "games.json").write_text(
        json.dumps({"version": 99, "entries": {}}), encoding="utf-8"
    )
    lib2, _ = library(tmp_path)
    assert lib2.games() == []


# --- la liste blanche -----------------------------------------------------------------------------------------------------


async def test_only_a_game_of_the_list_can_be_found_by_name(tmp_path):
    lib, roms = library(tmp_path)
    put(roms, "pong.zip")
    await scanned(lib)
    assert lib.find("pong") is not None
    for hostile in (
        "../pong",
        "pong.zip",
        "pong; rm -rf /",
        "PONG",
        "",
        "x" * 40,
        None,
        7,
        ["pong"],
        "pacman",
    ):
        assert lib.find(hostile) is None


# --- les miniatures -------------------------------------------------------------------------------------------------------

PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 40


async def test_a_thumbnail_is_served_only_for_a_listed_game_and_only_if_it_is_a_real_image(
    tmp_path,
):
    lib, roms = library(tmp_path)
    put(roms, "pong.zip", "pacman.zip")
    await scanned(lib)
    snap = lib.snap_dir
    snap.mkdir(parents=True)
    (snap / "pong.png").write_bytes(PNG)
    (snap / "pacman.png").write_bytes(b"<html>pas une image</html>")
    (snap / "neogeo.png").write_bytes(PNG)  # pas un jeu de la liste
    assert lib.snap_file("pong") == (snap / "pong.png", "image/png")
    assert lib.snap_file("pacman") is None  # pas une image
    assert lib.snap_file("neogeo") is None  # pas dans la liste
    assert lib.snap_file("../pong") is None
    payload = lib.payload()
    assert {g["id"]: g["snap"] for g in payload["games"]} == {"pacman": False, "pong": True}


async def test_thumbnails_written_by_mame_in_a_folder_are_found_and_links_and_big_files_are_not(
    tmp_path,
):
    lib, roms = library(tmp_path)
    put(roms, "pong.zip", "pacman.zip")
    await scanned(lib)
    (lib.snap_dir / "pong").mkdir(parents=True)
    (lib.snap_dir / "pong" / "0000.png").write_bytes(PNG)
    assert lib.snap_file("pong") == (lib.snap_dir / "pong" / "0000.png", "image/png")
    (lib.snap_dir / "pacman.png").symlink_to(lib.snap_dir / "pong" / "0000.png")
    assert lib.snap_file("pacman") is None  # un lien
    (lib.snap_dir / "pacman.jpg").write_bytes(
        b"\xff\xd8\xff" + b"0" * (retrogame.SNAP_MAX_BYTES + 1)
    )
    assert lib.snap_file("pacman") is None  # trop gros


# --- les réglages ---------------------------------------------------------------------------------------------------------


def test_the_rom_folder_and_the_button_pin_are_validated():
    assert clean_roms_dir("") == ""
    assert clean_roms_dir(" /media/usb/roms ") == "/media/usb/roms"
    for bad in ("roms", "/a/../b", "/a\x00b", "/" + "a" * 250, 12, ["/a"]):
        with pytest.raises(ValidationFailure):
            clean_roms_dir(bad)
    assert clean_gpio(17) == 17
    assert clean_gpio("4") == 4
    for bad in (0, 1, 28, -3, "x", None, True, 3.5, "17.2"):
        with pytest.raises(ValidationFailure):
            clean_gpio(bad)


def test_the_settings_are_saved_in_the_configuration_file(tmp_path):
    path = tmp_path / "dock.toml"
    config = DockConfig.from_dict({}, path)
    assert config.roms_path == config.state_dir / "roms"
    assert config.button_gpio == 17
    config.set_retrogame(roms_dir="/srv/roms", button_gpio=22)
    again = DockConfig.load(path)
    assert (again.roms_path, again.button_gpio) == (Path("/srv/roms"), 22)
    again.set_retrogame(button_gpio=5)  # un champ absent ne change pas
    assert (again.roms_path, again.button_gpio) == (Path("/srv/roms"), 5)
    with pytest.raises(ValidationFailure):
        again.set_retrogame(roms_dir="relatif")
    assert again.roms_path == Path("/srv/roms")  # un refus ne laisse rien derrière
    assert "[retrogame]" in path.read_text(encoding="utf-8")


# --- avec le vrai MAME (s'il est installé) --------------------------------------------------------------------------------

needs_mame = pytest.mark.skipif(retrogame.find_mame() is None, reason="MAME n'est pas installé")


@needs_mame
async def test_the_real_mame_lists_pong_which_needs_no_rom_file(tmp_path):
    roms = tmp_path / "roms"
    roms.mkdir()
    put(roms, "pong.zip", "nothing.zip")
    lib = GameLibrary(lambda: roms, tmp_path / "retro")
    lib.refresh()
    await asyncio.wait_for(lib.wait(), 120)
    assert lib.state == "idle", lib.error
    pong = lib.find("pong")
    assert pong is not None
    assert (pong.title, pong.year, pong.maker) == ("Pong (Rev E)", "1972", "Atari")
    assert lib.find("nothing") is None
