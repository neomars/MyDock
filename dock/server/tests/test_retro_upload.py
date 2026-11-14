"""L'envoi des jeux depuis le PC : la session à code, l'écriture d'un fichier, et la page du portail."""

import asyncio
import io
import logging
import os
import zipfile

import httpx
import pytest

from mydock_agent.testing import TOKEN
from mydock_dock import retro_upload
from mydock_dock.retro_upload import (
    MAX_WRONG,
    UPLOAD_CSP,
    PartialUploads,
    UploadRefused,
    UploadSessions,
    check_upload,
    clean_filename,
    megabytes,
    render_page,
    save_rom,
)
from mydock_dock.testing import eventually, running_dock

PEER = "192.168.1.20"


def make_zip(*names: str) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name in names or ("rom.bin",):
            archive.writestr(name, b"contenu " * 64)
    return buffer.getvalue()


SEVEN_ZIP = retro_upload.SEVENZIP_MAGIC + bytes(range(64))


async def stream(*chunks: bytes):
    for chunk in chunks:
        yield chunk


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def fixed_code(code: str = "ABCDEFGH"):
    letters = iter(code)
    return lambda _alphabet: next(letters)


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
def sessions(clock):
    changes: list[int] = []
    result = UploadSessions(
        on_change=lambda: changes.append(1), clock=clock, wall=lambda: 5000.0, choose=fixed_code()
    )
    result.changes = changes
    return result


# --- la session -------------------------------------------------------------------------------------------


def test_a_session_has_a_code_and_is_closed_before_it_starts(sessions):
    assert sessions.info() == {"open": False, "last": None}
    sessions.start()
    info = sessions.info()
    assert info["open"] is True
    assert info["code"].replace("-", "") == "ABCDEFGH"
    assert info["files"] == 0
    assert info["wrong_left"] == MAX_WRONG
    assert sessions.changes  # l'écran est prévenu


def test_the_right_code_gives_a_token_bound_to_the_pc(sessions):
    sessions.start()
    token = sessions.login(PEER, "abcd-efgh")
    sessions.authorize(PEER, token)
    with pytest.raises(UploadRefused) as refused:
        sessions.authorize("192.168.1.99", token)  # le jeton ne sert qu'à l'adresse qui l'a obtenu
    assert refused.value.code == "unauthorized"


def test_login_is_refused_while_no_session_is_open(sessions):
    with pytest.raises(UploadRefused) as refused:
        sessions.login(PEER, "ABCD-EFGH")
    assert refused.value.code == "not_open"
    assert refused.value.status == 409


@pytest.mark.parametrize(
    "presented", [None, 12, "", "ABCD", "ABCD-EFGX", "ABCDEFGH" * 2, ["ABCDEFGH"]]
)
def test_a_wrong_code_is_refused_and_counted(sessions, presented):
    sessions.start()
    with pytest.raises(UploadRefused) as refused:
        sessions.login(PEER, presented)
    assert refused.value.code == "bad_code"
    assert sessions.info()["wrong_left"] == MAX_WRONG - 1


def test_the_session_locks_after_too_many_wrong_codes(sessions):
    sessions.start()
    for _ in range(MAX_WRONG - 1):
        with pytest.raises(UploadRefused):
            sessions.login(PEER, "XXXXXXXX")
    with pytest.raises(UploadRefused) as refused:
        sessions.login(PEER, "XXXXXXXX")
    assert refused.value.code == "locked"
    assert sessions.info()["open"] is False
    assert sessions.info()["last"]["result"] == "locked"
    with pytest.raises(
        UploadRefused
    ) as after:  # même avec le bon code : il faut rouvrir sur le dock
        sessions.login(PEER, "ABCD-EFGH")
    assert after.value.code == "not_open"


def test_a_token_dies_with_its_session(sessions):
    sessions.start()
    token = sessions.login(PEER, "ABCDEFGH")
    sessions.stop()
    with pytest.raises(UploadRefused) as refused:
        sessions.authorize(PEER, token)
    assert refused.value.code == "not_open"


def test_reopening_changes_the_code_and_cancels_old_tokens(clock):
    codes = iter("ABCDEFGH" + "JKLMNPQR")
    sessions = UploadSessions(clock=clock, wall=lambda: 5000.0, choose=lambda _a: next(codes))
    sessions.start()
    token = sessions.login(PEER, "ABCDEFGH")
    sessions.start()
    assert sessions.info()["code"].replace("-", "") == "JKLMNPQR"
    with pytest.raises(UploadRefused):
        sessions.authorize(PEER, token)


def test_the_session_expires(sessions, clock):
    sessions.start()
    token = sessions.login(PEER, "ABCDEFGH")
    clock.now += retro_upload.SESSION_SECONDS + 1
    assert sessions.is_open() is False
    assert sessions.info()["last"]["result"] == "expired"
    with pytest.raises(UploadRefused):
        sessions.authorize(PEER, token)


def test_connection_attempts_are_throttled_per_address(sessions):
    sessions.start()
    refused = []
    for _ in range(retro_upload.THROTTLE_MAX + 3):
        try:
            sessions.login("10.0.0.5", "ABCDEFGH")
        except UploadRefused as refusal:
            refused.append(refusal.code)
            if refusal.code == "locked":
                sessions.start()
    assert "too_many" in refused
    sessions.login("10.0.0.6", "ABCDEFGH")  # une autre adresse n'est pas touchée


def test_the_code_never_appears_without_a_session_and_files_are_counted(sessions):
    assert "code" not in sessions.info()
    sessions.start()
    session = sessions.authorize(PEER, sessions.login(PEER, "ABCDEFGH"))
    sessions.reserve(session, 10)
    sessions.done(session, 10)
    assert sessions.info()["files"] == 1


def test_a_session_is_limited_in_files_and_bytes(sessions):
    sessions.start()
    session = sessions.authorize(PEER, sessions.login(PEER, "ABCDEFGH"))
    with pytest.raises(UploadRefused) as full:
        sessions.reserve(session, retro_upload.MAX_SESSION_BYTES + 1)
    assert full.value.code == "session_full"
    for _ in range(retro_upload.MAX_FILES):
        sessions.done(session, 1)
    with pytest.raises(UploadRefused) as many:
        sessions.reserve(session, 1)
    assert many.value.code == "too_many_files"


# --- la session du PC associé (sans code) ------------------------------------------------------------------------------


def test_the_associated_pc_gets_a_token_without_any_code_or_open_screen_session(sessions):
    token, seconds = sessions.trusted_session(PEER)
    assert 0 < seconds <= retro_upload.SESSION_SECONDS
    session = sessions.authorize(PEER, token)
    assert session.code is None
    assert sessions.info() == {
        "open": False,
        "last": None,
    }  # l'écran n'a rien à montrer : aucun code n'a été ouvert
    assert not sessions.changes


def test_a_trusted_token_serves_only_the_address_that_got_it(sessions):
    token, _ = sessions.trusted_session(PEER)
    with pytest.raises(UploadRefused) as refused:
        sessions.authorize("192.168.1.99", token)
    assert refused.value.code == "unauthorized"


