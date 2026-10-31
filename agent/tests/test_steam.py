"""Steam : format VDF, bibliothèques, jeux installés, jaquettes, jeu en cours, lancement."""

import asyncio
import base64
import io
from pathlib import Path

import pytest

from mydock_agent.collectors import steam
from mydock_agent.collectors.steam import (
    SteamProcesses,
    SteamProvider,
    VdfError,
    find_cover,
    library_folders,
    parse_vdf,
    prepare_cover,
    read_games,
    read_manifest,
    register_steam_actions,
    running_appid,
    scan_processes,
    steam_roots,
    vget,
)
from mydock_agent.launcher import LaunchError
from mydock_agent.testing import eventually, png_bytes
from mydock_common.actions import ActionContext, ActionError, ActionRegistry
from mydock_common.protocol import Act

# Les fichiers de Steam, tels qu'il les écrit (tabulations comprises).
LIBRARYFOLDERS = """"libraryfolders"
{
\t"0"
\t{
\t\t"path"\t\t"@ROOT@"
\t\t"label"\t\t""
\t\t"contentid"\t\t"5372927593270251390"
\t\t"totalsize"\t\t"0"
\t\t"apps"
\t\t{
\t\t\t"228980"\t\t"384253289"
\t\t\t"1245620"\t\t"58999999999"
\t\t}
\t}
\t"1"
\t{
\t\t"path"\t\t"@SECOND@"
\t\t"label"\t\t"Jeux"
\t\t"apps"
\t\t{
\t\t\t"730"\t\t"38000000000"
\t\t}
\t}
}
"""

OLD_LIBRARYFOLDERS = """"LibraryFolders"
{
\t"TimeNextStatsReport"\t\t"1583872437"
\t"ContentStatsID"\t\t"-6130293599214589379"
\t"1"\t\t"@SECOND@"
}
"""


def libraries_text(template, root="", second=""):
    """Remplit un modèle de libraryfolders.vdf (ses accolades interdisent str.format)."""
    return template.replace("@ROOT@", str(root)).replace("@SECOND@", str(second))


def manifest(appid, name, *, flags=4, extra=""):
    return f""""AppState"
{{
\t"appid"\t\t"{appid}"
\t"Universe"\t\t"1"
\t"name"\t\t"{name}"
\t"StateFlags"\t\t"{flags}"
\t"installdir"\t\t"{name}"
\t"LastUpdated"\t\t"1698765432"
\t"SizeOnDisk"\t\t"1000000"
{extra}\t"InstalledDepots"
\t{{
\t\t"1245621"
\t\t{{
\t\t\t"manifest"\t\t"123"
\t\t}}
\t}}
}}
"""


def write(path: Path, content: str | bytes) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content.encode() if isinstance(content, str) else content)
    return path


# --- format VDF ---------------------------------------------------------------------------------


def test_a_modern_library_file_is_parsed_into_nested_dictionaries():
    data = parse_vdf(libraries_text(LIBRARYFOLDERS, "/home/mars/.local/share/Steam", "/mnt/jeux"))
    folders = data["libraryfolders"]
    assert folders["0"]["path"] == "/home/mars/.local/share/Steam"
    assert folders["0"]["apps"] == {"228980": "384253289", "1245620": "58999999999"}
    assert folders["1"]["label"] == "Jeux"


def test_strings_comments_escapes_and_bare_words():
    text = r"""
    // un commentaire
    "racine" { "a" "x\ty\\z \"q\"" // fin de ligne
      "b" valeur_nue
      "c" { }
      "a" "doublon : le dernier gagne"
    }
    """
    assert parse_vdf(text) == {
        "racine": {"a": "doublon : le dernier gagne", "b": "valeur_nue", "c": {}}
    }


def test_the_first_key_is_found_whatever_its_case():
    state = parse_vdf(manifest(1, "X"))["AppState"]
    assert vget(state, "APPID") == "1"
    assert vget(state, "statEFlags") == "4"
    assert vget(state, "absent", "défaut") == "défaut"
    assert vget("pas un dictionnaire", "a") is None


@pytest.mark.parametrize(
    "broken",
    [
        '"a" {',
        '"a" { "b" }',
        '"a" "b" }',
        '"a"',
        '{ "a" "b" }',
        '"a" { "b" { "c" } }',
        '"a" "non terminé',
    ],
)
def test_broken_vdf_raises_a_clear_error(broken):
    with pytest.raises(VdfError):
        parse_vdf(broken)


