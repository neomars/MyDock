"""Applications : fichiers .desktop, icônes, lancement. Tout est lu dans de vrais fichiers d'un dossier temporaire."""

import base64
from pathlib import Path

import pytest

from mydock_agent.collectors import desktop
from mydock_agent.collectors.desktop import (
    AppsProvider,
    IconFinder,
    current_desktops,
    current_locales,
    data_dirs,
    icon_id,
    is_svg,
    is_visible,
    localized,
    parse_entry,
    read_icon,
    register_apps_actions,
    scan_applications,
    split_list,
)
from mydock_agent.launcher import LaunchError
from mydock_agent.testing import eventually, png_bytes
from mydock_common.actions import ActionContext, ActionError, ActionRegistry
from mydock_common.protocol import Act

SVG = (
    b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 8 8"><rect width="8" height="8"/></svg>'
)


def write(path: Path, content: str | bytes) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content.encode() if isinstance(content, str) else content)
    return path


def entry(name: str, **extra: str) -> str:
    fields = {"Type": "Application", "Name": name, "Exec": "true", **extra}
    return "[Desktop Entry]\n" + "".join(f"{k}={v}\n" for k, v in fields.items())


def which_all(name: str) -> str:
    return f"/usr/bin/{name}"


def which_none(_name: str) -> None:
    return None


# --- lecture d'un fichier -----------------------------------------------------------------------


def test_only_the_desktop_entry_group_is_read_and_values_are_unescaped():
    text = """# commentaire
[Desktop Entry]
Type = Application
Name=Lecteur\\sde musique
Name[fr]=Lecteur de musique
Comment=Une ligne\\nsur deux
Exec=spotify %U
Name=Doublon ignoré

[Desktop Action new-window]
Name=Nouvelle fenêtre
Exec=spotify --new
"""
    entry_ = parse_entry(text)
    assert entry_["Type"] == "Application"
    assert entry_["Name"] == "Lecteur de musique"
    assert entry_["Comment"] == "Une ligne\nsur deux"
    assert entry_["Exec"] == "spotify %U"
    assert "Nouvelle fenêtre" not in entry_.values()


def test_the_name_follows_the_session_language():
    entry_ = {"Name": "Files", "Name[fr]": "Fichiers", "Name[fr_FR]": "Fichiers (France)"}
    assert localized(entry_, "Name", ["fr_FR", "fr"]) == "Fichiers (France)"
    assert localized(entry_, "Name", ["fr"]) == "Fichiers"
    assert localized(entry_, "Name", ["de"]) == "Files"
    assert localized(entry_, "Comment", ["fr"]) is None


def test_lists_split_on_unescaped_semicolons_only():
    assert split_list("GNOME;KDE;") == ["GNOME", "KDE"]
    assert split_list("a\\;b;c") == ["a;b", "c"]
    assert split_list("") == []
    assert split_list(None) == []


@pytest.mark.parametrize(
    ("extra", "which", "visible"),
    [
        ({}, which_all, True),
        ({"NoDisplay": "true"}, which_all, False),
        ({"Hidden": "true"}, which_all, False),
        ({"NoDisplay": "false"}, which_all, True),
        ({"OnlyShowIn": "KDE;"}, which_all, False),
        ({"OnlyShowIn": "KDE;GNOME;"}, which_all, True),
        ({"NotShowIn": "GNOME;"}, which_all, False),
        ({"NotShowIn": "KDE;"}, which_all, True),
        ({"TryExec": "absent"}, which_none, False),
        ({"TryExec": "present"}, which_all, True),
        ({"Type": "Link"}, which_all, False),
        ({"Type": "Directory"}, which_all, False),
    ],
)
def test_visibility_follows_the_desktop_rules(extra, which, visible):
    parsed = parse_entry(entry("Appli", **extra))
    assert is_visible(parsed, ["ubuntu", "GNOME"], which) is visible


def test_an_entry_without_name_or_command_is_not_an_application():
    assert not is_visible({"Type": "Application", "Exec": "x"}, [], which_all)
    assert not is_visible({"Type": "Application", "Name": "X"}, [], which_all)


# --- environnement ------------------------------------------------------------------------------


