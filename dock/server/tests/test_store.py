import json

import pytest

from mydock_dock.store import PAGE_IDS, StateStore
from mydock_dock.validation import ValidationFailure


def test_defaults(tmp_path):
    store = StateStore(tmp_path)
    assert store.ui == {"last_page": "system", "order": [], "hidden": [], "pinned_apps": []}
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


def test_pinned_apps_keep_their_order_and_persist(tmp_path):
    store = StateStore(tmp_path)
    chosen = [
        "org.mozilla.firefox.desktop",
        "spotify.desktop",
        "kde4-k.desktop",
        "a+b_c-d.e.desktop",
    ]
    assert store.update_ui({"pinned_apps": chosen})["pinned_apps"] == chosen
    assert StateStore(tmp_path).ui["pinned_apps"] == chosen
    assert store.update_ui({"pinned_apps": list(reversed(chosen))})["pinned_apps"] == list(
        reversed(chosen)
    )
    assert store.update_ui({"pinned_apps": []})["pinned_apps"] == []


@pytest.mark.parametrize(
    "apps",
    [
        "firefox.desktop",
        ["firefox"],
        ["firefox.desktop", "firefox.desktop"],
        ["../etc/passwd.desktop"],
        ["a b.desktop"],
        ["-rf.desktop", 5],
        [f"app{i}.desktop" for i in range(25)],
        ["x" * 300 + ".desktop"],
        [None],
        {"a": 1},
    ],
)
def test_invalid_pinned_apps_are_refused_and_change_nothing(tmp_path, apps):
    store = StateStore(tmp_path)
    store.update_ui({"pinned_apps": ["spotify.desktop"]})
    with pytest.raises(ValidationFailure):
        store.update_ui({"pinned_apps": apps})
    assert store.ui["pinned_apps"] == ["spotify.desktop"]


def test_a_state_file_from_an_older_version_gets_an_empty_launcher(tmp_path):
    (tmp_path / "state.json").write_text(
        '{"version": 1, "ui": {"last_page": "power", "order": [], "hidden": []}}'
    )
    store = StateStore(tmp_path)
    assert store.ui["last_page"] == "power"
    assert store.ui["pinned_apps"] == []


def test_the_market_list_starts_with_bitcoin_and_ethereum_in_euros(tmp_path):
    assert StateStore(tmp_path).markets == {
        "watchlist": [{"kind": "crypto", "symbol": "BTC"}, {"kind": "crypto", "symbol": "ETH"}],
        "currency": "EUR",
    }


def test_the_market_list_keeps_its_order_and_persists(tmp_path):
    store = StateStore(tmp_path)
    wanted = [
        {"kind": "stock", "symbol": "mstr"},
        {"kind": "crypto", "symbol": "sol"},
        {"kind": "stock", "symbol": "^fchi"},
    ]
    saved = store.update_markets({"watchlist": wanted, "currency": "USD"})
    assert saved["watchlist"] == [
        {"kind": "stock", "symbol": "MSTR"},
        {"kind": "crypto", "symbol": "SOL"},
        {"kind": "stock", "symbol": "^FCHI"},
    ]
    assert StateStore(tmp_path).markets == saved
    assert store.update_markets({"watchlist": []})["watchlist"] == []  # vider la liste est permis
    assert StateStore(tmp_path).markets["watchlist"] == []
    assert store.update_markets({"currency": "EUR"})["currency"] == "EUR"


@pytest.mark.parametrize(
    "patch",
    [
        {"watchlist": "BTC"},
        {"watchlist": [{"kind": "crypto", "symbol": "BTC"}, {"kind": "crypto", "symbol": "btc"}]},
        {"watchlist": [{"kind": "crypto"}]},
        {"watchlist": [{"kind": "action", "symbol": "MSTR"}]},
        {"watchlist": [{"kind": "stock", "symbol": f"S{i}"} for i in range(21)]},
        {"currency": "GBP"},
        {"currency": None},
    ],
)
def test_an_invalid_market_list_is_refused_and_changes_nothing(tmp_path, patch):
    store = StateStore(tmp_path)
    before = store.markets
    with pytest.raises(ValidationFailure):
        store.update_markets(patch)
    assert store.markets == before


def test_a_state_file_from_before_the_stock_market_gets_the_default_list(tmp_path):
    (tmp_path / "state.json").write_text(
        '{"version": 1, "ui": {"last_page": "power", "order": [], "hidden": []}}'
    )
    store = StateStore(tmp_path)
    assert [i["symbol"] for i in store.markets["watchlist"]] == ["BTC", "ETH"]


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
    assert store.ui == {"last_page": "steam", "order": [], "hidden": [], "pinned_apps": []}
    assert store.pc["hostname"] == "x"


def test_unwritable_state_dir_keeps_working_in_memory(tmp_path):
    blocker = tmp_path / "fichier"
    blocker.write_text("je ne suis pas un dossier")
    store = StateStore(blocker / "sous-dossier")
    assert store.update_ui({"last_page": "power"})["last_page"] == "power"
    assert store.ui["last_page"] == "power"