def test_a_trusted_token_is_not_a_screen_token_and_the_reverse(sessions):
    sessions.start()
    screen = sessions.login(PEER, "ABCDEFGH")
    trusted, _ = sessions.trusted_session(PEER)
    assert sessions.authorize(PEER, screen).code is not None
    assert sessions.authorize(PEER, trusted).code is None
    sessions.stop()  # fermer la session de l'écran ne ferme pas celle du PC associé
    assert sessions.authorize(PEER, trusted).code is None
    with pytest.raises(UploadRefused):
        sessions.authorize(PEER, screen)


def test_reloading_the_page_keeps_the_same_quota(sessions):
    first, _ = sessions.trusted_session(PEER)
    session = sessions.authorize(PEER, first)
    sessions.done(session, 100)
    second, _ = sessions.trusted_session(PEER)
    assert second != first
    assert (
        sessions.authorize(PEER, second) is session
    )  # la même session : mêmes plafonds, rien remis à zéro
    assert session.total == 100


def test_a_trusted_session_expires(sessions, clock):
    token, _ = sessions.trusted_session(PEER)
    clock.now += retro_upload.SESSION_SECONDS + 1
    with pytest.raises(UploadRefused) as refused:
        sessions.authorize(PEER, token)
    assert refused.value.code == "not_open"
    fresh, _ = sessions.trusted_session(PEER)  # une nouvelle, avec de nouveaux plafonds
    assert sessions.authorize(PEER, fresh).total == 0


def test_forgetting_the_trusted_pc_cancels_its_tokens(sessions):
    token, _ = sessions.trusted_session(PEER)
    sessions.forget_trusted()
    with pytest.raises(UploadRefused):
        sessions.authorize(PEER, token)


def test_probing_is_throttled_like_the_other_attempts(sessions):
    refused = []
    for _ in range(retro_upload.THROTTLE_MAX + 2):
        try:
            sessions.trusted_session("10.0.0.5")
        except UploadRefused as refusal:
            refused.append(refusal.code)
    assert "too_many" in refused


def test_trusted_files_do_not_wake_the_screen(sessions):
    token, _ = sessions.trusted_session(PEER)
    session = sessions.authorize(PEER, token)
    sessions.done(session, 10)
    assert not sessions.changes  # l'écran du dock ne montre que la session à code


# --- le nom d'un fichier --------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("given", "expected"),
    [
        ("pacman.zip", ("pacman", "zip")),
        ("PacMan.ZIP", ("pacman", "zip")),
        (" galaga.7z ", ("galaga", "7z")),
        ("pacman.zip\n", ("pacman", "zip")),  # les blancs autour sont sans importance
        ("a" * 24 + ".zip", ("a" * 24, "zip")),
        ("1942.zip", ("1942", "zip")),
        ("dkong_j.zip", ("dkong_j", "zip")),
    ],
)
def test_a_game_name_is_clean(given, expected):
    assert clean_filename(given) == expected


@pytest.mark.parametrize(
    "bad",
    [
        None,
        42,
        "",
        ".zip",
        "pacman",
        "pacman.rar",
        "pacman.zip.exe",
        "pacman.exe",
        "../pacman.zip",
        "dir/pacman.zip",
        "dir\\pacman.zip",
        "pac man.zip",
        "pacman-2.zip",
        "a" * 25 + ".zip",
        "pacman.zip\x00.png",
        "pacmân.zip",
        "..zip",
    ],
)
def test_anything_that_is_not_a_game_name_is_refused(bad):
    with pytest.raises(UploadRefused) as refused:
        clean_filename(bad)
    assert refused.value.code == "bad_name"


# --- l'écriture -------------------------------------------------------------------------------------------------


async def test_a_zip_is_saved_where_mame_reads_it(tmp_path):
    roms = tmp_path / "roms"
    data = make_zip("a.bin", "b.bin")
    saved = await save_rom(stream(data[:100], data[100:]), "PacMan.zip", len(data), roms, reserve=0)
    assert (saved.name, saved.size) == ("pacman", len(data))
    assert (roms / "pacman.zip").read_bytes() == data
    assert (roms / "pacman.zip").stat().st_mode & 0o777 == 0o644
    assert [p.name for p in roms.iterdir()] == ["pacman.zip"]  # pas de fichier provisoire


async def test_a_7z_is_saved(tmp_path):
    saved = await save_rom(stream(SEVEN_ZIP), "galaga.7z", len(SEVEN_ZIP), tmp_path, reserve=0)
    assert saved.name == "galaga"
    assert (tmp_path / "galaga.7z").read_bytes() == SEVEN_ZIP


async def test_sending_again_replaces_the_game_and_its_other_format(tmp_path):
    (tmp_path / "pacman.7z").write_bytes(SEVEN_ZIP)
    (tmp_path / "pacman.zip").write_bytes(b"vieux")
    data = make_zip()
    await save_rom(stream(data), "pacman.zip", len(data), tmp_path, reserve=0)
    assert (tmp_path / "pacman.zip").read_bytes() == data
    assert not (tmp_path / "pacman.7z").exists()


async def test_a_refused_file_leaves_the_existing_game_and_no_trace(tmp_path):
    good = make_zip()
    (tmp_path / "pacman.zip").write_bytes(good)
    with pytest.raises(UploadRefused):
        await save_rom(stream(b"pas une archive"), "pacman.zip", 15, tmp_path, reserve=0)
    assert (tmp_path / "pacman.zip").read_bytes() == good
    assert [p.name for p in tmp_path.iterdir()] == ["pacman.zip"]


@pytest.mark.parametrize(
    ("filename", "payload", "declared", "code"),
    [
        ("pacman.zip", b"x" * 50, 50, "not_archive"),  # ni PK ni 7z
        ("pacman.zip", SEVEN_ZIP, len(SEVEN_ZIP), "not_archive"),  # une archive 7z sous un nom .zip
        ("pacman.7z", make_zip(), None, "length_required"),
        ("pacman.7z", make_zip(), 0, "length_required"),
        ("pacman.zip", make_zip()[:-30], None, "length_required"),
        ("pacman.exe", make_zip(), 10, "bad_name"),
        ("../x.zip", make_zip(), 10, "bad_name"),
    ],
)
async def test_bad_files_are_refused_and_nothing_is_written(
    tmp_path, filename, payload, declared, code
):
    with pytest.raises(UploadRefused) as refused:
        await save_rom(stream(payload), filename, declared, tmp_path, reserve=0)
    assert refused.value.code == code
    assert list(tmp_path.iterdir()) == []