def test_absurd_nesting_is_refused_instead_of_blowing_the_stack():
    deep = '"a" {' * 200 + "}" * 200
    with pytest.raises(VdfError, match="imbrication"):
        parse_vdf(deep)


def test_an_unreadable_or_oversized_vdf_file_reads_as_empty(tmp_path):
    assert steam.read_vdf(tmp_path / "absent.vdf") == {}
    assert steam.read_vdf(write(tmp_path / "casse.vdf", '"a" {')) == {}
    assert (
        steam.read_vdf(write(tmp_path / "gros.vdf", '"a" "' + "x" * steam.MAX_VDF_BYTES + '"'))
        == {}
    )


# --- dossiers Steam et bibliothèques ------------------------------------------------------------


def test_the_same_steam_folder_reached_by_several_paths_is_one_root(tmp_path):
    real = tmp_path / ".local" / "share" / "Steam"
    real.mkdir(parents=True)
    (tmp_path / ".steam").mkdir()
    (tmp_path / ".steam" / "steam").symlink_to(real)
    (tmp_path / ".steam" / "root").symlink_to(real)
    flatpak = tmp_path / ".var" / "app" / steam.FLATPAK_ID / ".local" / "share" / "Steam"
    flatpak.mkdir(parents=True)
    assert steam_roots(tmp_path) == [real.resolve(), flatpak.resolve()]
    assert steam_roots(tmp_path / "absent") == []


def test_libraries_come_from_libraryfolders_in_both_formats_without_duplicates(tmp_path):
    root = tmp_path / "Steam"
    second = tmp_path / "jeux" / "SteamLibrary"
    (root / "steamapps").mkdir(parents=True)
    (second / "steamapps").mkdir(parents=True)
    write(root / "steamapps" / "libraryfolders.vdf", libraries_text(LIBRARYFOLDERS, root, second))
    assert library_folders(root) == [root.resolve(), second.resolve()]
    write(
        root / "steamapps" / "libraryfolders.vdf", libraries_text(OLD_LIBRARYFOLDERS, second=second)
    )
    assert library_folders(root) == [root.resolve(), second.resolve()]


def test_a_library_that_is_gone_or_has_no_steamapps_is_ignored(tmp_path):
    root = tmp_path / "Steam"
    (root / "steamapps").mkdir(parents=True)
    (tmp_path / "vide").mkdir()
    write(
        root / "steamapps" / "libraryfolders.vdf",
        libraries_text(LIBRARYFOLDERS, "/mnt/disque-debranche/Steam", tmp_path / "vide"),
    )
    assert library_folders(root) == [root.resolve()]
    write(
        root / "steamapps" / "libraryfolders.vdf", '"libraryfolders" { "0" { "path" "relatif" } }'
    )
    assert library_folders(root) == [root.resolve()]


# --- jeux ---------------------------------------------------------------------------------------


def test_an_installed_game_is_read_from_its_manifest(tmp_path):
    path = write(
        tmp_path / "appmanifest_1245620.acf",
        manifest(1245620, "ELDEN RING", extra='\t"LastPlayed"\t\t"1700000000"\n'),
    )
    game = read_manifest(path)
    assert (game.appid, game.name, game.size, game.played) == (
        1245620,
        "ELDEN RING",
        1_000_000,
        1_700_000_000,
    )


def test_without_last_played_the_last_update_is_used(tmp_path):
    game = read_manifest(write(tmp_path / "m.acf", manifest(5, "Jeu")))
    assert game.played == 1_698_765_432


@pytest.mark.parametrize(
    ("appid", "name", "flags"),
    [
        (1493710, "Proton Experimental", 4),
        (1580130, "Proton 9.0 (Beta)", 4),
        (1628350, "Steam Linux Runtime 3.0 (sniper)", 4),
        (228980, "Steamworks Common Redistributables", 4),
        (1070560, "Steam Linux Runtime", 4),
        (990080, "Hogwarts Legacy", 1026),  # en cours de téléchargement : pas installé
        (990081, "Jeu à moitié", 0),
    ],
)
def test_tools_and_games_still_downloading_are_left_out(tmp_path, appid, name, flags):
    assert read_manifest(write(tmp_path / "m.acf", manifest(appid, name, flags=flags))) is None


