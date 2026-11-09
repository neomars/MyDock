"""Économiseur d'écran, côté Pi : le dossier de vidéos, leur ajout, leur service au navigateur et les réglages."""

import io
import os
import types
import urllib.error
import urllib.request

import httpx
import pytest

from mydock_dock import screensaver
from mydock_dock.__main__ import main
from mydock_dock.config import DockConfig
from mydock_dock.screensaver import SaverError
from mydock_dock.testing import running_dock


@pytest.fixture
async def stack(tmp_path):
    """Un dock en marche (sans PC : ces pages n'en ont pas besoin)."""
    async with running_dock(tmp_path) as dock:
        yield types.SimpleNamespace(dock=dock)


MP4 = b"\x00\x00\x00\x18ftypmp42" + b"x" * 5000


def put(directory, name, data=MP4):
    directory.mkdir(parents=True, exist_ok=True)
    (directory / name).write_bytes(data)


# --- noms et liste ---------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "name", ["mer.mp4", "Vue aérienne (Hawaï) 4.MP4", "a.webm", "x.m4v", "é.mp4"]
)
def test_ordinary_video_names_are_accepted(name):
    assert screensaver.safe_name(name)


@pytest.mark.parametrize(
    "name",
    [
        "",
        ".cache.mp4",
        "../secret.mp4",
        "dossier/x.mp4",
        "x\\y.mp4",
        "a\nb.mp4",
        "a\x00.mp4",
        "x.mov",
        "x.mkv",
        "x.mp4.sh",
        "sans-extension",
        "x" * 200 + ".mp4",
    ],
)
def test_names_that_are_paths_hidden_or_not_videos_are_refused(name):
    assert not screensaver.safe_name(name)


def test_only_real_video_files_of_the_folder_are_listed_by_name(tmp_path):
    put(tmp_path, "b.mp4")
    put(tmp_path, "A.webm")
    put(tmp_path, "notes.txt", b"pas une video")
    put(tmp_path, "vide.mp4", b"")
    put(tmp_path, ".cache.mp4")
    (tmp_path / "sous-dossier.mp4").mkdir()
    (tmp_path / "lien.mp4").symlink_to(tmp_path / "b.mp4")  # jamais un lien symbolique
    found = screensaver.videos(tmp_path)
    assert [v.name for v in found] == ["A.webm", "b.mp4"]
    assert [v.mime for v in found] == ["video/webm", "video/mp4"]
    assert found[1].size == len(MP4)


def test_a_missing_folder_is_an_empty_list(tmp_path):
    assert screensaver.videos(tmp_path / "absent") == []


def test_the_list_is_bounded(tmp_path):
    for n in range(screensaver.MAX_VIDEOS + 5):
        put(tmp_path, f"v{n:03}.mp4", b"x")
    assert len(screensaver.videos(tmp_path)) == screensaver.MAX_VIDEOS


def test_find_only_returns_what_the_listing_knows(tmp_path):
    put(tmp_path, "mer.mp4")
    assert screensaver.find(tmp_path, "mer.mp4").name == "mer.mp4"  # type: ignore[union-attr]
    for name in ("absent.mp4", "../mer.mp4", "mer.mp4/..", "/etc/passwd", "mer.MP4"):
        assert screensaver.find(tmp_path, name) is None


@pytest.mark.parametrize(
    ("raw", "clean"),
    [
        ("/chemin/vers/Vue du matin.MP4", "Vue du matin.mp4"),
        ("/a/b/c%20d%2Fe.webm", "e.webm"),
        ("..\\..\\x.mp4", "x.mp4"),
        ("super:vidéo?.mp4", "super_vidéo_.mp4"),
    ],
)
def test_names_taken_from_an_address_or_a_path_are_cleaned(raw, clean):
    assert screensaver.clean_name(raw) == clean


def test_sizes_are_written_in_french():
    assert screensaver.human_size(512) == "512 o"
    assert screensaver.human_size(1536) == "1,5 Ko"
    assert screensaver.human_size(25 * 1024**2) == "25,0 Mo"
    assert screensaver.human_size(3 * 1024**3) == "3,0 Go"


# --- ajouter ----------------------------------------------------------------------------------------------------