async def test_a_truncated_zip_is_refused(tmp_path):
    data = make_zip("a.bin")
    cut = data[: len(data) // 2]
    with pytest.raises(UploadRefused) as refused:
        await save_rom(stream(cut), "pacman.zip", len(cut), tmp_path, reserve=0)
    assert refused.value.code == "not_archive"
    assert list(tmp_path.iterdir()) == []


async def test_a_file_that_arrives_incomplete_is_refused(tmp_path):
    data = make_zip()
    with pytest.raises(UploadRefused) as refused:
        await save_rom(stream(data[:50]), "pacman.zip", len(data), tmp_path, reserve=0)
    assert refused.value.code == "incomplete"
    assert list(tmp_path.iterdir()) == []


async def test_a_file_bigger_than_announced_is_refused(tmp_path):
    data = make_zip()
    with pytest.raises(UploadRefused) as refused:
        await save_rom(stream(data, b"en trop"), "pacman.zip", len(data), tmp_path, reserve=0)
    assert refused.value.code == "bad_length"
    assert list(tmp_path.iterdir()) == []


async def test_a_file_over_the_limit_is_refused_before_any_byte_is_read(tmp_path):
    read = []

    async def never():
        read.append(1)
        yield b"x"

    with pytest.raises(UploadRefused) as refused:
        await save_rom(never(), "pacman.zip", 2_000, tmp_path, max_bytes=1_000, reserve=0)
    assert refused.value.code == "too_large"
    assert refused.value.status == 413
    assert not read
    assert list(tmp_path.iterdir()) == []


async def test_not_enough_free_space_is_refused(tmp_path):
    data = make_zip()
    with pytest.raises(UploadRefused) as refused:
        await save_rom(stream(data), "pacman.zip", len(data), tmp_path, reserve=10**15)
    assert refused.value.code == "no_space"
    assert refused.value.status == 507
    assert list(tmp_path.iterdir()) == []


async def test_the_space_refusal_says_how_much_is_free_and_how_much_is_needed(tmp_path):
    """« Pas assez de place » seul ne dit pas si c'est le fichier ou la carte : le message donne les chiffres."""
    with pytest.raises(UploadRefused) as refused:
        await check_upload("pacman.zip", 3 * 1024 * 1024, tmp_path, reserve=10**15)
    message = refused.value.message
    assert refused.value.code == "no_space"
    assert "libres" in message
    assert "3,0 Mo pour ce jeu" in message
    assert "Mo que le dock garde libres" in message


async def test_a_file_fits_when_the_free_space_covers_it_and_the_reserve(tmp_path):
    assert await check_upload("Pacman.ZIP", 1024, tmp_path, reserve=0) == ("pacman", "zip")
    assert tmp_path.is_dir()
    assert list(tmp_path.iterdir()) == []  # vérifier n'écrit rien


@pytest.mark.parametrize(
    ("declared", "code", "status"),
    [(None, "length_required", 411), (0, "length_required", 411), (2_000, "too_large", 413)],
)
async def test_the_check_refuses_from_the_request_alone(tmp_path, declared, code, status):
    with pytest.raises(UploadRefused) as refused:
        await check_upload("pacman.zip", declared, tmp_path, max_bytes=1_000, reserve=0)
    assert (refused.value.code, refused.value.status) == (code, status)


def test_sizes_are_written_the_french_way():
    assert megabytes(3_250_000) == "3,1 Mo"
    assert megabytes(500 * 1024 * 1024) == "500,0 Mo"
    assert megabytes(-5) == "0,0 Mo"


async def test_a_stalled_upload_is_abandoned(tmp_path, monkeypatch):
    monkeypatch.setattr(retro_upload, "CHUNK_TIMEOUT", 0.1)

    async def stalled():
        yield make_zip()[:20]
        import asyncio

        await asyncio.sleep(5)
        yield b""

    with pytest.raises(UploadRefused) as refused:
        await save_rom(stalled(), "pacman.zip", 1_000, tmp_path, reserve=0)
    assert refused.value.code == "stalled"
    assert list(tmp_path.iterdir()) == []


async def test_a_folder_that_cannot_be_written_is_reported(tmp_path):
    blocker = tmp_path / "pas-un-dossier"
    blocker.write_bytes(b"")
    data = make_zip()
    with pytest.raises(UploadRefused) as refused:
        await save_rom(stream(data), "pacman.zip", len(data), blocker / "roms", reserve=0)
    assert refused.value.code == "not_writable"


async def test_the_provisional_file_is_invisible_to_the_game_list(tmp_path):
    """Un envoi en cours ne ressemble pas à un jeu : ni `.zip`, ni `.7z`."""
    seen: list[str] = []

    async def slow():
        data = make_zip()
        yield data[:10]
        seen.extend(p.name for p in tmp_path.iterdir())
        yield data[10:]

    data = make_zip()
    await save_rom(slow(), "pacman.zip", len(data), tmp_path, reserve=0)
    assert seen
    assert all(not name.endswith((".zip", ".7z")) for name in seen)


# --- la page ----------------------------------------------------------------------------------------------------


def test_the_page_is_self_contained_and_its_csp_matches_it():
    page = render_page("mydock")
    assert "<script src" not in page
    assert "http://" not in page
    assert "https://" not in page
    assert retro_upload._csp_hash(retro_upload.STYLE) in UPLOAD_CSP
    assert retro_upload._csp_hash(retro_upload.SCRIPT) in UPLOAD_CSP
    assert f"<script>{retro_upload.SCRIPT}</script>" in page
    assert f"<style>{retro_upload.STYLE}</style>" in page
    assert "default-src 'none'" in UPLOAD_CSP
    assert "frame-ancestors 'none'" in UPLOAD_CSP
    assert "unsafe" not in UPLOAD_CSP


def test_the_page_never_echoes_a_hostile_hostname():
    page = render_page("<script>alert(1)</script>")
    assert "<script>alert(1)</script>" not in page
    assert "&lt;script&gt;" in page


# --- le portail, serveur réel -----------------------------------------------------------------------------------


@pytest.fixture
async def dock(tmp_path):
    roms = tmp_path / "roms"
    async with running_dock(tmp_path, portal=True) as running:
        running.config.set_retrogame(roms_dir=str(roms))
        running.roms = roms
        yield running


def web(dock) -> httpx.AsyncClient:
    return httpx.AsyncClient(trust_env=False, base_url=dock.portal_origin, timeout=10)


async def login(client: httpx.AsyncClient, dock) -> str:
    dock.server.uploads.start()
    code = dock.server.uploads.info()["code"]
    reply = await client.post("/api/retro/login", json={"code": code})
    assert reply.status_code == 200, reply.text
    return reply.json()["token"]


async def test_the_upload_page_is_served_with_its_own_csp(dock):
    async with web(dock) as client:
        reply = await client.get("/jeux")
        head = await client.head("/jeux")
    assert reply.status_code == 200
    assert reply.headers["content-type"].startswith("text/html")
    assert reply.headers["content-security-policy"] == UPLOAD_CSP
    assert reply.headers["cache-control"] == "no-store"
    assert reply.headers["x-content-type-options"] == "nosniff"
    assert "Retrogame" in reply.text
    assert head.status_code == 200


async def test_the_page_does_not_contain_the_code(dock):
    dock.server.uploads.start()
    code = dock.server.uploads.info()["code"]
    async with web(dock) as client:
        body = (await client.get("/jeux")).text
        state = (await client.get("/api/portal")).text
    assert code not in body
    assert code.replace("-", "") not in body.replace("-", "")
    assert code.replace("-", "") not in state.replace("-", "")


async def test_a_game_goes_from_the_browser_to_the_rom_folder(dock):
    data = make_zip("pong.bin")
    async with web(dock) as client:
        token = await login(client, dock)
        reply = await client.post(
            "/api/retro/upload?name=Pong.zip", content=data, headers={"X-Upload-Token": token}
        )
    assert reply.status_code == 200, reply.text
    assert reply.json() == {"ok": True, "name": "pong", "size": len(data)}
    assert (dock.roms / "pong.zip").read_bytes() == data
    assert dock.server.uploads.info()["files"] == 1
    assert reply.headers["cache-control"] == "no-store"


async def test_the_game_list_is_refreshed_after_an_upload(dock, monkeypatch):
    calls = []
    monkeypatch.setattr(dock.server.retro, "refresh", lambda *a, **k: calls.append((a, k)))
    data = make_zip()
    async with web(dock) as client:
        token = await login(client, dock)
        await client.post(
            "/api/retro/upload?name=pong.zip", content=data, headers={"X-Upload-Token": token}
        )
    assert calls


async def test_login_with_a_wrong_code_is_refused(dock):
    dock.server.uploads.start()
    async with web(dock) as client:
        reply = await client.post("/api/retro/login", json={"code": "XXXX-XXXX"})
    assert reply.status_code == 403
    assert reply.json()["error"] == "bad_code"
    assert "token" not in reply.text


async def test_login_without_an_open_session_is_refused(dock):
    async with web(dock) as client:
        reply = await client.post("/api/retro/login", json={"code": "ABCD-EFGH"})
    assert reply.status_code == 409
    assert reply.json()["error"] == "not_open"


@pytest.mark.parametrize(
    ("content", "content_type", "status"),
    [
        (b"code=ABCD", "application/x-www-form-urlencoded", 415),
        (b"{pas du json", "application/json", 400),
        (b"[1, 2]", "application/json", 400),
        (b'{"code": "' + b"A" * 10_000 + b'"}', "application/json", 413),
    ],
)
async def test_login_bodies_are_checked(dock, content, content_type, status):
    dock.server.uploads.start()
    async with web(dock) as client:
        reply = await client.post(
            "/api/retro/login", content=content, headers={"Content-Type": content_type}
        )
    assert reply.status_code == status


async def test_upload_needs_the_token(dock):
    data = make_zip()
    async with web(dock) as client:
        await login(client, dock)
        for headers in ({}, {"X-Upload-Token": "n-importe-quoi"}):
            reply = await client.post(
                "/api/retro/upload?name=pong.zip", content=data, headers=headers
            )
            assert reply.status_code == 401
            assert reply.json()["error"] == "unauthorized"
    assert not dock.roms.exists() or list(dock.roms.iterdir()) == []


async def test_upload_without_a_session_is_refused(dock):
    async with web(dock) as client:
        reply = await client.post(
            "/api/retro/upload?name=pong.zip", content=make_zip(), headers={"X-Upload-Token": "x"}
        )
    assert reply.status_code == 409


async def test_upload_refuses_bad_names_and_leaves_the_folder_clean(dock):
    data = make_zip()
    async with web(dock) as client:
        token = await login(client, dock)
        for name in ("../../etc/passwd", "pong.exe", "pong", "A%00.zip", ""):
            reply = await client.post(
                "/api/retro/upload",
                params={"name": name},
                content=data,
                headers={"X-Upload-Token": token},
            )
            assert reply.status_code == 400, name
            assert reply.json()["error"] == "bad_name"
        missing = await client.post(
            "/api/retro/upload", content=data, headers={"X-Upload-Token": token}
        )
        assert missing.status_code == 400
    assert not dock.roms.exists() or list(dock.roms.iterdir()) == []


async def test_upload_refuses_something_that_is_not_an_archive(dock):
    async with web(dock) as client:
        token = await login(client, dock)
        reply = await client.post(
            "/api/retro/upload?name=pong.zip",
            content=b"#!/bin/sh\nrm -rf /\n",
            headers={"X-Upload-Token": token},
        )
    assert reply.status_code == 415
    assert reply.json()["error"] == "not_archive"
    assert list(dock.roms.iterdir()) == []


async def test_upload_refuses_a_file_over_the_limit_without_reading_it(dock, monkeypatch):
    monkeypatch.setattr(retro_upload, "MAX_FILE_BYTES", 100)
    monkeypatch.setattr(retro_upload, "FREE_RESERVE", 0)
    async with web(dock) as client:
        token = await login(client, dock)
        reply = await client.post(
            "/api/retro/upload?name=pong.zip",
            content=b"x" * 5_000,
            headers={"X-Upload-Token": token},
        )
    assert reply.status_code == 413
    assert reply.json()["error"] == "too_large"


async def test_a_refusal_closes_the_connection(dock):
    async with web(dock) as client:
        token = await login(client, dock)
        reply = await client.post(
            "/api/retro/upload?name=pong.exe", content=make_zip(), headers={"X-Upload-Token": token}
        )
    assert reply.headers["connection"] == "close"


async def test_the_control_interface_has_no_upload_address(dock):
    """L'envoi n'existe que sur le portail : le port de l'interface du dock ne le propose pas."""
    async with httpx.AsyncClient(trust_env=False, base_url=dock.origin, timeout=10) as client:
        for path in ("/jeux", "/api/retro/login", "/api/retro/check", "/api/retro/upload"):
            for method in ("GET", "POST"):
                reply = await client.request(method, path)
                assert reply.status_code in (404, 405, 200), (method, path, reply.status_code)
                assert reply.status_code != 200 or "text/html" in reply.headers["content-type"]
    assert dock.server.uploads.info()["open"] is False


async def test_the_dock_screen_learns_about_the_session(dock):
    pushed = []
    dock.server.uploads._on_change = lambda: pushed.append(1)
    dock.server.uploads.start()
    await eventually(lambda: bool(pushed))


async def test_a_big_file_streams_through_the_real_portal_in_pieces(dock):
    """40 Mo (un jeu moyen) envoyés par morceaux de 256 Ko à travers le vrai portail : le fichier arrive entier."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_STORED) as archive:
        archive.writestr("gros.bin", bytes(range(256)) * (40 * 1024 * 4))
    data = buffer.getvalue()
    assert len(data) > 40 * 1024 * 1024

    async def pieces():
        for start in range(0, len(data), 256 * 1024):
            yield data[start : start + 256 * 1024]

    async with web(dock) as client:
        token = await login(client, dock)
        reply = await client.post(
            "/api/retro/upload?name=gros.zip",
            content=pieces(),
            headers={"X-Upload-Token": token, "Content-Length": str(len(data))},
            timeout=60,
        )
    assert reply.status_code == 200, reply.text
    assert reply.json()["size"] == len(data)
    assert (dock.roms / "gros.zip").stat().st_size == len(data)
    assert [p.name for p in dock.roms.iterdir()] == ["gros.zip"]


async def check(client, token, **body):
    return await client.post(
        "/api/retro/check", json=body, headers={"X-Upload-Token": token} if token else {}
    )


async def test_the_check_says_yes_without_any_byte_being_sent(dock):
    async with web(dock) as client:
        token = await login(client, dock)
        reply = await check(client, token, name="Pong.zip", size=14 * 1024 * 1024)
    assert reply.status_code == 200, reply.text
    assert reply.json() == {"ok": True}
    assert reply.headers["cache-control"] == "no-store"
    assert list(dock.roms.iterdir()) == []
    assert dock.server.uploads.info()["files"] == 0  # vérifier ne compte pas comme un envoi


async def test_a_big_file_that_will_be_refused_is_told_why_as_a_normal_answer(dock, monkeypatch):
    """Le cas d'origine : 14 Mo refusés faute de place. Envoyés, ils coupaient la connexion (le navigateur ne voyait
    pas pourquoi) ; vérifiés d'abord, c'est une réponse comme une autre, avec le message."""
    monkeypatch.setattr(retro_upload, "FREE_RESERVE", 10**15)
    async with web(dock) as client:
        token = await login(client, dock)
        reply = await check(client, token, name="tetrisp2.zip", size=14 * 1024 * 1024)
    assert reply.status_code == 507
    body = reply.json()
    assert body["error"] == "no_space"
    assert "libres" in body["message"]
    assert "14,0 Mo pour ce jeu" in body["message"]


@pytest.mark.parametrize(
    ("body", "status", "error"),
    [
        ({"name": "pong.exe", "size": 100}, 400, "bad_name"),
        ({"name": "../pong.zip", "size": 100}, 400, "bad_name"),
        ({"name": "pong.zip"}, 411, "length_required"),
        ({"name": "pong.zip", "size": 0}, 411, "length_required"),
        ({"name": "pong.zip", "size": "100"}, 411, "length_required"),
        ({"name": "pong.zip", "size": True}, 411, "length_required"),
        ({"name": "pong.zip", "size": 500 * 1024 * 1024}, 413, "too_large"),
        ({"name": "pong.zip", "size": 10**12}, 413, "session_full"),
        ({"name": 5, "size": 100}, 400, "bad_name"),
    ],
)
async def test_the_check_refuses_what_the_upload_would_refuse(dock, body, status, error):
    async with web(dock) as client:
        token = await login(client, dock)
        reply = await check(client, token, **body)
    assert (reply.status_code, reply.json()["error"]) == (status, error)
    assert not dock.roms.exists() or list(dock.roms.iterdir()) == []


async def test_the_check_needs_the_token_like_the_upload(dock):
    async with web(dock) as client:
        token = await login(client, dock)
        assert (await check(client, None, name="pong.zip", size=100)).status_code == 401
        assert (await check(client, "faux", name="pong.zip", size=100)).status_code == 401
        assert (await check(client, token, name="pong.zip", size=100)).status_code == 200
        dock.server.uploads.stop()
        assert (await check(client, token, name="pong.zip", size=100)).status_code == 409


async def test_the_check_knows_a_session_is_full(dock, monkeypatch):
    monkeypatch.setattr(retro_upload, "MAX_SESSION_BYTES", 1_000)
    async with web(dock) as client:
        token = await login(client, dock)
        reply = await check(client, token, name="pong.zip", size=2_000)
    assert (reply.status_code, reply.json()["error"]) == (413, "session_full")


async def test_the_check_answers_only_json_within_the_size_limit(dock):
    async with web(dock) as client:
        token = await login(client, dock)
        headers = {"X-Upload-Token": token}
        wrong_type = await client.post(
            "/api/retro/check", content=b"{}", headers={**headers, "Content-Type": "text/plain"}
        )
        too_big = await client.post(
            "/api/retro/check",
            content=b"[" + b"1," * 5_000 + b"1]",
            headers={**headers, "Content-Type": "application/json"},
        )
        garbage = await client.post(
            "/api/retro/check",
            content=b"pas du json",
            headers={**headers, "Content-Type": "application/json"},
        )
    assert wrong_type.status_code == 415
    assert too_big.status_code == 413
    assert garbage.status_code == 400


async def test_the_page_checks_before_sending_and_explains_a_cut_connection():
    """Le script de la page : vérification d'abord, et si l'envoi lâche, on redemande pourquoi avant de parler de réseau."""
    assert "/api/retro/check" in retro_upload.SCRIPT
    assert retro_upload.SCRIPT.index("ask(job)") < retro_upload.SCRIPT.index("send(job)")
    assert "xhr.onerror" in retro_upload.SCRIPT
    assert "réseau ou Wi-Fi" in retro_upload.SCRIPT


# --- l'envoi par morceaux, serveur réel ----------------------------------------------------------------------------------


CHUNK = 131072  # la page envoie des morceaux de 128 Ko


def chunk_url(name: str, total: int, offset: int) -> str:
    return f"/api/retro/upload?name={name}&total={total}&offset={offset}"


async def put_chunk(client, token, name, data, offset, *, size=CHUNK):
    return await client.post(
        chunk_url(name, len(data), offset),
        content=data[offset : offset + size],
        headers={"X-Upload-Token": token},
    )


async def test_a_3_mb_game_goes_through_the_real_portal_in_chunks_of_128_kb(dock):
    """Le cas du Pi de l'utilisateur : un jeu de 3 Mo, qui ne passait pas d'un seul tenant."""
    data = big_zip(3 * 1024)
    assert len(data) > 3 * 1024 * 1024
    async with web(dock) as client:
        token = await login(client, dock)
        offset, answers = 0, []
        while True:
            reply = await put_chunk(client, token, "tetris.zip", data, offset)
            assert reply.status_code == 200, reply.text
            answers.append(reply.json())
            offset = reply.json()["received"]
            if reply.json()["done"]:
                break
    assert len(answers) > 20
    assert all(a == {"ok": True, "received": a["received"], "done": False} for a in answers[:-1])
    assert answers[-1] == {
        "ok": True,
        "name": "tetris",
        "size": len(data),
        "received": len(data),
        "done": True,
    }
    assert (dock.roms / "tetris.zip").read_bytes() == data
    assert [p.name for p in dock.roms.iterdir()] == ["tetris.zip"]
    assert dock.server.uploads.info()["files"] == 1  # un jeu, pas vingt-cinq morceaux


async def test_a_lost_answer_is_resumed_through_the_real_portal(dock):
    data = big_zip(1024)
    async with web(dock) as client:
        token = await login(client, dock)
        first = await put_chunk(client, token, "pong.zip", data, 0)
        second = await put_chunk(
            client, token, "pong.zip", data, CHUNK
        )  # écrit, mais la page n'en a pas la réponse
        assert second.json()["received"] == 2 * CHUNK
        again = await put_chunk(client, token, "pong.zip", data, CHUNK)
        assert again.status_code == 409
        assert again.json()["error"] == "offset"
        assert again.json()["received"] == 2 * CHUNK
        assert first.json()["received"] == CHUNK
        offset = again.json()["received"]
        while True:
            reply = await put_chunk(client, token, "pong.zip", data, offset)
            offset = reply.json()["received"]
            if reply.json()["done"]:
                break
    assert (dock.roms / "pong.zip").read_bytes() == data


async def test_a_refused_chunk_is_answered_in_full_even_with_a_128_kb_body(dock):
    """Le but : un refus qui arrive (nom, session…) arrive au navigateur, parce que le corps du morceau est lu en entier."""
    async with web(dock) as client:
        token = await login(client, dock)
        reply = await client.post(
            chunk_url("pong.exe", 10_000_000, 0),
            content=b"x" * CHUNK,
            headers={"X-Upload-Token": token},
        )
    assert reply.status_code == 400
    assert reply.json()["error"] == "bad_name"


@pytest.mark.parametrize(
    ("query", "status", "error"),
    [
        ("name=pong.zip&total=1000&offset=abc", 400, "bad_request"),
        ("name=pong.zip&total=abc&offset=0", 400, "bad_request"),
        ("name=pong.zip&offset=0", 400, "bad_request"),
        ("name=pong.zip&total=1000&offset=-5", 400, "bad_request"),
        ("name=pong.zip&total=1000&offset=500", 409, "no_upload"),
    ],
)
async def test_chunk_parameters_are_checked(dock, query, status, error):
    async with web(dock) as client:
        token = await login(client, dock)
        reply = await client.post(
            f"/api/retro/upload?{query}", content=b"x" * 10, headers={"X-Upload-Token": token}
        )
    assert (reply.status_code, reply.json()["error"]) == (status, error)
    assert not dock.roms.exists() or not any(dock.roms.iterdir())


async def test_a_chunk_needs_the_token_too(dock):
    async with web(dock) as client:
        await login(client, dock)
        reply = await client.post(chunk_url("pong.zip", 1000, 0), content=b"x" * 10)
        wrong = await client.post(
            chunk_url("pong.zip", 1000, 0), content=b"x", headers={"X-Upload-Token": "faux"}
        )
    assert reply.status_code == 401
    assert wrong.status_code == 401


async def test_a_session_limit_stops_a_game_at_its_first_chunk(dock, monkeypatch):
    monkeypatch.setattr(retro_upload, "MAX_SESSION_BYTES", 1_000)
    async with web(dock) as client:
        token = await login(client, dock)
        reply = await client.post(
            chunk_url("pong.zip", 2_000, 0), content=b"x" * 100, headers={"X-Upload-Token": token}
        )
    assert (reply.status_code, reply.json()["error"]) == (413, "session_full")


async def test_a_lack_of_space_is_told_at_the_first_chunk(dock, monkeypatch):
    monkeypatch.setattr(retro_upload, "FREE_RESERVE", 10**15)
    async with web(dock) as client:
        token = await login(client, dock)
        reply = await put_chunk(client, token, "tetris.zip", big_zip(), 0)
    assert reply.status_code == 507
    assert "libres" in reply.json()["message"]


async def test_a_connection_cut_in_the_middle_is_logged_and_the_chunk_can_be_sent_again(
    dock, caplog
):
    caplog.set_level(logging.WARNING, logger="mydock_dock.media")
    data = big_zip()
    host, port = dock.portal_origin.removeprefix("http://").split(":")
    async with web(dock) as client:
        token = await login(client, dock)
        _reader, writer = await asyncio.open_connection(host, int(port))
        writer.write(
            (
                f"POST {chunk_url('pong.zip', len(data), 0)} HTTP/1.1\r\nHost: x\r\n"
                f"X-Upload-Token: {token}\r\nContent-Length: {CHUNK}\r\n\r\n"
            ).encode()
            + data[:1000]
        )
        await writer.drain()
        writer.close()  # le navigateur est parti en plein morceau
        await eventually(
            lambda: any("coupé par le navigateur" in r.getMessage() for r in caplog.records)
        )
        message = next(
            r.getMessage() for r in caplog.records if "coupé par le navigateur" in r.getMessage()
        )
        assert "pong.zip" in message
        assert "morceau à l'octet 0" in message
        offset = 0  # ... et le même morceau, renvoyé, repart de zéro
        while True:
            reply = await put_chunk(client, token, "pong.zip", data, offset)
            assert reply.status_code == 200, reply.text
            offset = reply.json()["received"]
            if reply.json()["done"]:
                break
    assert (dock.roms / "pong.zip").read_bytes() == data


async def test_an_unexpected_error_is_logged_and_answered_with_a_message(dock, monkeypatch, caplog):
    async def broken(*_args, **_kwargs):
        raise RuntimeError("disque en panne")

    monkeypatch.setattr(PartialUploads, "write", broken)
    caplog.set_level(logging.ERROR, logger="mydock_dock.media")
    async with web(dock) as client:
        token = await login(client, dock)
        reply = await put_chunk(client, token, "pong.zip", big_zip(), 0)
    assert reply.status_code == 500
    assert reply.json()["error"] == "server_error"
    assert any("disque en panne" in (r.exc_text or "") for r in caplog.records)


# --- le PC associé, serveur réel -----------------------------------------------------------------------------------------


async def associate(dock, host="127.0.0.1"):
    """Le PC qui joint le portail (ici 127.0.0.1) est le PC associé au dock."""
    dock.config.agent_host = host
    dock.config.token = TOKEN


async def test_a_dock_without_an_associated_pc_asks_for_the_code(dock):
    async with web(dock) as client:
        reply = await client.post("/api/retro/me", json={})
    assert reply.status_code == 200  # pas une erreur : la page affiche alors le champ du code
    assert reply.json() == {"ok": True, "trusted": False}


async def test_probing_never_spends_a_wrong_code_of_the_screen_session(dock):
    dock.server.uploads.start()
    async with web(dock) as client:
        for _ in range(MAX_WRONG + 2):
            await client.post("/api/retro/me", json={})
    assert dock.server.uploads.info()["wrong_left"] == MAX_WRONG
    assert dock.server.uploads.info()["open"] is True


async def test_the_associated_pc_sends_a_game_without_any_code(dock):
    await associate(dock)
    data = make_zip("pong.bin")
    async with web(dock) as client:
        me = await client.post("/api/retro/me", json={})
        assert me.json()["trusted"] is True
        token = me.json()["token"]
        reply = await client.post(
            "/api/retro/upload?name=Pong.zip", content=data, headers={"X-Upload-Token": token}
        )
    assert reply.status_code == 200, reply.text
    assert (dock.roms / "pong.zip").read_bytes() == data
    assert dock.server.uploads.info()["open"] is False  # rien n'a été demandé sur l'écran du dock


async def test_another_device_is_not_trusted_even_with_a_paired_dock(dock):
    await associate(dock, host="192.0.2.50")  # le PC associé est une autre machine
    async with web(dock) as client:
        reply = await client.post("/api/retro/me", json={})
    assert reply.json() == {"ok": True, "trusted": False}


async def test_the_associated_pc_can_be_known_by_name(dock):
    await associate(dock, host="localhost")
    async with web(dock) as client:
        reply = await client.post("/api/retro/me", json={})
    assert reply.json()["trusted"] is True


async def test_turning_the_setting_off_asks_everyone_for_the_code(dock):
    await associate(dock)
    dock.config.upload_without_code = False
    async with web(dock) as client:
        reply = await client.post("/api/retro/me", json={})
    assert reply.json() == {"ok": True, "trusted": False}


async def test_a_trusted_token_stops_working_the_moment_the_setting_is_turned_off(dock):
    await associate(dock)
    async with web(dock) as client:
        token = (await client.post("/api/retro/me", json={})).json()["token"]
        dock.config.upload_without_code = False
        reply = await client.post(
            "/api/retro/upload?name=pong.zip", content=make_zip(), headers={"X-Upload-Token": token}
        )
    assert reply.status_code == 401
    assert reply.json()["error"] == "unauthorized"
    assert not dock.roms.exists() or list(dock.roms.iterdir()) == []


async def test_a_trusted_token_stops_working_when_another_pc_is_associated(dock):
    await associate(dock)
    async with web(dock) as client:
        token = (await client.post("/api/retro/me", json={})).json()["token"]
        dock.config.agent_host = "192.0.2.50"
        reply = await client.post(
            "/api/retro/upload?name=pong.zip", content=make_zip(), headers={"X-Upload-Token": token}
        )
    assert reply.status_code == 401


async def test_the_screen_code_still_works_for_other_devices_on_a_paired_dock(dock):
    await associate(dock, host="192.0.2.50")
    async with web(dock) as client:
        token = await login(client, dock)
        reply = await client.post(
            "/api/retro/upload?name=pong.zip", content=make_zip(), headers={"X-Upload-Token": token}
        )
    assert reply.status_code == 200, reply.text


async def test_the_probe_answers_only_json_requests_within_the_size_limit(dock):
    await associate(dock)
    async with web(dock) as client:
        big = await client.post("/api/retro/me", content=b"x" * 10_000)
    assert big.status_code == 413


@pytest.mark.parametrize(
    ("peer", "agent", "expected"),
    [
        ("192.168.1.20", "192.168.1.20", True),
        ("::ffff:192.168.1.20", "192.168.1.20", True),
        ("192.168.1.21", "192.168.1.20", False),
        ("?", "192.168.1.20", False),
        ("192.168.1.20", "", False),
    ],
)
async def test_the_associated_pc_is_recognised_by_its_address(peer, agent, expected):
    from types import SimpleNamespace

    from mydock_dock.portal import is_associated_pc

    config = SimpleNamespace(
        upload_without_code=True, agent_host=agent, token="t" * 40, agent_port=8765
    )
    assert await is_associated_pc(SimpleNamespace(config=config), peer) is expected


async def test_the_portal_home_links_to_the_upload_page(dock):
    async with web(dock) as client:
        page = (await client.get("/")).text
        jeux = await client.get("/jeux")
    assert 'href="/jeux"' in page
    assert jeux.status_code == 200


# --- l'envoi par morceaux ------------------------------------------------------------------------------------------------


def big_zip(kilobytes: int = 300) -> bytes:
    """Une vraie archive ZIP d'au moins `kilobytes` Ko (un fichier sans compression)."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_STORED) as archive:
        archive.writestr("rom.bin", bytes(range(256)) * (kilobytes * 4))
    return buffer.getvalue()