@pytest.mark.parametrize(
    "text",
    [
        "",
        '"AppState" { }',
        '"AppState" { "appid" "abc" "name" "X" "StateFlags" "4" }',
        '"AppState" { "appid" "5" "StateFlags" "4" }',
        "pas du vdf {{",
    ],
)
def test_a_damaged_manifest_is_skipped(tmp_path, text):
    assert read_manifest(write(tmp_path / "m.acf", text)) is None


def test_a_game_installed_on_two_libraries_is_listed_once_and_names_sort_naturally(tmp_path):
    first, second = tmp_path / "a", tmp_path / "b"
    write(first / "steamapps" / "appmanifest_1.acf", manifest(1, "zèbre"))
    write(first / "steamapps" / "appmanifest_2.acf", manifest(2, "Alpha"))
    write(second / "steamapps" / "appmanifest_1.acf", manifest(1, "zèbre (copie)"))
    write(second / "steamapps" / "appmanifest_3.acf", manifest(3, "beta\x00\x07"))
    games = read_games([first, second])
    assert [g.name for g in games] == ["Alpha", "beta", "zèbre"]
    write(first / "steamapps" / "appmanifest_4.acf", manifest(4, "Épopée"))
    assert [g.name for g in read_games([first, second])] == ["Alpha", "beta", "Épopée", "zèbre"]


def test_the_number_of_games_is_bounded(tmp_path):
    for appid in range(1, steam.MAX_GAMES + 30):
        write(
            tmp_path / "steamapps" / f"appmanifest_{appid}.acf", manifest(appid, f"Jeu {appid:04d}")
        )
    assert len(read_games([tmp_path])) == steam.MAX_GAMES


# --- jaquettes ----------------------------------------------------------------------------------


def test_covers_are_found_in_both_cache_layouts_and_fall_back_to_the_banner(tmp_path):
    cache = tmp_path / "appcache" / "librarycache"
    old = write(cache / "10_library_600x900.jpg", b"x")
    new = write(cache / "20" / "library_600x900.jpg", b"x")
    hashed = write(cache / "30" / ("ab" * 20) / "library_600x900.jpg", b"x")
    banner = write(cache / "40_header.jpg", b"x")
    banner_new = write(cache / "50" / ("cd" * 20) / "header.jpg", b"x")
    assert [find_cover([tmp_path], n) for n in (10, 20, 30, 40, 50)] == [
        old,
        new,
        hashed,
        banner,
        banner_new,
    ]
    assert find_cover([tmp_path], 99) is None
    assert find_cover([], 10) is None


def test_the_portrait_cover_wins_over_the_banner_even_in_another_root(tmp_path):
    one, two = tmp_path / "un", tmp_path / "deux"
    write(one / "appcache" / "librarycache" / "7_header.jpg", b"x")
    portrait = write(two / "appcache" / "librarycache" / "7_library_600x900.jpg", b"x")
    assert find_cover([one, two], 7) == portrait


def test_a_cover_must_be_an_image_and_a_big_one_is_reduced():
    with pytest.raises(ActionError) as caught:
        prepare_cover(b"-----pas une image-----")
    assert caught.value.code == "cover_failed"
    assert prepare_cover(png_bytes())[0] == "image/png" if steam_without_pillow() else True
    pil = pytest.importorskip("PIL.Image")
    big = pil.new("RGB", (600, 900), (200, 30, 30))
    raw = io.BytesIO()
    big.save(raw, "JPEG")
    mime, data = prepare_cover(raw.getvalue())
    assert mime == "image/jpeg"
    with pil.open(io.BytesIO(data)) as reduced:
        assert reduced.size[0] <= steam.COVER_SIZE[0]
        assert reduced.size[1] <= steam.COVER_SIZE[1]


def steam_without_pillow() -> bool:
    try:
        import PIL  # noqa: F401
    except ImportError:
        return True
    return False


# --- jeu en cours -------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("command", "appid"),
    [
        (
            [
                "/home/mars/.local/share/Steam/ubuntu12_32/reaper",
                "SteamLaunch",
                "AppId=1245620",
                "--",
                "proton",
            ],
            1245620,
        ),
        (["reaper", "SteamLaunch", "AppId=730", "--", "/home/x/jeu"], 730),
        (["reaper", "SteamLaunch", "AppId=abc"], None),
        (["reaper", "SteamLaunch"], None),
        (
            ["reaper", "AppId=730"],
            None,
        ),  # il faut SteamLaunch : un autre programme peut dire AppId=
        ([], None),
    ],
)
def test_the_running_game_is_read_from_steams_reaper_command(command, appid):
    assert running_appid(command) == appid


