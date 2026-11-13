"""Retrogame en démonstration : la liste de faux jeux (avec images) et les fausses manettes."""

import struct
import zlib

import pytest

from mydock_dock.bluetooth import PadsError
from mydock_dock.demo_retro import (
    DEMO_GAMES,
    DUALSENSE,
    EIGHTBITDO,
    WITHOUT_SNAP,
    XBOX,
    DemoGameLibrary,
    DemoPads,
    draw_snap,
)
from mydock_dock.retrogame import NAME_RE


def decode(png: bytes) -> tuple[int, int, bytes]:
    assert png.startswith(b"\x89PNG\r\n\x1a\n")
    offset, width, height, data = 8, 0, 0, b""
    while offset < len(png):
        (length,) = struct.unpack(">I", png[offset : offset + 4])
        kind, body = png[offset + 4 : offset + 8], png[offset + 8 : offset + 8 + length]
        (crc,) = struct.unpack(">I", png[offset + 8 + length : offset + 12 + length])
        assert crc == zlib.crc32(kind + body)
        if kind == b"IHDR":
            width, height = struct.unpack(">II", body[:8])
        elif kind == b"IDAT":
            data += body
        offset += 12 + length
    return width, height, zlib.decompress(data)


def test_a_snap_is_a_valid_png_of_the_right_size():
    width, height, raw = decode(draw_snap("pacman"))
    assert (width, height) == (224, 168)
    assert len(raw) == height * (1 + width * 3)


def test_snaps_are_deterministic_and_differ_between_games():
    assert draw_snap("pacman") == draw_snap("pacman")
    assert draw_snap("pacman") != draw_snap("galaga")


def test_demo_game_names_are_valid_mame_names():
    assert all(NAME_RE.fullmatch(g.name) for g in DEMO_GAMES)
    assert len({g.name for g in DEMO_GAMES}) == len(DEMO_GAMES)


def test_the_demo_library_lists_games_with_and_without_a_picture(tmp_path):
    library = DemoGameLibrary(lambda: tmp_path / "roms", tmp_path / "retro")
    payload = library.payload()
    assert payload["mame"] is True
    assert payload["readable"] is True
    assert payload["state"] == "idle"
    assert len(payload["games"]) == len(DEMO_GAMES)
    snaps = {g["id"]: g["snap"] for g in payload["games"]}
    assert all(snaps[g.name] is (g.name not in WITHOUT_SNAP) for g in DEMO_GAMES)
    assert {g["status"] for g in payload["games"]} >= {"good", "imperfect", "preliminary"}


def test_the_demo_library_serves_pictures_only_for_its_games(tmp_path):
    library = DemoGameLibrary(lambda: tmp_path / "roms", tmp_path / "retro")
    path, kind = library.snap_file("pacman")
    assert kind == "image/png"
    assert path.read_bytes() == draw_snap("pacman")
    assert library.snap_file("defender") is None  # un jeu sans capture
    assert library.snap_file("../../etc/passwd") is None
    assert library.snap_file("inconnu") is None
    assert library.find("pacman").title == "Pac-Man (Midway)"


def test_the_demo_library_never_scans_or_launches_mame(tmp_path):
    library = DemoGameLibrary(lambda: tmp_path / "roms", tmp_path / "retro")
    assert library.is_stale() is False
    library.refresh(force=True)  # sans boucle d'évènements : aucune tâche n'est créée
    assert library.state == "idle"


# --- les manettes -------------------------------------------------------------------------------------------------------


@pytest.fixture
def pads():
    changes = []
    return DemoPads(
        lambda: changes.append(1), scan_seconds=5, find_after=0.05, step_seconds=0.02
    ), changes


def names(pads):
    return {d["id"]: d for d in pads.snapshot()["devices"]}


async def test_two_pads_are_known_at_first_one_connected_with_its_battery(pads):
    demo, _ = pads
    snapshot = demo.snapshot()
    assert snapshot["available"] is True
    assert snapshot["scanning"] is False
    devices = names(demo)
    assert set(devices) == {XBOX, DUALSENSE}
    assert devices[XBOX]["connected"] is True
    assert devices[XBOX]["battery"] == 78
    assert devices[DUALSENSE]["paired"] is True
    assert devices[DUALSENSE]["connected"] is False
    assert [d["id"] for d in demo.connected()] == [XBOX]
    assert snapshot["devices"][0]["id"] == XBOX  # la connectée d'abord


async def test_a_scan_finds_the_third_pad_then_pairing_connects_it(pads):
    import asyncio

    demo, changes = pads
    await demo.scan()
    assert demo.snapshot()["scanning"] is True
    assert demo.snapshot()["scan_until"] is not None
    await asyncio.sleep(0.2)
    found = names(demo)[EIGHTBITDO]
    assert found["paired"] is False
    await demo.pair(EIGHTBITDO)
    assert demo.snapshot()["scanning"] is False  # l'association arrête la recherche
    assert names(demo)[EIGHTBITDO]["connected"] is True
    assert demo.snapshot()["pairing"]["state"] == "done"
    assert len(changes) > 4
    await demo.stop()


async def test_the_scan_only_shows_unpaired_pads_while_it_runs(pads):
    import asyncio

    demo, _ = pads
    await demo.scan()
    await asyncio.sleep(0.2)
    await demo.stop_scan()
    assert EIGHTBITDO not in names(demo)  # trouvée mais pas associée : plus affichée hors recherche
    await demo.stop()


async def test_forgetting_a_pad_removes_it(pads):
    demo, _ = pads
    await demo.forget(DUALSENSE)
    assert set(names(demo)) == {XBOX}
    with pytest.raises(PadsError):
        await demo.forget(DUALSENSE)


@pytest.mark.parametrize("bad", [None, "", "zz", "AA:BB:CC:00:00:99", 3])
async def test_an_unknown_pad_is_refused(pads, bad):
    demo, _ = pads
    with pytest.raises(PadsError):
        await demo.pair(bad)
    with pytest.raises(PadsError):
        demo._known(bad)
