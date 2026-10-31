"""LibreOffice : documents récents (liste de LibreOffice, liste du bureau), nouveau document, ouverture."""

import os
import time
from pathlib import Path

import pytest

from mydock_agent.collectors import office
from mydock_agent.collectors.office import (
    OfficeProvider,
    collect_recent,
    detect_command,
    kind_of,
    libreoffice_profiles,
    read_libreoffice_history,
    read_recently_used,
    recent_id,
    recently_used_files,
    register_office_actions,
    shorten,
    url_to_path,
)
from mydock_agent.launcher import LaunchError
from mydock_common.actions import ActionContext, ActionError, ActionRegistry
from mydock_common.protocol import Act

XCU_HEAD = (
    '<?xml version="1.0" encoding="UTF-8"?>\n'
    '<oor:items xmlns:oor="http://openoffice.org/2001/registry" '
    'xmlns:xs="http://www.w3.org/2001/XMLSchema" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">\n'
    '<item oor:path="/org.openoffice.Office.Common/Misc"><prop oor:name="FirstRun" oor:op="fuse">'
    "<value>false</value></prop></item>\n"
)
PICK = "/org.openoffice.Office.Histories/Histories/['PickList']"


def xcu(items: list[tuple[str, str]], order: list[str]) -> str:
    """Une liste d'ouverture de LibreOffice comme le programme l'écrit : un nœud par adresse."""
    body = XCU_HEAD
    for url, filter_name in items:
        body += (
            f'<item oor:path="{PICK}/ItemList"><node oor:name="{url}" oor:op="replace">'
            f'<prop oor:name="Filter" oor:op="fuse"><value>{filter_name}</value></prop>'
            '<prop oor:name="Title" oor:op="fuse"><value>titre</value></prop>'
            '<prop oor:name="Thumbnail" oor:op="fuse"><value>' + "QUJD" * 5000 + "</value></prop>"
            "</node></item>\n"
        )
    for position, url in enumerate(order):
        body += (
            f'<item oor:path="{PICK}/OrderList"><node oor:name="{position}" oor:op="replace">'
            f'<prop oor:name="HistoryItemRef" oor:op="fuse"><value>{url}</value></prop></node></item>\n'
        )
    return body + "</oor:items>\n"


def xbel(*bookmarks: tuple[str, str, str, str]) -> str:
    """(adresse, date, type MIME, programme)"""
    body = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<xbel version="1.0" xmlns:bookmark="http://www.freedesktop.org/standards/desktop-bookmarks" '
        'xmlns:mime="http://www.freedesktop.org/standards/shared-mime-info">\n'
    )
    for href, modified, mime, app in bookmarks:
        body += (
            f'  <bookmark href="{href}" added="{modified}" modified="{modified}" visited="{modified}">\n'
            f'    <info><metadata owner="http://freedesktop.org"><mime:mime-type type="{mime}"/>'
            f'<bookmark:applications><bookmark:application name="{app}" exec="x" modified="{modified}" count="1"/>'
            "</bookmark:applications></metadata></info>\n  </bookmark>\n"
        )
    return body + "</xbel>\n"


def write(path: Path, content: str = "") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


def url(path: Path) -> str:
    return f"file://{path.as_posix()}"


# --- type de document et adresses ----------------------------------------------------------------


@pytest.mark.parametrize(
    ("name", "filter_name", "kind"),
    [
        ("a.odt", None, "writer"),
        ("a.DOCX", None, "writer"),
        ("a.rtf", None, "writer"),
        ("a.ods", None, "calc"),
        ("a.xlsx", None, "calc"),
        ("a.csv", None, "calc"),
        ("a.odp", None, "impress"),
        ("a.pptx", None, "impress"),
        ("a.odg", None, "draw"),
        ("a.pdf", None, "draw"),
        ("sans-extension", "writer8", "writer"),
        ("sans-extension", "MS Excel 97", "calc"),
        ("sans-extension", "impress8", "impress"),
        ("sans-extension", "draw_pdf_import", "draw"),
        ("sans-extension", "mystère", "other"),
        ("a.zip", None, "other"),
    ],
)
def test_the_libreoffice_module_follows_the_extension_then_the_filter(name, filter_name, kind):
    assert kind_of(Path(name), filter_name) == kind


@pytest.mark.parametrize(
    ("address", "path"),
    [
        (
            "file:///home/mars/Mes%20documents/rapport.odt",
            Path("/home/mars/Mes documents/rapport.odt"),
        ),
        ("file://localhost/tmp/a.odt", Path("/tmp/a.odt")),
        ("file:///home/mars/%C3%89t%C3%A9.odt", Path("/home/mars/Été.odt")),
        ("http://exemple.org/a.odt", None),
        ("file://autre-machine/partage/a.odt", None),
        ("smb://nas/a.odt", None),
        ("a.odt", None),
        ("", None),
    ],
)
def test_only_local_file_addresses_become_paths(address, path):
    assert url_to_path(address) == path