def pieces(data: bytes, size: int):
    return [data[start : start + size] for start in range(0, len(data), size)]


def trusted_session(clock=None):
    sessions = UploadSessions(clock=clock or Clock())
    token, _ = sessions.trusted_session(PEER)
    return sessions.authorize(PEER, token)


async def send_all(partials, session, name, data, roms, *, size=65536, **options):
    """Envoie `data` par morceaux de `size` octets, comme la page ; renvoie le dernier résultat."""
    offset, result = 0, (None, 0)
    for piece in pieces(data, size):
        result = await partials.write(
            session, name, len(data), offset, stream(piece), roms, **options
        )
        offset = result[1]
    return result


def temp_files(folder):
    return sorted(p.name for p in folder.iterdir() if p.name.startswith(".envoi-"))


async def test_a_game_arrives_in_chunks_and_takes_its_name_only_at_the_end(tmp_path):
    data, partials, session = big_zip(), PartialUploads(), trusted_session()
    first = await partials.write(
        session, "Pacman.zip", len(data), 0, stream(data[:65536]), tmp_path, reserve=0
    )
    assert first == (None, 65536)
    assert len(temp_files(tmp_path)) == 1  # un fichier provisoire, jamais un jeu à moitié écrit
    assert not (tmp_path / "pacman.zip").exists()
    saved, received = await send_all(partials, session, "pacman.zip", data, tmp_path, reserve=0)
    assert (saved.name, saved.size, received) == ("pacman", len(data), len(data))
    assert (tmp_path / "pacman.zip").read_bytes() == data
    assert temp_files(tmp_path) == []
    assert len(partials) == 0
    assert (tmp_path / "pacman.zip").stat().st_mode & 0o777 == 0o644