def test_locales_come_from_the_session_most_wanted_first():
    assert current_locales({"LANGUAGE": "fr_FR:en", "LANG": "C"}) == ["fr_FR", "fr", "en"]
    assert current_locales({"LANG": "fr_FR.UTF-8"}) == ["fr_FR", "fr"]
    assert current_locales({"LC_ALL": "de_DE@euro", "LANG": "fr_FR"}) == [
        "de_DE",
        "de",
        "fr_FR",
        "fr",
    ]
    assert current_locales({"LANG": "C"}, default_file=Path("/inexistant")) == []


def test_a_user_service_without_lang_falls_back_to_the_system_default(tmp_path):
    default = write(tmp_path / "locale", 'LANG="fr_FR.UTF-8"\nLC_TIME=en_GB.UTF-8\n')
    assert current_locales({}, default_file=default) == ["fr_FR", "fr"]


def test_desktops_split_like_xdg_current_desktop():
    assert current_desktops({"XDG_CURRENT_DESKTOP": "ubuntu:GNOME"}) == ["ubuntu", "GNOME"]
    assert current_desktops({}) == []


def test_data_dirs_put_the_user_first_then_the_system_then_flatpak_and_snap(tmp_path):
    env = {"XDG_DATA_DIRS": "/usr/local/share:/usr/share:/usr/share"}
    dirs = data_dirs(env, tmp_path)
    assert dirs[0] == tmp_path / ".local" / "share"
    assert dirs[1:3] == [Path("/usr/local/share"), Path("/usr/share")]
    assert Path("/var/lib/flatpak/exports/share") in dirs
    assert tmp_path / ".local" / "share" / "flatpak" / "exports" / "share" in dirs
    assert Path("/var/lib/snapd/desktop") in dirs
    assert len(dirs) == len(set(dirs))  # pas de doublon
    assert data_dirs({"XDG_DATA_HOME": "/donnees"}, tmp_path)[0] == Path("/donnees")


# --- parcours des dossiers ----------------------------------------------------------------------


def names(apps):
    return [a.name for a in apps]


def scan(directories, **kwargs):
    return scan_applications(
        directories,
        desktops=["GNOME"],
        locales=kwargs.pop("locales", ["fr"]),
        which=which_all,
        **kwargs,
    )


def test_applications_are_sorted_by_name_and_get_the_desktop_file_id(tmp_path):
    share = tmp_path / "share" / "applications"
    write(share / "b.desktop", entry("beta"))
    write(share / "a.desktop", entry("Écrire"))
    write(share / "kde4" / "k.desktop", entry("Alpha"))
    apps = scan([share])
    assert names(apps) == ["Alpha", "beta", "Écrire"]
    assert [a.id for a in apps] == ["kde4-k.desktop", "b.desktop", "a.desktop"]


def test_names_sort_the_french_way_accents_do_not_push_a_name_after_the_z(tmp_path):
    share = tmp_path / "apps"
    for index, name in enumerate(("Zèbre", "Étoile", "alpha", "Écrire", "Eau", "ÀVoir", "Oiseau")):
        write(share / f"a{index}.desktop", entry(name))
    assert names(scan([share])) == ["alpha", "ÀVoir", "Eau", "Écrire", "Étoile", "Oiseau", "Zèbre"]
    assert desktop.sort_key("Éditeur") == desktop.sort_key("editeur") == "editeur"


def test_the_users_version_wins_and_a_hidden_one_hides_the_systems(tmp_path):
    user, system = tmp_path / "user", tmp_path / "system"
    write(user / "navigateur.desktop", entry("Mon navigateur"))
    write(system / "navigateur.desktop", entry("Navigateur du système"))
    write(user / "cache.desktop", entry("Caché", Hidden="true"))
    write(system / "cache.desktop", entry("Pourtant visible"))
    write(system / "autre.desktop", entry("Autre"))
    assert names(scan([user, system])) == ["Autre", "Mon navigateur"]


def test_names_use_the_session_language_and_icons_are_kept(tmp_path):
    share = tmp_path / "apps"
    write(
        share / "f.desktop",
        entry("Files", **{"Name[fr]": "Fichiers", "GenericName": "Gestionnaire", "Icon": "folder"}),
    )
    (app,) = scan([share])
    assert (app.name, app.generic, app.icon) == ("Fichiers", "Gestionnaire", "folder")
    assert app.icon_id == icon_id("folder")
    (app,) = scan([share], locales=["de"])
    assert app.name == "Files"