def test_a_video_file_is_copied_into_the_folder_with_a_clean_name(tmp_path):
    source = tmp_path / "téléchargé" / "Vue: du matin.MP4"
    put(source.parent, source.name)
    saved = screensaver.add_file(tmp_path / "saver", source)
    assert saved.name == "Vue_ du matin.mp4" or saved.name.endswith(".mp4")
    assert saved.read_bytes() == MP4
    assert oct(saved.stat().st_mode & 0o777) == "0o644"
    assert [v.name for v in screensaver.videos(tmp_path / "saver")] == [saved.name]
    assert not [p for p in (tmp_path / "saver").iterdir() if p.name.endswith(".part")]


def test_a_name_can_be_chosen_and_must_be_acceptable(tmp_path):
    source = tmp_path / "x.mp4"
    put(tmp_path, "x.mp4")
    assert screensaver.add_file(tmp_path / "s", source, "mer.mp4").name == "mer.mp4"
    for bad in ("../x.mp4", "x.exe", ".x.mp4"):
        with pytest.raises(SaverError, match="nom de vidéo"):
            screensaver.add_file(tmp_path / "s", source, bad)


def test_an_empty_or_unreadable_file_is_refused_and_leaves_nothing_behind(tmp_path):
    put(tmp_path, "vide.mp4", b"")
    with pytest.raises(SaverError, match="vide"):
        screensaver.add_file(tmp_path / "s", tmp_path / "vide.mp4")
    with pytest.raises(SaverError, match="Impossible de lire"):
        screensaver.add_file(tmp_path / "s", tmp_path / "absent.mp4")
    assert screensaver.videos(tmp_path / "s") == []
    assert not list((tmp_path / "s").glob(".*"))


def test_the_card_of_the_pi_is_never_filled(tmp_path, monkeypatch):
    put(tmp_path, "x.mp4")
    monkeypatch.setattr(screensaver, "_free_bytes", lambda _d: 100)
    with pytest.raises(SaverError, match="place libre"):
        screensaver.add_file(tmp_path / "s", tmp_path / "x.mp4")


class Reply(io.BytesIO):
    def __init__(self, data, headers):
        super().__init__(data)
        self.headers = headers


def opener(data=MP4, **headers):
    seen = []

    def open_(request, timeout):
        seen.append((request.full_url, timeout))
        return Reply(data, {"Content-Type": "video/mp4", **headers})

    open_.seen = seen  # type: ignore[attr-defined]
    return open_


def test_a_video_is_downloaded_over_https_into_the_folder(tmp_path):
    fetch = opener(**{"Content-Length": str(len(MP4))})
    saved = screensaver.add_url(
        tmp_path / "s", "https://exemple.org/videos/Vue%20du%20matin.mp4", opener=fetch
    )
    assert saved.name == "Vue du matin.mp4"
    assert saved.read_bytes() == MP4
    assert fetch.seen == [("https://exemple.org/videos/Vue%20du%20matin.mp4", screensaver.TIMEOUT)]


def test_the_extension_comes_from_the_content_type_when_the_address_has_none(tmp_path):
    saved = screensaver.add_url(
        tmp_path / "s",
        "https://exemple.org/v/123",
        opener=opener(**{"Content-Type": "video/webm; codecs=vp9"}),
    )
    assert saved.name == "123.webm"


@pytest.mark.parametrize(
    "url",
    [
        "http://exemple.org/x.mp4",
        "ftp://exemple.org/x.mp4",
        "file:///etc/passwd",
        "https:///x.mp4",
        "x.mp4",
    ],
)
def test_only_https_addresses_are_downloaded(tmp_path, url):
    with pytest.raises(SaverError, match="https"):
        screensaver.add_url(tmp_path / "s", url, opener=opener())


def test_what_is_not_a_video_or_is_too_big_is_refused(tmp_path):
    with pytest.raises(SaverError, match="pas une vidéo"):
        screensaver.add_url(
            tmp_path / "s",
            "https://exemple.org/page.mp4",
            opener=opener(b"<html>", **{"Content-Type": "text/html"}),
        )
    huge = opener(**{"Content-Length": str(screensaver.MAX_BYTES + 1)})
    with pytest.raises(SaverError, match="trop grosse"):
        screensaver.add_url(tmp_path / "s", "https://exemple.org/x.mp4", opener=huge)
    assert screensaver.videos(tmp_path / "s") == []