async def test_a_lost_answer_is_resumed_where_the_dock_really_is(tmp_path):
    """Le morceau est écrit, mais sa réponse s'est perdue : la page le renvoie, et le dock lui dit où reprendre."""
    data, partials, session = big_zip(), PartialUploads(), trusted_session()
    parts = pieces(data, 65536)
    await partials.write(session, "pong.zip", len(data), 0, stream(parts[0]), tmp_path, reserve=0)
    await partials.write(
        session, "pong.zip", len(data), 65536, stream(parts[1]), tmp_path, reserve=0
    )
    with pytest.raises(
        UploadRefused
    ) as refused:  # la page renvoie le morceau 1, dont elle n'a pas eu la réponse
        await partials.write(
            session, "pong.zip", len(data), 65536, stream(parts[1]), tmp_path, reserve=0
        )
    assert (refused.value.code, refused.value.status) == ("offset", 409)
    assert refused.value.extra == {"received": 131072}
    offset = refused.value.extra["received"]
    for piece in pieces(data[offset:], 65536):
        saved, offset = await partials.write(
            session, "pong.zip", len(data), offset, stream(piece), tmp_path, reserve=0
        )
    assert saved is not None
    assert (tmp_path / "pong.zip").read_bytes() == data  # ni trou ni doublon