def test_a_symlinked_desktop_file_is_followed_like_flatpak_exports(tmp_path):
    real = write(tmp_path / "flatpak" / "app" / "org.x.desktop", entry("Appli Flatpak"))
    exports = tmp_path / "exports"
    exports.mkdir()
    (exports / "org.x.desktop").symlink_to(real)
    assert names(scan([exports])) == ["Appli Flatpak"]


def test_unreadable_oversized_and_control_character_entries_are_handled(tmp_path):
    share = tmp_path / "apps"
    write(share / "gros.desktop", entry("Gros") + "# " + "x" * (desktop.MAX_ENTRY_BYTES + 10))
    write(share / "bizarre.desktop", entry("Nom\x07avec\x00contrôles"))
    write(share / "binaire.desktop", b"\xff\xfe\x00 pas du texte")
    (share / "dossier.desktop").mkdir()
    write(share / "vide-nom.desktop", entry("   "))
    assert names(scan([share])) == ["Nom avec contrôles"]


def test_missing_directories_are_skipped(tmp_path):
    assert scan([tmp_path / "absent"]) == []


# --- icônes -------------------------------------------------------------------------------------


def make_theme(share: Path, theme: str, inherits: str | None = None) -> Path:
    base = share / "icons" / theme
    write(
        base / "index.theme",
        f"[Icon Theme]\nName={theme}\n" + (f"Inherits={inherits}\n" if inherits else ""),
    )
    return base


def test_icons_are_found_in_the_theme_then_its_parents_then_hicolor_then_pixmaps(tmp_path):
    share = tmp_path / "share"
    yaru = make_theme(share, "Yaru", inherits="Adwaita,hicolor")
    adwaita = make_theme(share, "Adwaita")
    hicolor = make_theme(share, "hicolor")
    write(yaru / "256x256" / "apps" / "firefox.png", png_bytes())
    write(adwaita / "scalable" / "apps" / "gimp.svg", SVG)
    write(hicolor / "48x48" / "apps" / "vlc.png", png_bytes())
    write(share / "pixmaps" / "ancien.png", png_bytes())
    finder = IconFinder([share], "Yaru", tmp_path)
    assert finder.find("firefox") == yaru / "256x256" / "apps" / "firefox.png"
    assert finder.find("gimp") == adwaita / "scalable" / "apps" / "gimp.svg"
    assert finder.find("vlc") == hicolor / "48x48" / "apps" / "vlc.png"
    assert finder.find("ancien") == share / "pixmaps" / "ancien.png"
    assert finder.find("inexistante") is None


def test_the_size_closest_to_the_screen_is_preferred_and_png_beats_svg(tmp_path):
    share = tmp_path / "share"
    base = make_theme(share, "hicolor")
    for size in (16, 48, 128, 512):
        write(base / f"{size}x{size}" / "apps" / "x.png", png_bytes())
    write(base / "scalable" / "apps" / "x.svg", SVG)
    assert IconFinder([share], None, tmp_path).find("x") == base / "128x128" / "apps" / "x.png"


def test_an_absolute_icon_path_is_used_as_is_and_hostile_names_are_refused(tmp_path):
    share = tmp_path / "share"
    make_theme(share, "hicolor")
    absolute = write(tmp_path / "snap" / "app" / "icon.png", png_bytes())
    finder = IconFinder([share], None, tmp_path)
    assert finder.find(str(absolute)) == absolute
    assert finder.find(str(tmp_path / "absent.png")) is None
    for hostile in ("../../etc/passwd", "a/b", "x y", "", "-rf", "a" * 200):
        assert finder.find(hostile) is None


def test_an_icon_must_really_be_an_image(tmp_path):
    secret = write(
        tmp_path / "secret.png", "-----BEGIN PRIVATE KEY-----\n(pas un secret : un essai)\n"
    )
    with pytest.raises(ActionError) as caught:
        read_icon(secret)
    assert caught.value.code == "icon_failed"
    assert read_icon(write(tmp_path / "ok.png", png_bytes()))[0] == "image/png"
    assert read_icon(write(tmp_path / "ok.svg", SVG))[0] == "image/svg+xml"
    assert is_svg(b"\xef\xbb\xbf  \n" + SVG)
    assert not is_svg(b"<html><body>pas un svg</body></html>")