class FakeProcess:
    def __init__(self, name, command=(), broken=False):
        self.info = {"name": name}
        self._command, self._broken = list(command), broken

    def cmdline(self):
        if self._broken:
            raise steam.psutil.NoSuchProcess(1)
        return self._command


def test_the_process_scan_finds_the_client_and_the_running_game(monkeypatch):
    processes = [
        FakeProcess("bash"),
        FakeProcess("steam"),
        FakeProcess("reaper", ["reaper", "SteamLaunch", "AppId=620", "--", "x"]),
        FakeProcess("reaper", broken=True),  # il disparaît pendant la lecture
    ]
    monkeypatch.setattr(steam.psutil, "process_iter", lambda _attrs: processes)
    assert scan_processes() == SteamProcesses(client=True, running=620)
    monkeypatch.setattr(steam.psutil, "process_iter", lambda _attrs: [FakeProcess("bash")])
    assert scan_processes() == SteamProcesses(client=False, running=None)


# --- le fournisseur -----------------------------------------------------------------------------


class RecordingLauncher:
    def __init__(self, fail=None):
        self.calls, self.fail = [], fail

    async def start(self, argv, *, label):
        if self.fail:
            raise LaunchError(self.fail)
        self.calls.append((list(argv), label))


@pytest.fixture
def library(tmp_path):
    root = tmp_path / "home" / ".local" / "share" / "Steam"
    second = tmp_path / "jeux" / "SteamLibrary"
    write(root / "steamapps" / "libraryfolders.vdf", libraries_text(LIBRARYFOLDERS, root, second))
    write(root / "steamapps" / "appmanifest_1245620.acf", manifest(1245620, "ELDEN RING"))
    write(second / "steamapps" / "appmanifest_730.acf", manifest(730, "Counter-Strike 2"))
    write(root / "steamapps" / "appmanifest_1493710.acf", manifest(1493710, "Proton Experimental"))
    write(root / "appcache" / "librarycache" / "1245620_library_600x900.jpg", png_bytes(4, 6))
    state = {"scan": SteamProcesses(False, None)}
    launcher = RecordingLauncher()
    provider = SteamProvider(
        home=tmp_path / "home",
        scanner=lambda: state["scan"],
        launcher=launcher,
        which=lambda name: f"/usr/bin/{name}" if name == "steam" else None,
        interval=0.05,
    )
    return provider, launcher, state, root, second


async def test_the_snapshot_lists_installed_games_without_tools_or_paths(library):
    provider, _launcher, _state, root, _second = library
    await provider.refresh()
    snapshot = provider.snapshot()
    assert [(g["appid"], g["name"]) for g in snapshot["games"]] == [
        (730, "Counter-Strike 2"),
        (1245620, "ELDEN RING"),
    ]
    assert snapshot["running"] is None
    assert snapshot["client"] is False
    assert "unavailable" not in snapshot
    assert str(root) not in str(snapshot)


async def test_without_steam_the_snapshot_says_so(tmp_path):
    provider = SteamProvider(home=tmp_path, scanner=lambda: SteamProcesses(False, None))
    await provider.refresh()
    snapshot = provider.snapshot()
    assert "Steam n'est pas installé" in snapshot["unavailable"]
    assert snapshot["games"] == []


async def test_a_game_installed_later_appears_and_an_unchanged_library_is_not_read_again(
    library, monkeypatch
):
    provider, _launcher, _state, _root, second = library
    await provider.refresh()
    reads = []
    original = steam.read_games
    monkeypatch.setattr(steam, "read_games", lambda libs: reads.append(1) or original(libs))
    await provider.refresh()
    assert reads == []  # rien n'a bougé : aucune relecture
    write(second / "steamapps" / "appmanifest_620.acf", manifest(620, "Portal 2"))
    await provider.refresh()
    assert reads == [1]
    assert "Portal 2" in [g["name"] for g in provider.snapshot()["games"]]


async def test_the_cover_is_sent_once_as_base64(library):
    provider, _launcher, _state, _root, _second = library
    reply = await provider.cover(1245620)
    assert reply["mime"] in ("image/png", "image/jpeg")
    assert base64.b64decode(reply["data"])
    assert await provider.cover(1245620) == reply  # mémorisée
    with pytest.raises(ActionError) as caught:
        await provider.cover(730)  # pas de jaquette dans le cache
    assert caught.value.code == "no_cover"
    with pytest.raises(ActionError) as caught:
        await provider.cover(1493710)  # un outil n'est pas un jeu de la liste
    assert caught.value.code == "not_found"