def test_the_displayed_folder_is_short(tmp_path):
    assert shorten(tmp_path / "Documents" / "Travail" / "a.odt", tmp_path) == "~/Documents/Travail"
    assert shorten(tmp_path / "a.odt", tmp_path) == "~"
    assert shorten(Path("/mnt/nas/partage/a.odt"), tmp_path) == "/mnt/nas/partage"


# --- la liste de LibreOffice --------------------------------------------------------------------


def test_the_libreoffice_list_is_read_in_its_own_order_thumbnails_ignored(tmp_path):
    profile = write(
        tmp_path / "registrymodifications.xcu",
        xcu(
            [
                ("file:///a/ancien.odt", "writer8"),
                ("file:///a/budget.ods", "calc8"),
                ("file:///a/oublie.odp", "impress8"),
            ],
            order=["file:///a/budget.ods", "file:///a/ancien.odt"],
        ),
    )
    assert read_libreoffice_history(profile) == [
        ("file:///a/budget.ods", "calc8"),
        ("file:///a/ancien.odt", "writer8"),
        ("file:///a/oublie.odp", "impress8"),  # dans la liste mais pas dans l'ordre : à la fin
    ]


def test_references_to_unknown_items_and_other_lists_are_ignored(tmp_path):
    other = "/org.openoffice.Office.Histories/Histories/['URLHistory']"
    body = xcu([("file:///a/x.odt", "writer8")], order=["file:///a/fantome.odt", "file:///a/x.odt"])
    body = body.replace(
        "</oor:items>",
        f'<item oor:path="{other}/ItemList"><node oor:name="file:///a/web.odt" oor:op="replace"/></item></oor:items>',
    )
    assert read_libreoffice_history(write(tmp_path / "r.xcu", body)) == [
        ("file:///a/x.odt", "writer8")
    ]


@pytest.mark.parametrize(
    "content", ["", "pas du xml", "<oor:items>", '<?xml version="1.0"?><autre/>']
)
def test_a_missing_or_damaged_libreoffice_file_reads_as_empty(tmp_path, content):
    assert read_libreoffice_history(tmp_path / "absent.xcu") == []
    assert read_libreoffice_history(write(tmp_path / "abime.xcu", content)) == []


def test_an_oversized_libreoffice_file_is_not_read(tmp_path, monkeypatch):
    monkeypatch.setattr(office, "MAX_XML_BYTES", 100)
    profile = write(tmp_path / "r.xcu", xcu([("file:///a/x.odt", "writer8")], ["file:///a/x.odt"]))
    assert read_libreoffice_history(profile) == []


# --- la liste du bureau -------------------------------------------------------------------------


def test_the_desktop_list_keeps_office_documents_newest_first(tmp_path):
    odt, ods = (
        "application/vnd.oasis.opendocument.text",
        "application/vnd.oasis.opendocument.spreadsheet",
    )
    path = write(
        tmp_path / "recently-used.xbel",
        xbel(
            ("file:///a/vieux.odt", "2026-01-01T00:00:00Z", odt, "LibreOffice Writer"),
            ("file:///a/photo.jpg", "2026-10-30T00:00:00Z", "image/jpeg", "Visionneuse d'images"),
            ("file:///a/recent.ods", "2026-10-01T00:00:00Z", ods, "LibreOffice Calc"),
            (
                "file:///a/sans-mime.xyz",
                "2026-06-01T00:00:00Z",
                "application/x-inconnu",
                "LibreOffice Writer",
            ),
            (
                "file:///a/word.docx",
                "2026-09-01T00:00:00Z",
                "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                "Autre",
            ),
        ),
    )
    assert [href for href, _ in read_recently_used(path)] == [
        "file:///a/recent.ods",
        "file:///a/word.docx",
        "file:///a/sans-mime.xyz",
        "file:///a/vieux.odt",
    ]


def test_a_damaged_desktop_list_reads_as_empty(tmp_path):
    assert read_recently_used(tmp_path / "absent") == []
    assert read_recently_used(write(tmp_path / "x", "<xbel>")) == []


# --- les documents récents ----------------------------------------------------------------------


def make_documents(home: Path, *names: str) -> list[Path]:
    paths = []
    for index, name in enumerate(names):
        path = write(home / "Documents" / name, "contenu")
        stamp = time.time() - 3600 * (index + 1)
        os.utime(path, (stamp, stamp))
        paths.append(path)
    return paths


