"""Le diagnostic de l'interface compilée : un fichier manquant laisserait un onglet vide, il faut le voir."""

from __future__ import annotations

import os
import shutil
from pathlib import Path

import pytest

from mydock_common.report import Report
from mydock_dock.config import DockConfig
from mydock_dock.doctor import _http_get, check_web, entry_of, missing_assets

DIST = Path(__file__).resolve().parents[3] / "dock" / "web" / "dist"

pytestmark = pytest.mark.skipif(
    not (DIST / "index.html").is_file(), reason="interface non compilée"
)


@pytest.fixture
def site(tmp_path) -> Path:
    """Une copie jetable de l'interface compilée, et un dossier d'état."""
    shutil.copytree(DIST, tmp_path / "web")
    (tmp_path / "state").mkdir()
    return tmp_path


def run(site: Path, get=lambda _url: None) -> Report:
    config = DockConfig.from_dict(
        {"paths": {"web_dir": str(site / "web"), "state_dir": str(site / "state")}}, site / "d.toml"
    )
    report = Report()
    check_web(report, config, get=get)
    return report


def entry(site: Path) -> str:
    found = entry_of((site / "web" / "index.html").read_text())
    assert found is not None
    return found


def lazy_page(site: Path, name: str = "Music") -> Path:
    return next((site / "web" / "assets").glob(f"{name}-*.js"))


# --- lire index.html ---------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "src",
    [
        "./assets/index-D5XASdAc.js",
        "/assets/index-D5XASdAc.js",
        "http://127.0.0.1:8080/assets/index-D5XASdAc.js",
    ],
)
def test_the_entry_script_is_found_however_the_page_writes_it(src):
    page = f'<script type="module" crossorigin src="{src}"></script><link rel="modulepreload" href="/assets/x.js">'
    assert entry_of(page) == "/assets/index-D5XASdAc.js"


def test_a_page_without_an_entry_script_has_none():
    assert entry_of("x") is None
    assert entry_of('<script src="/a.js"></script>') is None


# --- les fichiers sur le disque -----------------------------------------------------------------------


def test_the_compiled_interface_of_the_repository_is_complete():
    assert missing_assets(DIST) == []


def test_a_complete_interface_is_reported_as_such(site):
    report = run(site)
    text = report.render()
    assert "Interface complète" in text
    assert not report.failed


def test_a_missing_page_is_named_and_says_what_it_costs(site):
    music = lazy_page(site)
    music.unlink()
    report = run(site)
    assert report.failed
    text = report.render()
    assert music.name in text
    assert "onglets" in text
    assert "vides" in text
    assert "réinstalle le paquet mydock" in text


def test_a_missing_entry_script_is_reported_before_anything_else(site):
    (site / "web" / entry(site).lstrip("/")).unlink()
    report = run(site)
    assert report.failed
    assert "index.html demande" in report.render()
    assert "Interface complète" not in report.render()


def test_many_missing_files_are_counted_and_the_list_is_cut(site):
    for page in ("Music", "Sound", "Apps", "Power", "Settings", "Steam"):
        lazy_page(site, page).unlink()
    text = run(site).render()
    assert "6 fichier(s) absent(s)" in text
    assert "…" in text


def test_a_missing_stylesheet_or_font_is_seen_too(site):
    next((site / "web" / "assets").glob("*.woff2")).unlink()
    assert run(site).failed


@pytest.mark.skipif(
    os.getuid() == 0, reason="root lit tout : le test suppose un utilisateur ordinaire"
)
def test_a_file_the_service_cannot_read_counts_as_missing(site):
    lazy_page(site).chmod(0o000)
    assert run(site).failed


def test_an_unreadable_directory_does_not_crash_the_check(tmp_path):
    (tmp_path / "web").mkdir()
    (tmp_path / "web" / "index.html").write_text("x")  # ni script d'entrée ni dossier assets
    (tmp_path / "state").mkdir()
    report = run(tmp_path)
    assert not report.failed
    assert "non vérifiée" in report.render()


# --- ce que le dock sert vraiment ----------------------------------------------------------------------


def serving(site: Path, *, index_status=200, script_status=200, other_entry: str | None = None):
    page = (site / "web" / "index.html").read_text()
    if other_entry:
        page = page.replace(entry(site).lstrip("/"), other_entry.lstrip("/"))
    calls: list[str] = []

    def get(url: str):
        calls.append(url)
        if url.endswith("/"):
            return index_status, page.encode()
        return script_status, b"x"

    get.calls = calls  # type: ignore[attr-defined]
    return get


def test_a_dock_that_serves_the_same_interface_is_confirmed_over_http(site):
    get = serving(site)
    report = run(site, get)
    assert "Le dock sert l'interface en HTTP" in report.render()
    assert not report.failed
    assert get.calls[0] == "http://127.0.0.1:8080/"  # le port du dock, par défaut 8080
    assert (
        get.calls[1] == f"http://127.0.0.1:8080{entry(site)}"
    )  # un chemin absolu, jamais « ./assets »


def test_a_dock_that_serves_another_interface_is_told_to_restart(site):
    report = run(site, serving(site, other_entry="/assets/index-AUTRE.js"))
    text = report.render()
    assert "index-AUTRE.js" in text
    assert "systemctl restart mydock-dock" in text
    assert not report.failed  # un avertissement : un redémarrage suffit


def test_a_dock_that_is_not_running_is_not_an_error(site):
    report = run(site, lambda _url: None)
    assert "interface non testée en HTTP" in report.render()
    assert not report.failed


def test_a_dock_that_answers_without_the_interface_is_flagged(site):
    report = run(site, serving(site, index_status=503))
    assert "503" in report.render()
    assert not report.failed


def test_a_dock_that_serves_the_page_but_not_its_script_fails(site):
    report = run(site, serving(site, script_status=404))
    assert report.failed
    assert "pas /assets/" in report.render()
    assert "404" in report.render()


def test_the_real_http_probe_gives_up_quietly_on_a_closed_port():
    assert _http_get("http://127.0.0.1:1/", timeout=0.5) is None