async def test_a_chunk_cut_in_the_middle_leaves_the_file_where_it_was(tmp_path):
    data, partials, session = big_zip(), PartialUploads(), trusted_session()
    parts = pieces(data, 65536)
    await partials.write(session, "pong.zip", len(data), 0, stream(parts[0]), tmp_path, reserve=0)

    async def cut():
        yield parts[1][:1000]
        raise ConnectionResetError  # le navigateur est parti

    with pytest.raises(ConnectionResetError):
        await partials.write(session, "pong.zip", len(data), 65536, cut(), tmp_path, reserve=0)
    (partial,) = partials._partials.values()
    assert partial.received == 65536
    assert partial.path.stat().st_size == 65536  # pas un octet du morceau coupé
    offset = 65536  # le morceau coupé est renvoyé au même endroit
    for piece in parts[1:]:
        saved, offset = await partials.write(
            session, "pong.zip", len(data), offset, stream(piece), tmp_path, reserve=0
        )
    assert saved is not None
    assert (tmp_path / "pong.zip").read_bytes() == data


async def test_starting_again_replaces_the_unfinished_file(tmp_path):
    data, partials, session = big_zip(), PartialUploads(), trusted_session()
    await partials.write(
        session, "pong.zip", len(data), 0, stream(data[:65536]), tmp_path, reserve=0
    )
    (old,) = temp_files(tmp_path)
    await partials.write(
        session, "pong.zip", len(data), 0, stream(data[:65536]), tmp_path, reserve=0
    )
    (new,) = temp_files(tmp_path)  # un seul fichier provisoire : l'ancien est effacé
    assert new != old
    assert len(partials) == 1