def test_a_huge_icon_is_refused_and_a_big_png_is_reduced(tmp_path):
    huge = write(tmp_path / "huge.png", png_bytes() + b"\0" * desktop.MAX_ICON_BYTES)
    with pytest.raises(ActionError):
        read_icon(huge)
    pil = pytest.importorskip("PIL.Image")
    import io

    noise = pil.effect_noise((256, 256), 80).convert("RGB")
    raw = io.BytesIO()
    noise.save(raw, "PNG")
    assert len(raw.getvalue()) > desktop.ICON_SEND_LIMIT
    mime, data = read_icon(write(tmp_path / "big.png", raw.getvalue()))
    assert mime == "image/png"
    with pil.open(io.BytesIO(data)) as reduced:
        assert max(reduced.size) <= desktop.ICON_SIZE


# --- le fournisseur -----------------------------------------------------------------------------


class RecordingLauncher:
    def __init__(self, fail: str | None = None):
        self.calls: list[tuple[list[str], str]] = []
        self.fail = fail

    async def start(self, argv, *, label):
        if self.fail:
            raise LaunchError(self.fail)
        self.calls.append((list(argv), label))


@pytest.fixture
def world(tmp_path):
    share = tmp_path / "share"
    write(share / "applications" / "firefox.desktop", entry("Firefox", Icon="firefox"))
    write(share / "applications" / "calc.desktop", entry("Calculatrice", Icon="calc"))
    write(share / "applications" / "sans-icone.desktop", entry("Sans icône"))
    base = make_theme(share, "hicolor")
    write(base / "128x128" / "apps" / "firefox.png", png_bytes(8, 8))
    write(base / "scalable" / "apps" / "calc.svg", SVG)
    launcher = RecordingLauncher()
    provider = AppsProvider(
        roots=[share],
        launcher=launcher,
        env={"XDG_CURRENT_DESKTOP": "GNOME", "LANG": "fr_FR.UTF-8", "PATH": "/usr/bin"},
        home=tmp_path,
        theme=lambda: "hicolor",
    )
    return provider, launcher, share


async def test_the_snapshot_lists_applications_with_opaque_icon_ids(world):
    provider, _launcher, _share = world
    await provider.refresh()
    snapshot = provider.snapshot()
    assert snapshot["count"] == 3
    assert [a["name"] for a in snapshot["apps"]] == ["Calculatrice", "Firefox", "Sans icône"]
    firefox = next(a for a in snapshot["apps"] if a["id"] == "firefox.desktop")
    assert firefox["icon"] == icon_id("firefox")
    assert len(firefox["icon"]) == 16
    assert next(a for a in snapshot["apps"] if a["name"] == "Sans icône")["icon"] is None
    assert "firefox.png" not in str(snapshot)  # aucun chemin de fichier ne sort de l'agent


async def test_an_icon_is_sent_once_as_base64_and_then_served_from_memory(world):
    provider, _launcher, share = world
    await provider.refresh()
    reply = await provider.icon(icon_id("firefox"))
    assert reply["mime"] == "image/png"
    assert base64.b64decode(reply["data"]).startswith(b"\x89PNG")
    (share / "icons" / "hicolor" / "128x128" / "apps" / "firefox.png").unlink()
    assert await provider.icon(icon_id("firefox")) == reply  # mémorisée
    svg = await provider.icon(icon_id("calc"))
    assert svg["mime"] == "image/svg+xml"


async def test_an_unknown_or_unresolvable_icon_is_an_error_not_a_crash(world):
    provider, _launcher, _share = world
    await provider.refresh()
    with pytest.raises(ActionError) as caught:
        await provider.icon("0123456789abcdef")
    assert caught.value.code == "unknown_icon"
    provider._icons["feedfeedfeedfeed"] = "icone-absente"  # une icône que le thème n'a pas
    with pytest.raises(ActionError) as caught:
        await provider.icon("feedfeedfeedfeed")
    assert caught.value.code == "icon_failed"