def test_recents_merge_both_lists_skip_what_is_gone_and_never_repeat_a_document(tmp_path):
    a, b, c = make_documents(tmp_path, "a.odt", "b.ods", "c.odp")
    profile = write(
        tmp_path / "profile.xcu",
        xcu(
            [
                (url(b), "calc8"),
                (url(a), "writer8"),
                (url(tmp_path / "Documents" / "disparu.odt"), "writer8"),
                ("http://x/y.odt", "writer8"),
            ],
            order=[url(b), url(a)],
        ),
    )
    odp = "application/vnd.oasis.opendocument.presentation"
    xbel_file = write(
        tmp_path / "recent.xbel",
        xbel(
            (
                url(a),
                "2026-10-01T00:00:00Z",
                "application/vnd.oasis.opendocument.text",
                "LibreOffice Writer",
            ),
            (url(c), "2026-09-01T00:00:00Z", odp, "LibreOffice Impress"),
        ),
    )
    recents = collect_recent([profile], [xbel_file], tmp_path)
    assert [r.path for r in recents] == [b, a, c]
    assert [r.kind for r in recents] == ["calc", "writer", "impress"]
    assert recents[0].id == recent_id(url(b))
    assert recents[0].modified is not None


def test_a_directory_named_like_a_document_is_not_a_recent_document(tmp_path):
    (tmp_path / "Documents" / "dossier.odt").mkdir(parents=True)
    profile = write(
        tmp_path / "p.xcu", xcu([(url(tmp_path / "Documents" / "dossier.odt"), "writer8")], [])
    )
    assert collect_recent([profile], [], tmp_path) == []


def test_only_the_most_recent_documents_are_kept(tmp_path):
    paths = make_documents(tmp_path, *[f"d{i:02d}.odt" for i in range(30)])
    profile = write(
        tmp_path / "p.xcu", xcu([(url(p), "writer8") for p in paths], [url(p) for p in paths])
    )
    recents = collect_recent([profile], [], tmp_path)
    assert len(recents) == office.MAX_RECENT
    assert recents[0].name == "d00.odt"


def test_profile_locations_cover_the_package_flatpak_and_snap(tmp_path):
    texts = " ".join(str(p) for p in libreoffice_profiles(tmp_path))
    assert ".config/libreoffice/4/user" in texts
    assert office.FLATPAK_ID in texts
    assert "snap/libreoffice" in texts
    assert len(recently_used_files(tmp_path)) == 2


# --- la commande --------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("present", "flatpak", "expected"),
    [
        ({"libreoffice"}, False, ["libreoffice"]),
        ({"soffice"}, False, ["soffice"]),
        ({"libreoffice", "soffice"}, False, ["libreoffice"]),
        ({"flatpak"}, True, ["flatpak", "run", office.FLATPAK_ID]),
        ({"flatpak"}, False, None),
        ({}, True, None),
        ({}, False, None),
    ],
)
def test_the_libreoffice_command_depends_on_how_it_is_installed(present, flatpak, expected):
    assert detect_command(lambda n: f"/usr/bin/{n}" if n in present else None, flatpak) == expected


# --- le fournisseur -----------------------------------------------------------------------------


class RecordingLauncher:
    def __init__(self, fail=None):
        self.calls, self.fail = [], fail

    async def start(self, argv, *, label):
        if self.fail:
            raise LaunchError(self.fail)
        self.calls.append((list(argv), label))


@pytest.fixture
def provider(tmp_path):
    a, b = make_documents(tmp_path, "rapport.odt", "budget.ods")
    profile = tmp_path / ".config" / "libreoffice" / "4" / "user" / "registrymodifications.xcu"
    write(profile, xcu([(url(a), "writer8"), (url(b), "calc8")], [url(a), url(b)]))
    launcher = RecordingLauncher()
    instance = OfficeProvider(
        home=tmp_path,
        launcher=launcher,
        which=lambda n: f"/usr/bin/{n}" if n == "libreoffice" else None,
        flatpak_present=lambda: False,
        interval=0.05,
    )
    return instance, launcher, tmp_path, (a, b), profile


async def test_the_snapshot_lists_recent_documents_without_full_paths(provider):
    instance, _launcher, home, (a, _b), _profile = provider
    await instance.refresh()
    snapshot = instance.snapshot()
    assert snapshot["kinds"] == ["writer", "calc", "impress"]
    assert [(r["name"], r["kind"], r["where"]) for r in snapshot["recent"]] == [
        ("rapport.odt", "writer", "~/Documents"),
        ("budget.ods", "calc", "~/Documents"),
    ]
    assert snapshot["recent"][0]["id"] == recent_id(url(a))
    assert str(home) not in str(snapshot)


