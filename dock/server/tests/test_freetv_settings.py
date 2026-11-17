"""La taille de la page de Free TV : réglée depuis l'écran du dock, gardée dans dock.toml, transmise à l'assistant."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from mydock_common.config import load_toml, write_toml_private
from mydock_dock import config as config_module
from mydock_dock.config import DEFAULT_FREETV_SCALE, DockConfig
from mydock_dock.testing import call, running_dock, snapshot_of
from mydock_dock.validation import ValidationFailure


def test_the_page_of_free_tv_is_one_and_a_half_times_bigger_by_default():
    """À 0,7 le texte de Free TV (et son message d'erreur) était illisible sur le 7 pouces en 1024 × 600."""
    assert DEFAULT_FREETV_SCALE == 1.5
    assert DockConfig.from_dict({}, Path("/x/dock.toml")).freetv_scale == 1.5


def test_the_old_default_written_by_a_first_save_is_replaced_and_said_once(tmp_path, caplog):
    """La 1.9.0 écrivait `scale = 0.7` dans dock.toml à la première sauvegarde : sans ce remplacement, rien ne changerait."""
    config_module._NOTED.clear()
    path = tmp_path / "dock.toml"
    write_toml_private(path, {"freetv": {"scale": 0.7}}, header="x")
    with caplog.at_level("WARNING", logger="mydock_dock.config"):
        config = DockConfig.load(path)
        DockConfig.load(path)
    assert config.freetv_scale == 1.5
    said = [r.getMessage() for r in caplog.records if "scale" in r.getMessage()]
    assert len(said) == 1
    assert "0,7" in said[0] or "0.7" in said[0]
    config.save()
    assert load_toml(path)["freetv"]["scale"] == 1.5  # et le fichier garde la nouvelle valeur


def test_another_size_chosen_in_the_file_is_kept(tmp_path):
    path = tmp_path / "dock.toml"
    write_toml_private(path, {"freetv": {"scale": 2.1}}, header="x")
    assert DockConfig.load(path).freetv_scale == 2.1


@pytest.mark.parametrize("scale", [2, 2.25, 0.5, 3, 0.25])
def test_the_size_is_kept_in_the_private_file(tmp_path, scale):
    path = tmp_path / "dock.toml"
    config = DockConfig.from_dict({}, path)
    config.set_freetv(scale=scale)
    assert DockConfig.load(path).freetv_scale == float(scale)


@pytest.mark.parametrize("scale", [0, 0.2, 3.1, "grand", True, float("nan"), None])
def test_a_refused_size_changes_nothing(tmp_path, scale):
    path = tmp_path / "dock.toml"
    config = DockConfig.from_dict({}, path)
    if scale is None:
        config.set_freetv(scale=None)  # absent : ne change rien
    else:
        with pytest.raises(ValidationFailure):
            config.set_freetv(scale=scale)
    assert config.freetv_scale == 1.5


async def test_changing_the_size_reaches_the_helper_and_the_screen(tmp_path):
    async with (
        running_dock(tmp_path) as dock,
        dock.connect() as browser,
        dock.connect_player() as helper,
    ):
        await helper.send(json.dumps({"t": "hello", "v": 1, "vlc": True, "touch": True}))
        first = json.loads(await asyncio.wait_for(helper.recv(), 3))
        assert first["freetv_scale"] == 1.5
        ack = await call(browser, "s1", "dock.freetv.set", {"scale": 2.25})
        assert ack["ok"], ack
        assert ack["r"]["freetv_scale"] == 2.25
        again = json.loads(await asyncio.wait_for(helper.recv(), 3))
        assert again["freetv_scale"] == 2.25
        assert dock.config.freetv_scale == 2.25
        shown = (await snapshot_of(browser, "dock"))["data"]["media"]
        assert shown["freetv_scale"] == 2.25


@pytest.mark.parametrize(
    "params",
    [{"scale": 0}, {"scale": 3.5}, {"scale": "2"}, {"scale": True}, {}, {"scale": 2, "x": 1}],
)
async def test_a_bad_size_is_refused_and_changes_nothing(tmp_path, params):
    async with running_dock(tmp_path) as dock, dock.connect() as browser:
        ack = await call(browser, "s2", "dock.freetv.set", params)
        assert ack["ok"] is False
        assert dock.config.freetv_scale == 1.5
