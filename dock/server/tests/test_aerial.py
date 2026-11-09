"""Les vues aériennes : téléchargement et conversion automatiques (réseau et ffmpeg simulés)."""

from __future__ import annotations

import asyncio
import io
from pathlib import Path

import pytest

from mydock_dock import aerial
from mydock_dock.aerial import CATALOG, AerialError, AerialFetcher, target_name


class FakeResponse(io.BytesIO):
    headers = {"Content-Type": "video/quicktime"}

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False


def fetcher(tmp_path: Path, *, enabled=True, which=lambda _n: "/usr/bin/ffmpeg"):
    return AerialFetcher(
        tmp_path / "saver", lambda: enabled, which=which, start_delay=0, retry_delay=0.01
    )


def test_the_catalogue_is_ten_https_films_five_by_day_and_five_by_night():
    assert len(CATALOG) == 10
    assert aerial.BASE_URL.startswith("https://sylvan.apple.com/")
    assert len({t for _, t in CATALOG}) == 10
    for name, title in CATALOG:
        assert name.endswith(".mov")
        assert "/" not in name
        assert target_name(title).endswith(".mp4")
    assert sum("jour" in t for _, t in CATALOG) == 5


def test_status_counts_films_already_in_the_folder(tmp_path):
    f = fetcher(tmp_path)
    assert f.status()["done"] == 0
    saver = tmp_path / "saver"
    saver.mkdir()
    (saver / target_name(CATALOG[0][1])).write_bytes(b"x")
    status = f.status()
    assert (status["done"], status["total"], status["enabled"], status["active"]) == (
        1,
        10,
        True,
        False,
    )
    assert len(f.missing()) == 9


async def test_without_ffmpeg_nothing_is_downloaded_and_the_reason_is_given(tmp_path, monkeypatch):
    called = []
    monkeypatch.setattr("urllib.request.OpenerDirector.open", lambda *a, **k: called.append(1))
    f = fetcher(tmp_path, which=lambda _n: None)
    with pytest.raises(AerialError, match="ffmpeg"):
        await f.fetch_missing()
    assert called == []


async def test_each_missing_film_is_downloaded_converted_and_published(tmp_path, monkeypatch):
    urls = []

    def fake_open(self, request, timeout=None):
        urls.append(request.full_url)
        return FakeResponse(b"mov" * 100)

    monkeypatch.setattr("urllib.request.OpenerDirector.open", fake_open)
    f = fetcher(tmp_path)

    async def fake_convert(filename, title):
        out = f.directory / target_name(title)
        out.write_bytes(b"mp4")

    monkeypatch.setattr(f, "_convert", fake_convert)
    await f.fetch_missing()
    assert urls == [f"{aerial.BASE_URL}/{name}" for name, _ in CATALOG]
    assert f.status()["done"] == 10
    assert not any((f.directory / aerial.WORK).glob("*.mov"))  # le brut est retiré
    assert f.current is None


async def test_a_disabled_fetcher_downloads_nothing(tmp_path, monkeypatch):
    monkeypatch.setattr("urllib.request.OpenerDirector.open", lambda *a, **k: pytest.fail("réseau"))
    await fetcher(tmp_path, enabled=False).fetch_missing()


async def test_network_failure_is_reported_then_retried(tmp_path, monkeypatch):
    attempts = []

    def failing(self, request, timeout=None):
        attempts.append(1)
        raise OSError("réseau coupé")

    monkeypatch.setattr("urllib.request.OpenerDirector.open", failing)
    f = fetcher(tmp_path)
    task = asyncio.create_task(f.run())
    for _ in range(100):
        await asyncio.sleep(0.02)
        if len(attempts) >= 11 and f.error:
            break
    task.cancel()
    assert len(attempts) >= 11  # tous les films essayés, puis un nouvel essai
    assert "Téléchargement impossible" in (f.error or "")


async def test_an_html_answer_is_not_a_video(tmp_path, monkeypatch):
    class Page(FakeResponse):
        headers = {"Content-Type": "text/html"}

    monkeypatch.setattr("urllib.request.OpenerDirector.open", lambda *a, **k: Page(b"<html>"))
    with pytest.raises(AerialError, match="pas une vidéo"):
        await asyncio.to_thread(fetcher(tmp_path)._download, *CATALOG[0])


async def test_a_full_card_stops_before_downloading(tmp_path, monkeypatch):
    monkeypatch.setattr("shutil.disk_usage", lambda _p: type("U", (), {"free": 10})())
    monkeypatch.setattr("urllib.request.OpenerDirector.open", lambda *a, **k: pytest.fail("réseau"))
    with pytest.raises(AerialError, match="place"):
        await asyncio.to_thread(fetcher(tmp_path)._download, *CATALOG[0])


async def test_a_failed_conversion_leaves_no_half_film(tmp_path):
    f = fetcher(tmp_path)
    (f.directory / aerial.WORK).mkdir(parents=True)
    (f.directory / aerial.WORK / CATALOG[0][0]).write_bytes(b"pas une video")
    with pytest.raises(AerialError, match="Conversion"):
        await f._convert(*CATALOG[0])
    assert f.status()["done"] == 0


async def test_one_missing_film_does_not_block_the_others(tmp_path, monkeypatch):
    def fake_open(self, request, timeout=None):
        if CATALOG[0][0] in request.full_url:
            raise OSError("404")
        return FakeResponse(b"mov" * 100)

    monkeypatch.setattr("urllib.request.OpenerDirector.open", fake_open)
    f = fetcher(tmp_path)

    async def fake_convert(filename, title):
        (f.directory / target_name(title)).write_bytes(b"mp4")

    monkeypatch.setattr(f, "_convert", fake_convert)
    with pytest.raises(AerialError, match="1 film"):
        await f.fetch_missing()
    assert f.status()["done"] == 9
