"""`mydock-dock doctor` : la section Bourse dit ce qui est suivi et ce que chaque fournisseur répond."""

import asyncio
import json
from pathlib import Path

from mydock_common.report import Report
from mydock_dock.config import DockConfig
from mydock_dock.doctor import check_markets
from mydock_dock.markets.errors import NetworkError
from mydock_dock.markets.testing import FakeFetcher, json_response, text_response

DATA = Path(__file__).parent / "data" / "markets"


def sample(name):
    return json.loads((DATA / f"{name}.json").read_text(encoding="utf-8"))


def config_for(tmp_path, **markets) -> DockConfig:
    return DockConfig.from_dict(
        {"paths": {"state_dir": str(tmp_path / "state")}, "markets": markets}, tmp_path / "d.toml"
    )


def healthy() -> FakeFetcher:
    fetcher = FakeFetcher()
    fetcher.on("/ticker/24hr", json_response(sample("binance_ticker_batch")))
    fetcher.on("/klines", json_response(sample("binance_klines_30m")))
    fetcher.on("chart/AAPL", json_response(sample("yahoo_aapl_1d")))
    fetcher.on("finnhub.io", json_response(sample("finnhub_quote")))
    return fetcher


async def test_the_report_lists_what_is_followed_and_each_provider_that_answers(tmp_path):
    report = Report()
    await check_markets(report, config_for(tmp_path), fetcher=healthy())
    text = report.render()
    assert "Bourse" in text
    assert "2 valeur(s) suivie(s), cryptos cotées en EUR" in text
    assert "Clé Finnhub : aucune" in text
    assert "Binance répond (BTC : 61250.5 EUR" in text
    assert "Yahoo Finance répond (AAPL : 231.2 USD" in text
    assert "Finnhub" not in text.replace("Clé Finnhub", "")
    assert not report.failed


async def test_a_configured_key_adds_its_provider_and_is_never_shown(tmp_path):
    report = Report()
    config = config_for(tmp_path, finnhub_key="cle-tres-secrete-42")
    fetcher = healthy()
    await check_markets(report, config, fetcher=fetcher)
    text = report.render()
    assert "Clé Finnhub : configurée (non affichée)" in text
    assert "Finnhub répond (AAPL : 182.4 USD" in text
    assert "cle-tres-secrete-42" not in text
    assert fetcher.calls_to("finnhub.io")[0].headers == {"X-Finnhub-Token": "cle-tres-secrete-42"}


async def test_a_provider_in_trouble_is_a_warning_and_the_others_still_run(tmp_path):
    fetcher = FakeFetcher()
    fetcher.on("binance", text_response("", status=451))
    fetcher.on("chart/AAPL", json_response(sample("yahoo_aapl_1d")))
    report = Report()
    await check_markets(report, config_for(tmp_path), fetcher=fetcher)
    text = report.render()
    assert "Binance : Binance n'est pas accessible depuis ce pays" in text
    assert "Yahoo Finance répond" in text
    assert "est remplacé par le suivant" in text
    assert not report.failed  # une panne de fournisseur n'est pas une panne du dock


async def test_when_nothing_answers_the_report_points_at_the_pi_network(tmp_path):
    fetcher = FakeFetcher().on("", NetworkError("Le nom query2.finance.yahoo.com est introuvable."))
    report = Report()
    await check_markets(report, config_for(tmp_path), fetcher=fetcher)
    text = report.render()
    assert "Aucun fournisseur ne répond" in text
    assert "accès à Internet" in text
    assert "dernières cotations gardées" in text


async def test_a_provider_that_hangs_is_cut_short(tmp_path):
    class Hanging:
        async def get(self, *_a, **_k):
            await asyncio.sleep(5)

    report = Report()
    await check_markets(report, config_for(tmp_path), fetcher=Hanging(), timeout=0.1)
    assert "ne répond pas (délai dépassé)" in report.render()


async def test_offline_skips_every_request(tmp_path):
    fetcher = FakeFetcher()
    report = Report()
    await check_markets(report, config_for(tmp_path), online=False, fetcher=fetcher)
    assert fetcher.calls == []
    assert "Test des fournisseurs ignoré (--offline)" in report.render()


async def test_the_age_of_the_kept_quotes_is_shown(tmp_path):
    (tmp_path / "state").mkdir()
    (tmp_path / "state" / "markets-cache.json").write_text("{}")
    report = Report()
    await check_markets(report, config_for(tmp_path), online=False)
    assert "Dernières cotations gardées : il y a 0 min" in report.render()