async def test_without_libreoffice_the_snapshot_says_so(tmp_path):
    instance = OfficeProvider(home=tmp_path, which=lambda _n: None, flatpak_present=lambda: False)
    await instance.refresh()
    snapshot = instance.snapshot()
    assert "LibreOffice n'est pas installé" in snapshot["unavailable"]
    assert snapshot["recent"] == []
    with pytest.raises(ActionError) as caught:
        await instance.new("writer")
    assert caught.value.code == "unavailable"
    with pytest.raises(ActionError):
        await instance.open("0123456789abcdef")


@pytest.mark.parametrize(
    ("kind", "flag", "label"),
    [
        ("writer", "--writer", "LibreOffice Writer"),
        ("calc", "--calc", "LibreOffice Calc"),
        ("impress", "--impress", "LibreOffice Impress"),
    ],
)
async def test_a_new_document_starts_the_matching_module(provider, kind, flag, label):
    instance, launcher, *_ = provider
    assert await instance.new(kind) == {"started": True}
    assert launcher.calls == [(["libreoffice", flag], label)]


async def test_a_recent_document_is_reopened_by_its_identifier(provider):
    instance, launcher, _home, (a, _b), _profile = provider
    reply = await instance.open(recent_id(url(a)))
    assert reply == {"started": True, "name": "rapport.odt"}
    assert launcher.calls == [(["libreoffice", str(a)], "rapport.odt")]


async def test_only_listed_documents_can_be_opened_and_only_if_they_still_exist(provider):
    instance, launcher, _home, (a, _b), _profile = provider
    for wanted in ("0000000000000000", recent_id("file:///etc/passwd")):
        with pytest.raises(ActionError) as caught:
            await instance.open(wanted)
        assert caught.value.code == "not_found"
    await instance.refresh()
    a.unlink()
    with pytest.raises(ActionError) as caught:
        await instance.open(recent_id(url(a)))  # supprimé depuis la dernière lecture
    assert caught.value.code == "not_found"
    assert launcher.calls == []


async def test_a_failing_launcher_is_reported(provider):
    instance, _launcher, *_ = provider
    instance._launcher = RecordingLauncher(
        fail="Le lancement de « LibreOffice Calc » a échoué (code 1)."
    )
    with pytest.raises(ActionError) as caught:
        await instance.new("calc")
    assert caught.value.code == "launch_failed"


async def test_a_changed_list_is_read_again_and_a_vanished_file_leaves_the_list_at_once(provider):
    instance, _launcher, home, (a, b), profile = provider
    await instance.refresh()
    assert len(instance.snapshot()["recent"]) == 2
    b.unlink()
    await instance.refresh()  # la liste n'a pas changé, mais le fichier a disparu
    assert [r["name"] for r in instance.snapshot()["recent"]] == ["rapport.odt"]
    (c,) = make_documents(home, "nouveau.odp")
    write(profile, xcu([(url(c), "impress8"), (url(a), "writer8")], [url(c), url(a)]))
    stamp = time.time() + 5  # horodatage sûrement différent
    os.utime(profile, (stamp, stamp))
    await instance.refresh()
    assert [r["name"] for r in instance.snapshot()["recent"]] == ["nouveau.odp", "rapport.odt"]


# --- les actions, telles que le dock les envoie -------------------------------------------------


@pytest.fixture
def registry(provider):
    instance, launcher, *_ = provider
    reg = ActionRegistry()
    register_office_actions(reg, instance)
    reg.launcher = launcher
    return reg


async def call(reg, name, params):
    return await reg.invoke(Act("t1", name, params), ActionContext(1, "127.0.0.1"))


async def test_office_actions_are_not_sensitive_and_work(registry, provider):
    _instance, _launcher, _home, (a, _b), _profile = provider
    assert registry.names == ["office.new", "office.open"]
    assert not any(registry.is_sensitive(n) for n in registry.names)
    assert (await call(registry, "office.new", {"kind": "calc"}))["ok"] is True
    assert (await call(registry, "office.open", {"id": recent_id(url(a))}))["ok"] is True


@pytest.mark.parametrize(
    ("name", "params"),
    [
        ("office.new", {"kind": "draw"}),
        ("office.new", {"kind": "writer --infilter=x"}),
        ("office.new", {"kind": "../../bin/sh"}),
        ("office.new", {}),
        ("office.new", {"kind": "writer", "path": "/etc/passwd"}),
        ("office.open", {"id": "/etc/passwd"}),
        ("office.open", {"id": "file:///etc/passwd"}),
        ("office.open", {"id": "0123456789ABCDEF"}),
        ("office.open", {"id": 12345}),
        ("office.open", {"id": "0123456789abcdef", "path": "/etc/shadow"}),
    ],
)
async def test_malformed_requests_never_reach_the_provider(registry, name, params):
    reply = await call(registry, name, params)
    assert reply["e"]["code"] == "bad_params"
    assert registry.launcher.calls == []