async def test_launching_a_game_goes_through_the_steam_url(library):
    provider, launcher, _state, _root, _second = library
    assert await provider.launch(1245620) == {"started": True, "name": "ELDEN RING"}
    assert launcher.calls == [(["steam", "steam://rungameid/1245620"], "ELDEN RING")]


@pytest.mark.parametrize(
    ("which", "flatpak", "expected"),
    [
        ({"steam", "xdg-open"}, False, ["steam", "steam://rungameid/9"]),
        (
            {"flatpak", "xdg-open"},
            True,
            ["flatpak", "run", steam.FLATPAK_ID, "steam://rungameid/9"],
        ),
        ({"flatpak", "xdg-open"}, False, ["xdg-open", "steam://rungameid/9"]),
        ({"xdg-open"}, True, ["xdg-open", "steam://rungameid/9"]),
    ],
)
def test_the_steam_command_depends_on_how_steam_is_installed(tmp_path, which, flatpak, expected):
    roots = (
        [tmp_path / ".var" / "app" / steam.FLATPAK_ID / ".local" / "share" / "Steam"]
        if flatpak
        else [tmp_path / "Steam"]
    )
    provider = SteamProvider(roots=roots, which=lambda n: f"/usr/bin/{n}" if n in which else None)
    provider._roots = roots
    assert provider._command(9) == expected


def test_no_way_to_start_steam_is_reported(tmp_path):
    provider = SteamProvider(roots=[tmp_path], which=lambda _n: None)
    with pytest.raises(ActionError, match="introuvable"):
        provider._command(9)


async def test_only_installed_games_can_be_launched(library):
    provider, launcher, _state, _root, _second = library
    for appid in (1493710, 999999, 1):
        with pytest.raises(ActionError) as caught:
            await provider.launch(appid)
        assert caught.value.code == "not_found"
    assert launcher.calls == []


async def test_a_failing_launcher_is_reported(library):
    provider, _launcher, _state, _root, _second = library
    provider._launcher = RecordingLauncher(fail="Le lancement de « ELDEN RING » a échoué (code 1).")
    with pytest.raises(ActionError) as caught:
        await provider.launch(1245620)
    assert caught.value.code == "launch_failed"


async def test_the_running_game_is_followed_while_the_page_is_open(library):
    provider, _launcher, state, _root, _second = library
    published = []

    async def publish(data):
        published.append(data)
        return True

    task = asyncio.create_task(provider.run(publish))
    try:
        await eventually(lambda: published)
        assert published[-1]["running"] is None
        state["scan"] = SteamProcesses(True, 1245620)
        await eventually(lambda: published[-1]["running"] == 1245620)
        assert published[-1]["client"] is True
        state["scan"] = SteamProcesses(True, None)
        await eventually(lambda: published[-1]["running"] is None)
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
    assert provider.topic == "steam"
    assert provider.lazy is True


# --- les actions, telles que le dock les envoie -------------------------------------------------


@pytest.fixture
def registry(library):
    provider, launcher, *_ = library
    reg = ActionRegistry()
    register_steam_actions(reg, provider)
    reg.launcher = launcher
    return reg


async def call(reg, name, params):
    return await reg.invoke(Act("t1", name, params), ActionContext(1, "127.0.0.1"))


async def test_steam_actions_are_not_sensitive_and_work(registry):
    assert registry.names == ["steam.cover", "steam.launch"]
    assert not any(registry.is_sensitive(n) for n in registry.names)
    assert (await call(registry, "steam.launch", {"appid": 730}))["ok"] is True
    assert (await call(registry, "steam.cover", {"appid": 1245620}))["ok"] is True


@pytest.mark.parametrize(
    "params",
    [
        {"appid": "730"},
        {"appid": 0},
        {"appid": -5},
        {"appid": 7.5},
        {"appid": True},
        {"appid": 10**12},
        {},
        {"appid": 730, "cmd": "x"},
        {"appid": None},
    ],
)
async def test_malformed_requests_never_reach_the_provider(registry, params):
    reply = await call(registry, "steam.launch", params)
    assert reply["e"]["code"] == "bad_params"
    assert registry.launcher.calls == []