@pytest.mark.parametrize("offset", [65536, 1])
async def test_a_chunk_without_a_beginning_is_refused(tmp_path, offset):
    partials, session = PartialUploads(), trusted_session()
    with pytest.raises(UploadRefused) as refused:
        await partials.write(
            session, "pong.zip", 100_000, offset, stream(b"x" * 10), tmp_path, reserve=0
        )
    assert (refused.value.code, refused.value.status) == ("no_upload", 409)
    assert list(tmp_path.iterdir()) == []


async def test_a_changed_size_is_refused_and_the_file_is_dropped(tmp_path):
    data, partials, session = big_zip(), PartialUploads(), trusted_session()
    await partials.write(
        session, "pong.zip", len(data), 0, stream(data[:65536]), tmp_path, reserve=0
    )
    with pytest.raises(UploadRefused) as refused:
        await partials.write(
            session, "pong.zip", len(data) + 1, 65536, stream(b"x"), tmp_path, reserve=0
        )
    assert refused.value.code == "bad_length"
    assert temp_files(tmp_path) == []
    assert len(partials) == 0


async def test_a_chunk_over_the_limit_is_refused(tmp_path):
    partials, session = PartialUploads(), trusted_session()
    with pytest.raises(UploadRefused) as refused:
        await partials.write(
            session,
            "pong.zip",
            1_000_000,
            0,
            stream(b"x" * 5_000),
            tmp_path,
            reserve=0,
            max_chunk=1_000,
        )
    assert (refused.value.code, refused.value.status) == ("too_large", 413)