def test_a_download_that_outgrows_its_limit_is_cut_and_nothing_is_kept(tmp_path, monkeypatch):
    monkeypatch.setattr(screensaver, "MAX_BYTES", 1000)
    with pytest.raises(SaverError, match="trop grosse"):
        screensaver.add_url(tmp_path / "s", "https://exemple.org/x.mp4", opener=opener(b"x" * 5000))
    assert not list((tmp_path / "s").glob("*"))


def test_network_failures_are_explained(tmp_path):
    def broken(_request, _timeout):
        raise urllib.error.URLError("Name or service not known")

    with pytest.raises(SaverError, match="Téléchargement impossible"):
        screensaver.add_url(tmp_path / "s", "https://exemple.org/x.mp4", opener=broken)


def test_a_redirect_to_a_page_that_is_not_https_is_refused():
    handler = screensaver._HttpsOnly()
    request = urllib.request.Request("https://exemple.org/x.mp4")
    with pytest.raises(SaverError, match="https"):
        handler.redirect_request(
            request, io.BytesIO(), 302, "Found", {}, "http://autre.example/x.mp4"
        )


def test_a_video_is_removed_by_its_name_only(tmp_path):
    put(tmp_path, "mer.mp4")
    put(tmp_path, "note.txt", b"x")
    with pytest.raises(SaverError, match="n'est pas dans le dossier"):
        screensaver.remove(tmp_path, "../mer.mp4")
    with pytest.raises(SaverError):
        screensaver.remove(tmp_path, "note.txt")
    screensaver.remove(tmp_path, "mer.mp4")
    assert screensaver.videos(tmp_path) == []
    assert (tmp_path / "note.txt").exists()


# --- la commande « mydock-dock screensaver » ----------------------------------------------------------------------


def make_config(tmp_path):
    path = tmp_path / "dock.toml"
    assert main(["init", "--config", str(path), "--state-dir", str(tmp_path / "etat")]) == 0
    return path


def test_the_command_lists_adds_and_removes_videos(tmp_path, capsys):
    config = make_config(tmp_path)
    capsys.readouterr()
    assert main(["screensaver", "--config", str(config), "list"]) == 0
    assert "Aucune vidéo" in capsys.readouterr().out
    put(tmp_path, "source.mp4")
    assert (
        main(
            [
                "screensaver",
                "--config",
                str(config),
                "add",
                str(tmp_path / "source.mp4"),
                "--name",
                "mer.mp4",
            ]
        )
        == 0
    )
    assert "Ajoutée : mer.mp4" in capsys.readouterr().out
    assert main(["screensaver", "--config", str(config), "list"]) == 0
    assert "mer.mp4  (" in capsys.readouterr().out
    assert main(["screensaver", "--config", str(config), "remove", "mer.mp4"]) == 0
    assert main(["screensaver", "--config", str(config), "remove", "mer.mp4"]) == 1
    assert "n'est pas dans le dossier" in capsys.readouterr().err


def test_the_command_explains_a_refused_source(tmp_path, capsys):
    config = make_config(tmp_path)
    capsys.readouterr()
    assert main(["screensaver", "--config", str(config), "add", "http://exemple.org/x.mp4"]) == 1
    assert "https" in capsys.readouterr().err
    assert main(["screensaver", "--config", str(config), "add", str(tmp_path / "absent.mp4")]) == 1
    assert "Impossible de lire" in capsys.readouterr().err
    assert main(["screensaver", "--config", str(tmp_path / "absent.toml"), "list"]) == 2


def test_the_folder_is_inside_the_state_folder(tmp_path):
    config = DockConfig(path=tmp_path / "dock.toml", state_dir=tmp_path / "etat")
    assert config.saver_dir == tmp_path / "etat" / "screensaver"


# --- servir les vidéos au navigateur ------------------------------------------------------------------------------


