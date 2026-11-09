import json

import pytest

from mydock_dock.store import PAGE_IDS, SCREEN_SLEEP_CHOICES, StateStore
from mydock_dock.validation import ValidationFailure


def test_defaults(tmp_path):
    store = StateStore(tmp_path)
    assert store.ui == {
        "last_page": "system",
        "order": [],
        "hidden": [],
        "pinned_apps": [],
        "screen_sleep": 0,  # jamais : on ne coupe pas l'écran sans que ce soit demandé
        "saver_after": 300,  # l'économiseur d'écran démarre après 5 minutes sans toucher
        "saver_content": "videos",
        "saver_aerial": True,
        "saver_apple": True,
        "saver_sources": [],
    }
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
        "refresh": 15,
    }


def test_the_refresh_pace_is_one_of_four_and_persists(tmp_path):
    store = StateStore(tmp_path)
    for seconds in (30, 60, 300, 15):
        assert store.update_markets({"refresh": seconds})["refresh"] == seconds
        assert StateStore(tmp_path).markets["refresh"] == seconds


@pytest.mark.parametrize("value", [0, 10, 20, 301, "30", True, None, 15.5, [15]])
def test_an_unknown_refresh_pace_is_refused_and_changes_nothing(tmp_path, value):
    store = StateStore(tmp_path)
    with pytest.raises(ValidationFailure):
        store.update_markets({"refresh": value})
    assert store.markets["refresh"] == 15


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
    assert store.ui == {
        "last_page": "steam",
        "order": [],
        "hidden": [],
        "pinned_apps": [],
        "screen_sleep": 0,
        "saver_after": 300,
        "saver_content": "videos",
        "saver_aerial": True,
        "saver_apple": True,
        "saver_sources": [],
    }
    assert store.pc["hostname"] == "x"


def test_unwritable_state_dir_keeps_working_in_memory(tmp_path):
    blocker = tmp_path / "fichier"
    blocker.write_text("je ne suis pas un dossier")
    store = StateStore(blocker / "sous-dossier")
    assert store.update_ui({"last_page": "power"})["last_page"] == "power"
    assert store.ui["last_page"] == "power"


def test_the_screen_sleep_delay_is_one_of_the_offered_values_and_persists(tmp_path):
    store = StateStore(tmp_path)
    for seconds in SCREEN_SLEEP_CHOICES:
        assert store.update_ui({"screen_sleep": seconds})["screen_sleep"] == seconds
    assert StateStore(tmp_path).ui["screen_sleep"] == SCREEN_SLEEP_CHOICES[-1]


@pytest.mark.parametrize("value", [-1, 30, 61, 3600, "300", 300.0, True, None, [300]])
def test_other_screen_sleep_delays_are_refused_and_change_nothing(tmp_path, value):
    store = StateStore(tmp_path)
    store.update_ui({"screen_sleep": 300})
    with pytest.raises(ValidationFailure, match="veille de l'écran"):
        store.update_ui({"screen_sleep": value})
    assert store.ui["screen_sleep"] == 300


@pytest.mark.parametrize("damaged", ["jamais", -5, 7, 1.5, None, [60], True])
def test_a_damaged_screen_sleep_in_the_file_falls_back_to_never(tmp_path, damaged):
    (tmp_path / "state.json").write_text(json.dumps({"ui": {"screen_sleep": damaged}}))
    assert StateStore(tmp_path).ui["screen_sleep"] == 0


def test_a_state_file_from_before_the_screen_setting_gets_the_default(tmp_path):
    (tmp_path / "state.json").write_text(json.dumps({"ui": {"last_page": "power"}}))
    store = StateStore(tmp_path)
    assert (store.ui["last_page"], store.ui["screen_sleep"]) == ("power", 0)


# --- l'économiseur d'écran -------------------------------------------------------------------------------------