async def test_more_bytes_than_announced_are_refused_and_nothing_stays(tmp_path):
    data, partials, session = big_zip(), PartialUploads(), trusted_session()
    with pytest.raises(UploadRefused) as refused:
        await partials.write(
            session, "pong.zip", 1_000, 0, stream(data[:5_000]), tmp_path, reserve=0
        )
    assert refused.value.code == "bad_length"
    assert temp_files(tmp_path) == []
    assert len(partials) == 0


async def test_an_empty_chunk_is_refused(tmp_path):
    partials, session = PartialUploads(), trusted_session()
    with pytest.raises(UploadRefused) as refused:
        await partials.write(session, "pong.zip", 1_000, 0, stream(b""), tmp_path, reserve=0)
    assert refused.value.code == "incomplete"
    assert temp_files(tmp_path) == []


async def test_a_finished_file_that_is_not_an_archive_is_refused_and_nothing_stays(tmp_path):
    junk = b"MZ" + bytes(200_000)
    partials, session = PartialUploads(), trusted_session()
    with pytest.raises(UploadRefused) as refused:
        await send_all(partials, session, "pong.zip", junk, tmp_path, reserve=0)
    assert (refused.value.code, refused.value.status) == ("not_archive", 415)
    assert list(tmp_path.iterdir()) == []
    assert len(partials) == 0


async def test_the_other_format_is_replaced_when_the_game_is_complete(tmp_path):
    (tmp_path / "pong.7z").write_bytes(b"ancien")
    data, partials, session = big_zip(), PartialUploads(), trusted_session()
    await send_all(partials, session, "pong.zip", data, tmp_path, reserve=0)
    assert sorted(p.name for p in tmp_path.iterdir()) == ["pong.zip"]


async def test_the_free_space_is_checked_again_at_every_chunk(tmp_path, monkeypatch):
    """D'autres envois ont pu remplir la carte depuis le début de celui-ci."""
    free = iter(
        [1_000_000, 1_000_000, 900_000, 500]
    )  # le début, deux morceaux, puis la carte se remplit
    Usage = type("Usage", (), {})

    def disk_usage(_path):
        usage = Usage()
        usage.free = next(free)
        return usage

    monkeypatch.setattr(retro_upload.shutil, "disk_usage", disk_usage)
    data, partials, session = big_zip(), PartialUploads(), trusted_session()
    parts = pieces(data, 65536)
    await partials.write(
        session, "pong.zip", len(data), 0, stream(parts[0]), tmp_path, reserve=1_000
    )
    await partials.write(
        session, "pong.zip", len(data), 65536, stream(parts[1]), tmp_path, reserve=1_000
    )
    with pytest.raises(UploadRefused) as refused:
        await partials.write(
            session, "pong.zip", len(data), 131072, stream(parts[2]), tmp_path, reserve=1_000
        )
    assert (refused.value.code, refused.value.status) == ("no_space", 507)
    assert temp_files(tmp_path) == []  # l'envoi est abandonné, la place est rendue


async def test_an_abandoned_upload_is_swept_and_its_file_deleted(tmp_path):
    clock = Clock()
    data, partials, session = (
        big_zip(),
        PartialUploads(clock=clock, ttl=600),
        trusted_session(clock),
    )
    await partials.write(
        session, "pong.zip", len(data), 0, stream(data[:65536]), tmp_path, reserve=0
    )
    clock.now += 599
    partials.sweep()
    assert len(partials) == 1  # pas encore
    clock.now += 2
    partials.sweep()
    assert len(partials) == 0
    assert temp_files(tmp_path) == []
    with pytest.raises(UploadRefused) as refused:  # la page est invitée à reprendre depuis le début
        await partials.write(
            session, "pong.zip", len(data), 65536, stream(data[65536:131072]), tmp_path, reserve=0
        )
    assert refused.value.code == "no_upload"


async def test_each_chunk_keeps_the_upload_alive(tmp_path):
    clock = Clock()
    data, partials, session = (
        big_zip(),
        PartialUploads(clock=clock, ttl=600),
        trusted_session(clock),
    )
    offset = 0
    for piece in pieces(data, 65536)[:4]:
        clock.now += 500  # plus de dix minutes en tout, mais jamais dix minutes sans morceau
        _, offset = await partials.write(
            session, "pong.zip", len(data), offset, stream(piece), tmp_path, reserve=0
        )
    assert len(partials) == 1


async def test_only_a_few_uploads_may_run_at_once(tmp_path):
    partials, session = PartialUploads(max_partials=2), trusted_session()
    for name in ("un.zip", "deux.zip"):
        await partials.write(session, name, 100_000, 0, stream(b"x" * 10), tmp_path, reserve=0)
    with pytest.raises(UploadRefused) as refused:
        await partials.write(
            session, "trois.zip", 100_000, 0, stream(b"x" * 10), tmp_path, reserve=0
        )
    assert (refused.value.code, refused.value.status) == ("busy", 429)
    # recommencer un jeu déjà en cours n'est pas un envoi de plus
    await partials.write(session, "un.zip", 100_000, 0, stream(b"x" * 10), tmp_path, reserve=0)


async def test_two_devices_sending_the_same_name_do_not_mix(tmp_path):
    sessions = UploadSessions(clock=Clock())
    a = sessions.authorize("192.168.1.20", sessions.trusted_session("192.168.1.20")[0])
    b = sessions.authorize("192.168.1.21", sessions.trusted_session("192.168.1.21")[0])
    data, partials = big_zip(), PartialUploads()
    await partials.write(a, "pong.zip", len(data), 0, stream(data[:65536]), tmp_path, reserve=0)
    with pytest.raises(
        UploadRefused
    ) as refused:  # b n'a rien commencé : il ne peut pas reprendre celui de a
        await partials.write(
            b, "pong.zip", len(data), 65536, stream(data[65536:131072]), tmp_path, reserve=0
        )
    assert refused.value.code == "no_upload"
    assert len(partials) == 1


async def test_a_provisional_file_nobody_fills_is_cleaned_at_the_next_start(tmp_path):
    old, recent = tmp_path / ".envoi-abandonne.part", tmp_path / ".envoi-recent.part"
    old.write_bytes(b"x" * 100)
    recent.write_bytes(b"x" * 100)
    os.utime(old, (1, 1))  # dock arrêté en plein envoi, il y a longtemps
    partials, session = PartialUploads(), trusted_session()
    await partials.write(session, "pong.zip", 100_000, 0, stream(b"x" * 10), tmp_path, reserve=0)
    assert not old.exists()
    assert recent.exists()  # peut-être un envoi d'un seul tenant en cours : on n'y touche pas


async def test_the_one_shot_upload_still_works_beside_the_chunked_one(tmp_path):
    data = make_zip()
    saved = await save_rom(stream(data), "pong.zip", len(data), tmp_path, reserve=0)
    assert saved.name == "pong"
