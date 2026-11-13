"""L'envoi des jeux depuis le PC : la session à code, l'écriture d'un fichier, et la page du portail."""

import io
import zipfile

import httpx
import pytest

from mydock_dock import retro_upload
from mydock_dock.retro_upload import (
    MAX_WRONG,
    UPLOAD_CSP,
    UploadRefused,
    UploadSessions,
    clean_filename,
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
    sessions.reserve(10)
    sessions.done(10)
    assert sessions.info()["files"] == 1


def test_a_session_is_limited_in_files_and_bytes(sessions):
    sessions.start()
    with pytest.raises(UploadRefused) as full:
        sessions.reserve(retro_upload.MAX_SESSION_BYTES + 1)
    assert full.value.code == "session_full"
    for _ in range(retro_upload.MAX_FILES):
        sessions.done(1)
    with pytest.raises(UploadRefused) as many:
        sessions.reserve(1)
    assert many.value.code == "too_many_files"


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
        for path in ("/jeux", "/api/retro/login", "/api/retro/upload"):
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