async def test_the_browser_lists_and_streams_the_videos_with_range_requests(stack):
    saver = stack.dock.config.saver_dir
    put(saver, "mer.mp4")
    put(saver, "Vue du soir.webm", b"w" * 3000)
    put(saver, "secret.txt", b"non")
    async with httpx.AsyncClient(trust_env=False, base_url=stack.dock.origin) as client:
        listing = await client.get("/api/screensaver")
        assert listing.status_code == 200
        assert listing.headers["cache-control"] == "no-store"
        assert listing.json() == {
            "videos": [
                {"name": "mer.mp4", "size": len(MP4), "mime": "video/mp4"},
                {"name": "Vue du soir.webm", "size": 3000, "mime": "video/webm"},
            ]
        }
        whole = await client.get("/screensaver/mer.mp4")
        assert whole.status_code == 200
        assert whole.content == MP4
        assert whole.headers["content-type"] == "video/mp4"
        assert whole.headers["x-content-type-options"] == "nosniff"
        assert (
            "media-src" not in whole.headers["content-security-policy"]
        )  # « default-src 'self' » suffit
        part = await client.get("/screensaver/mer.mp4", headers={"Range": "bytes=100-199"})
        assert part.status_code == 206
        assert part.content == MP4[100:200]
        assert part.headers["content-range"] == f"bytes 100-199/{len(MP4)}"
        spaced = await client.get("/screensaver/Vue%20du%20soir.webm")
        assert spaced.status_code == 200
        assert len(spaced.content) == 3000


@pytest.mark.parametrize(
    "path",
    [
        "/screensaver/secret.txt",
        "/screensaver/absent.mp4",
        "/screensaver/..%2Fstate.json",
        "/screensaver/%2e%2e/x.mp4",
        "/screensaver//etc/passwd",
        "/screensaver/lien.mp4",
    ],
)
async def test_nothing_but_the_listed_videos_is_ever_served(stack, path):
    saver = stack.dock.config.saver_dir
    put(saver, "mer.mp4")
    put(saver, "secret.txt", b"non")
    os.symlink("/etc/passwd", saver / "lien.mp4")
    async with httpx.AsyncClient(trust_env=False, base_url=stack.dock.origin) as client:
        reply = await client.get(path)
    assert reply.status_code in (404, 405)
    assert b"root:" not in reply.content


async def test_without_a_folder_the_list_is_empty(stack):
    async with httpx.AsyncClient(trust_env=False, base_url=stack.dock.origin) as client:
        assert (await client.get("/api/screensaver")).json() == {"videos": []}


# --- les réglages et le diagnostic ---------------------------------------------------------------------------------


async def test_the_settings_screen_sets_the_delay_and_the_content_and_the_dock_keeps_them(tmp_path):
    from mydock_dock.testing import call

    async with running_dock(tmp_path) as dock, dock.connect() as ws:
        ack = await call(ws, "s1", "dock.ui.set", {"saver_after": 120, "saver_content": "scenes"})
        assert ack["ok"] is True
        assert (ack["r"]["saver_after"], ack["r"]["saver_content"]) == (120, "scenes")
        for params in (
            {"saver_after": 7},
            {"saver_after": "300"},
            {"saver_content": "tout"},
            {"saver_after": True},
        ):
            refused = await call(ws, "s2", "dock.ui.set", params)
            assert refused["ok"] is False
            assert refused["e"]["code"] in ("bad_params", "invalid")
    assert dock.server.store.ui["saver_after"] == 120  # rien n'a changé après les refus
    from mydock_dock.store import StateStore

    assert StateStore(dock.config.state_dir).ui["saver_content"] == "scenes"


def test_the_diagnostic_reports_the_choice_and_the_videos(tmp_path):
    from mydock_common.report import Report
    from mydock_dock.doctor import check_saver

    config = DockConfig(path=tmp_path / "dock.toml", state_dir=tmp_path / "etat")
    report = Report()
    check_saver(report, config)
    text = report.render()
    assert "Économiseur : après 5 min sans toucher, contenu : scènes et vidéos" in text
    assert "Aucune vidéo" in text
    assert "mydock-dock screensaver add" in text
    assert not report.failed

    put(config.saver_dir, "mer.mp4")
    (config.saver_dir / "gros.mp4").write_bytes(b"")  # vide : ignorée
    report = Report()
    check_saver(report, config)
    assert "1 vidéo(s)" in report.render()

    from mydock_dock.store import StateStore

    StateStore(config.state_dir).update_ui({"saver_after": 0})
    report = Report()
    check_saver(report, config)
    assert "Économiseur : jamais" in report.render()