async def test_launching_asks_gio_to_launch_the_desktop_file(world):
    provider, launcher, share = world
    reply = await provider.launch("firefox.desktop")
    assert reply == {"started": True, "name": "Firefox"}
    assert launcher.calls == [
        (["gio", "launch", str(share / "applications" / "firefox.desktop")], "Firefox")
    ]


async def test_only_listed_applications_can_be_launched(world):
    provider, launcher, share = world
    write(share / "applications" / "cache.desktop", entry("Caché", NoDisplay="true"))
    for wanted in ("cache.desktop", "../../../bin/sh.desktop", "absente.desktop", "firefox"):
        with pytest.raises(ActionError) as caught:
            await provider.launch(wanted)
        assert caught.value.code == "not_found"
    assert launcher.calls == []


async def test_a_failing_launcher_is_reported_with_its_own_words(world):
    provider, _launcher, _share = world
    provider._launcher = RecordingLauncher(fail="« gio » est introuvable sur ce PC.")
    with pytest.raises(ActionError) as caught:
        await provider.launch("firefox.desktop")
    assert caught.value.code == "launch_failed"
    assert "gio" in caught.value.message


async def test_new_and_removed_applications_are_noticed_on_the_next_look(world):
    provider, _launcher, share = world
    assert await provider.refresh() is True
    assert await provider.refresh() is False  # rien n'a changé : pas de relecture
    write(share / "applications" / "nouvelle.desktop", entry("Nouvelle"))
    assert await provider.refresh() is True
    assert "Nouvelle" in [a["name"] for a in provider.snapshot()["apps"]]
    (share / "applications" / "nouvelle.desktop").unlink()
    assert await provider.refresh() is True
    assert "Nouvelle" not in [a["name"] for a in provider.snapshot()["apps"]]


async def test_the_provider_publishes_the_list_when_it_starts(world):
    import asyncio

    provider, _launcher, _share = world
    published = []

    async def publish(data):
        published.append(data)
        return True

    task = asyncio.create_task(provider.run(publish))
    try:
        await eventually(lambda: published)
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
    assert published[0]["count"] == 3
    assert provider.topic == "apps"
    assert provider.lazy is True


# --- les actions, telles que le dock les envoie -------------------------------------------------


@pytest.fixture
def registry(world):
    provider, launcher, _share = world
    reg = ActionRegistry()
    register_apps_actions(reg, provider)
    reg.launcher = launcher
    return reg


async def call(reg, name, params):
    return await reg.invoke(Act("t1", name, params), ActionContext(1, "127.0.0.1"))


async def test_apps_actions_are_not_sensitive_and_work(registry):
    assert registry.names == ["apps.icon", "apps.launch"]
    assert not any(registry.is_sensitive(n) for n in registry.names)
    reply = await call(registry, "apps.launch", {"id": "calc.desktop"})
    assert reply["ok"] is True
    assert reply["r"]["name"] == "Calculatrice"
    reply = await call(registry, "apps.icon", {"icon": icon_id("calc")})
    assert reply["r"]["mime"] == "image/svg+xml"


@pytest.mark.parametrize(
    ("name", "params"),
    [
        ("apps.launch", {"id": "../x.desktop"}),
        ("apps.launch", {"id": "x"}),
        ("apps.launch", {"id": "x y.desktop"}),
        ("apps.launch", {"id": "a" * 300 + ".desktop"}),
        ("apps.launch", {"id": 5}),
        ("apps.launch", {"id": "ok.desktop", "extra": "rm -rf /"}),
        ("apps.launch", {}),
        ("apps.icon", {"icon": "../../etc/passwd"}),
        ("apps.icon", {"icon": "ABCDEF0123456789"}),
        ("apps.icon", {"icon": "0123456789abcde"}),
        ("apps.icon", {"icon": "firefox", "path": "/etc/shadow"}),
    ],
)
async def test_malformed_requests_never_reach_the_provider(registry, name, params):
    reply = await call(registry, name, params)
    assert reply["ok"] is False
    assert reply["e"]["code"] == "bad_params"
    assert registry.launcher.calls == []
