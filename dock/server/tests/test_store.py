import json

import pytest

from mydock_dock.store import PAGE_IDS, StateStore
from mydock_dock.validation import ValidationFailure


def test_defaults(tmp_path):
    store = StateStore(tmp_path)
    assert store.ui == {"last_page": "system", "order": [], "hidden": []}
    assert store.pc["interfaces"] == []


def test_ui_changes_persist_across_restarts(tmp_path):
    StateStore(tmp_path).update_ui({"last_page": "markets", "order": ["markets", "system"]})
    again = StateStore(tmp_path)
    assert again.ui["last_page"] == "markets"
    assert again.ui["order"] == ["markets", "system"]


@pytest.mark.parametrize(
    "patch",
    [
        {"last_page": "inconnue"},
        {"order": ["system", "system"]},
        {"order": ["nimporte"]},
        {"order": "system"},
        {"hidden": ["settings"]},
        {"hidden": [1]},
    ],
)
def test_invalid_ui_patches_are_refused_and_change_nothing(tmp_path, patch):
    store = StateStore(tmp_path)
    before = store.ui
    with pytest.raises(ValidationFailure):
        store.update_ui(patch)
    assert store.ui == before


def test_every_page_can_be_hidden_except_settings(tmp_path):
    store = StateStore(tmp_path)
    others = [p for p in PAGE_IDS if p != "settings"]
    assert store.update_ui({"hidden": others})["hidden"] == others


def test_no_write_when_nothing_changes(tmp_path):
    store = StateStore(tmp_path)
    store.update_ui({"last_page": "power"})
    path = tmp_path / "state.json"
    mtime = path.stat().st_mtime_ns
    store.update_ui({"last_page": "power"})
    assert path.stat().st_mtime_ns == mtime


def test_update_pc_reports_real_changes_only(tmp_path):
    store = StateStore(tmp_path)
    itf = [{"name": "enp3s0", "mac": "02:00:5e:10:00:01", "kind": "ethernet"}]
    assert store.update_pc(hostname="pc", interfaces=itf, seen_at=1) is True
    assert store.update_pc(hostname="pc", interfaces=itf, seen_at=2) is False  # seul seen_at bouge
    assert store.update_pc(hostname="autre") is True
    with pytest.raises(KeyError):
        store.update_pc(inconnu=1)


def test_corrupted_state_is_set_aside_not_fatal(tmp_path):
    (tmp_path / "state.json").write_text("{ pas du json")
    store = StateStore(tmp_path)
    assert store.ui["last_page"] == "system"
    assert (tmp_path / "state.json.bad").exists()


def test_unknown_keys_in_the_file_are_ignored(tmp_path):
    (tmp_path / "state.json").write_text(
        json.dumps({"ui": {"last_page": "steam", "intrus": 1}, "pc": {"hostname": "x"}})
    )
    store = StateStore(tmp_path)
    assert store.ui == {"last_page": "steam", "order": [], "hidden": []}
    assert store.pc["hostname"] == "x"


def test_unwritable_state_dir_keeps_working_in_memory(tmp_path):
    blocker = tmp_path / "fichier"
    blocker.write_text("je ne suis pas un dossier")
    store = StateStore(blocker / "sous-dossier")
    assert store.update_ui({"last_page": "power"})["last_page"] == "power"
    assert store.ui["last_page"] == "power"