def test_the_screensaver_delay_is_one_of_the_offered_values_and_persists(tmp_path):
    from mydock_dock.store import SAVER_CHOICES

    store = StateStore(tmp_path)
    assert SAVER_CHOICES == (0, 60, 120, 300, 600, 900, 1800)
    for seconds in SAVER_CHOICES:
        assert store.update_ui({"saver_after": seconds})["saver_after"] == seconds
    assert StateStore(tmp_path).ui["saver_after"] == SAVER_CHOICES[-1]


@pytest.mark.parametrize("value", [-1, 30, 61, 301, 3600, "300", 300.0, True, None, [300]])
def test_other_screensaver_delays_are_refused_and_change_nothing(tmp_path, value):
    store = StateStore(tmp_path)
    store.update_ui({"saver_after": 120})
    with pytest.raises(ValidationFailure, match="économiseur"):
        store.update_ui({"saver_after": value})
    assert store.ui["saver_after"] == 120


def test_the_screensaver_content_is_scenes_videos_or_both(tmp_path):
    store = StateStore(tmp_path)
    for content in ("scenes", "videos", "all"):
        assert store.update_ui({"saver_content": content})["saver_content"] == content
    for bad in ("tout", "", None, 3, ["all"]):
        with pytest.raises(ValidationFailure, match="économiseur"):
            store.update_ui({"saver_content": bad})
    assert store.ui["saver_content"] == "all"


@pytest.mark.parametrize("damaged", ["jamais", -5, 7, 1.5, None, [60], True])
def test_a_damaged_screensaver_in_the_file_falls_back_to_the_defaults(tmp_path, damaged):
    (tmp_path / "state.json").write_text(
        json.dumps({"ui": {"saver_after": damaged, "saver_content": damaged}})
    )
    ui = StateStore(tmp_path).ui
    assert (ui["saver_after"], ui["saver_content"]) == (300, "videos")


def test_screensaver_sources_are_https_addresses_and_persist(tmp_path):
    store = StateStore(tmp_path)
    urls = ["https://x.org/a.mp4", "https://x.org/liste.txt"]
    assert store.update_ui({"saver_sources": urls})["saver_sources"] == urls
    assert StateStore(tmp_path).ui["saver_sources"] == urls
    assert store.update_ui({"saver_apple": False})["saver_apple"] is False
    assert StateStore(tmp_path).ui["saver_apple"] is False
    assert store.update_ui({"saver_sources": []})["saver_sources"] == []


@pytest.mark.parametrize(
    "value",
    [
        ["http://x.org/a.mp4"],
        ["https://user:pw@x.org/a.mp4"],
        ["https://x.org/a b.mp4"],
        ["https://x.org/a.mp4", "https://x.org/a.mp4"],
        [f"https://x.org/{i}.mp4" for i in range(21)],
        "https://x.org/a.mp4",
        [None],
    ],
)
def test_bad_screensaver_sources_are_refused_and_change_nothing(tmp_path, value):
    store = StateStore(tmp_path)
    store.update_ui({"saver_sources": ["https://x.org/ok.mp4"]})
    with pytest.raises(ValidationFailure):
        store.update_ui({"saver_sources": value})
    assert store.ui["saver_sources"] == ["https://x.org/ok.mp4"]


def test_apple_switch_must_be_a_boolean(tmp_path):
    with pytest.raises(ValidationFailure):
        StateStore(tmp_path).update_ui({"saver_apple": "oui"})


def test_a_damaged_sources_list_in_the_file_keeps_only_the_valid_ones(tmp_path):
    (tmp_path / "state.json").write_text(
        json.dumps(
            {
                "version": 1,
                "ui": {
                    "saver_sources": [
                        "https://x.org/a.mp4",
                        "ftp://x.org/b",
                        3,
                        "https://x.org/a.mp4",
                    ],
                    "saver_apple": "peut-être",
                },
            }
        )
    )
    ui = StateStore(tmp_path).ui
    assert ui["saver_sources"] == ["https://x.org/a.mp4"]
    assert ui["saver_apple"] is True
