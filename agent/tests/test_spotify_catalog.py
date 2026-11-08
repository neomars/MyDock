"""Les réponses de l'API de Spotify remises en forme pour l'écran : fonctions pures, aucune dépendance au réseau."""

import pytest

from mydock_agent.spotify import catalog
from mydock_agent.spotify.catalog import (
    ArtRegistry,
    album_item,
    album_view,
    artist_item,
    artist_view,
    entries_tracks,
    image_host_ok,
    library_section,
    parse_uri,
    pick_image,
    playlist_item,
    playlist_view,
    search_results,
    track_item,
)

ID_A = "4aawyAB9vmqN3uQ7FjRGTy"
ID_B = "0ETFjACtuP2ADo6LFhL6HN"
ID_C = "1301WleyT98MSxVHPZCA6M"
COVER = "https://i.scdn.co/image/ab67616d0000b273aaaa"


def album_raw(**more):
    return {
        "id": ID_A,
        "name": "Néons",
        "album_type": "album",
        "release_date": "2021-05-14",
        "total_tracks": 4,
        "artists": [{"name": "Les Fantômes"}],
        "images": [
            {"url": "https://i.scdn.co/image/big", "width": 640},
            {"url": "https://i.scdn.co/image/small", "width": 64},
        ],
        **more,
    }


def track_raw(**more):
    return {
        "id": ID_B,
        "type": "track",
        "name": "Nuit blanche",
        "duration_ms": 222000,
        "track_number": 1,
        "explicit": False,
        "artists": [{"name": "Les Fantômes"}, {"name": "Mizu"}],
        "album": album_raw(),
        **more,
    }


# --- adresses ----------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("uri", "parsed"),
    [
        (f"spotify:album:{ID_A}", ("album", ID_A)),
        (f"spotify:track:{ID_B}", ("track", ID_B)),
        (f"spotify:playlist:{ID_C}", ("playlist", ID_C)),
        (f"spotify:show:{ID_C}", ("show", ID_C)),
    ],
)
def test_a_spotify_address_is_split_into_its_kind_and_its_id(uri, parsed):
    assert parse_uri(uri) == parsed


@pytest.mark.parametrize(
    "uri",
    [
        "",
        "spotify:album:trop-court",
        f"spotify:user:{ID_A}",
        f"spotify:local:{ID_A}",
        f"https://open.spotify.com/album/{ID_A}",
        f"spotify:album:{ID_A}\n",
        f"x spotify:album:{ID_A}",
        f"spotify:album:{ID_A}extra",
    ],
)
def test_anything_else_than_a_strict_spotify_address_is_refused(uri):
    assert parse_uri(uri) is None


# --- pochettes : seulement les serveurs d'images de Spotify -----------------------------------------


@pytest.mark.parametrize(
    "url",
    [
        "https://i.scdn.co/image/abc",
        "https://mosaic.scdn.co/640/abc",
        "https://image-cdn-ak.spotifycdn.com/image/abc",
        "https://scdn.co/x",
        "https://I.SCDN.CO/image/abc",
    ],
)
def test_the_covers_of_spotify_are_allowed(url):
    assert image_host_ok(url)


@pytest.mark.parametrize(
    "url",
    [
        "http://i.scdn.co/image/abc",  # pas en https
        "https://evilscdn.co/image/abc",  # pas un sous-domaine
        "https://scdn.co.evil.example/image/abc",
        "https://i.scdn.co@evil.example/image/abc",  # l'hôte réel est après l'arobase
        "https://evil.example/i.scdn.co",
        "ftp://i.scdn.co/image/abc",
        "file:///etc/passwd",
        "",
        "pas une adresse",
    ],
)
def test_no_other_server_is_ever_asked_for_an_image(url):
    assert not image_host_ok(url)


def test_the_registry_hands_out_opaque_ids_and_gives_the_address_back():
    art = ArtRegistry()
    ident = art.register(COVER)
    assert ident is not None
    assert COVER not in ident  # l'écran ne reçoit jamais d'adresse web
    assert len(ident) == 16
    assert art.url(ident) == COVER
    assert art.register(COVER) == ident  # la même image garde le même identifiant
    assert art.url("0123456789abcdef") is None


def test_the_registry_refuses_foreign_addresses_and_garbage():
    art = ArtRegistry()
    assert art.register("https://evil.example/cover.jpg") is None
    assert art.register(None) is None
    assert art.register(42) is None
    assert art.register(["https://i.scdn.co/x"]) is None


def test_the_registry_forgets_the_oldest_covers_first():
    art = ArtRegistry(limit=3)
    first, second, third = (art.register(f"https://i.scdn.co/image/{n}") for n in "abc")
    assert (
        art.register("https://i.scdn.co/image/a") == first
    )  # revue : elle redevient la plus récente
    fourth = art.register("https://i.scdn.co/image/d")
    assert art.url(second) is None  # la plus ancienne non revue a disparu
    assert art.url(first) is not None
    assert art.url(third) is not None
    assert art.url(fourth) is not None


# --- choix de l'image ---------------------------------------------------------------------------------


def test_the_smallest_image_that_is_big_enough_is_chosen():
    images = [
        {"url": "https://i.scdn.co/640", "width": 640},
        {"url": "https://i.scdn.co/300", "width": 300},
        {"url": "https://i.scdn.co/64", "width": 64},
    ]
    assert pick_image(images, 160) == "https://i.scdn.co/300"
    assert pick_image(images, 640) == "https://i.scdn.co/640"
    assert pick_image(images, 1000) == "https://i.scdn.co/640"  # sinon la plus grande
    assert pick_image(images, 10) == "https://i.scdn.co/64"


@pytest.mark.parametrize(
    "images", [None, [], "texte", {}, [None, 3, "x", {"width": 100}, {"url": 5}]]
)
def test_missing_or_broken_image_lists_give_nothing(images):
    assert pick_image(images) is None


def test_images_without_a_width_are_still_usable():
    assert pick_image([{"url": "https://i.scdn.co/x", "width": None}]) == "https://i.scdn.co/x"


# --- éléments -----------------------------------------------------------------------------------------


def test_a_track_carries_what_the_screen_needs():
    art = ArtRegistry()
    item = track_item(track_raw(), art)
    assert item is not None
    assert item["kind"] == "track"
    assert item["uri"] == f"spotify:track:{ID_B}"
    assert item["name"] == "Nuit blanche"
    assert item["artists"] == "Les Fantômes, Mizu"
    assert item["album"] == "Néons"
    assert item["album_id"] == ID_A
    assert item["duration_ms"] == 222000
    assert item["number"] == 1
    assert item["explicit"] is False
    assert item["playable"] is True
    assert art.url(item["art"]) == "https://i.scdn.co/image/big"  # 640 ≥ 160, 64 trop petite


def test_a_track_of_an_album_page_borrows_the_albums_cover_and_name():
    art = ArtRegistry()
    raw = track_raw()
    del raw["album"]
    item = track_item(raw, art, album=album_raw())
    assert item is not None
    assert item["album"] == "Néons"
    assert item["art"] is not None


@pytest.mark.parametrize(
    "raw",
    [
        None,
        "texte",
        {},
        {"id": None, "name": "Fichier local"},  # titre local : pas d'identifiant
        {"id": "court", "name": "x"},
        {"id": ID_B, "type": "episode", "name": "Un épisode"},
        {"id": ID_B + "!", "name": "x"},
    ],
)
def test_what_is_not_a_track_is_dropped_instead_of_failing_the_list(raw):
    assert track_item(raw, ArtRegistry()) is None


def test_a_track_tolerates_missing_and_odd_fields():
    item = track_item(
        {
            "id": ID_B,
            "name": None,
            "duration_ms": "long",
            "track_number": -3,
            "explicit": "oui",
            "is_playable": False,
            "artists": [None, {"name": ""}, {"name": "Mizu"}, "x"],
            "album": None,
        },
        ArtRegistry(),
    )
    assert item is not None
    assert item["name"] == "Sans titre"
    assert item["artists"] == "Mizu"
    assert item["duration_ms"] is None
    assert item["number"] is None
    assert item["explicit"] is False  # seul `true` compte
    assert item["playable"] is False
    assert item["album"] is None
    assert item["art"] is None


def test_texts_are_cleaned_and_shortened():
    item = track_item(track_raw(name="  Un\x00titre\n très " + "long " * 80), ArtRegistry())
    assert item is not None
    assert len(item["name"]) <= catalog.TEXT_LIMIT
    assert "\x00" not in item["name"]
    assert "\n" not in item["name"]


def test_an_album():
    art = ArtRegistry()
    item = album_item(album_raw(), art)
    assert item is not None
    assert item["uri"] == f"spotify:album:{ID_A}"
    assert (item["name"], item["artists"], item["year"], item["type"], item["tracks"]) == (
        "Néons",
        "Les Fantômes",
        "2021",
        "album",
        4,
    )
    assert art.url(item["art"]) is not None


def test_an_album_with_odd_fields():
    item = album_item(
        {"id": ID_A, "name": "", "album_type": "bootleg", "release_date": "?", "total_tracks": -1},
        ArtRegistry(),
    )
    assert item is not None
    assert item["name"] == "Sans titre"
    assert item["type"] is None
    assert item["year"] is None
    assert item["tracks"] is None
    assert album_item({"name": "sans id"}, ArtRegistry()) is None
    assert album_item(None, ArtRegistry()) is None


def test_an_artist_shows_at_most_three_genres():
    item = artist_item(
        {"id": ID_C, "name": "Mizu", "genres": ["pop", "électro", "j-pop", "city pop", 5]},
        ArtRegistry(),
    )
    assert item is not None
    assert item["genres"] == "pop, électro, j-pop"
    assert artist_item({"id": ID_C}, ArtRegistry())["name"] == "Artiste inconnu"  # type: ignore[index]
    assert artist_item({"id": ID_C, "genres": "pop"}, ArtRegistry())["genres"] is None  # type: ignore[index]
    assert artist_item({"id": "x"}, ArtRegistry()) is None


def test_a_playlist_counts_its_tracks_under_either_name_of_the_api():
    """Depuis février 2026 l'API nomme `items` ce qui s'appelait `tracks` ; on lit les deux."""
    old = playlist_item(
        {"id": ID_A, "name": "Soirée", "owner": {"display_name": "Toi"}, "tracks": {"total": 12}},
        ArtRegistry(),
    )
    new = playlist_item(
        {"id": ID_A, "name": "Soirée", "owner": {"display_name": "Toi"}, "items": {"total": 9}},
        ArtRegistry(),
    )
    assert old is not None
    assert new is not None
    assert (old["tracks"], new["tracks"]) == (12, 9)
    assert old["owner"] == "Toi"
    bare = playlist_item({"id": ID_A}, ArtRegistry())
    assert bare is not None
    assert (bare["name"], bare["owner"], bare["tracks"]) == ("Playlist sans nom", None, None)
    assert playlist_item({"id": "x"}, ArtRegistry()) is None


# --- réponses de l'API -------------------------------------------------------------------------------------


def test_a_search_keeps_the_good_results_and_drops_the_broken_ones():
    raw = {
        "tracks": {"items": [track_raw(), None, {"id": None}, {"id": ID_C, "name": "B"}]},
        "albums": {"items": [album_raw(), "x"]},
        "artists": {"items": [{"id": ID_C, "name": "Mizu"}, {}]},
        "playlists": {"items": [None, {"id": ID_A, "name": "P"}]},
    }
    result = search_results(raw, ArtRegistry())
    assert [t["id"] for t in result["tracks"]] == [ID_B, ID_C]
    assert len(result["albums"]) == 1
    assert len(result["artists"]) == 1
    assert [p["id"] for p in result["playlists"]] == [ID_A]


@pytest.mark.parametrize("raw", [None, {}, [], "x", {"tracks": None}, {"tracks": {"items": None}}])
def test_a_search_with_nothing_usable_gives_empty_lists(raw):
    assert search_results(raw, ArtRegistry()) == {
        "tracks": [],
        "albums": [],
        "artists": [],
        "playlists": [],
    }


def test_an_album_page_lists_its_tracks_with_the_album_cover():
    raw = album_raw(tracks={"items": [{k: v for k, v in track_raw().items() if k != "album"}]})
    view = album_view(raw, ArtRegistry())
    assert view is not None
    assert view["kind"] == "album"
    assert view["item"]["name"] == "Néons"
    assert [t["name"] for t in view["tracks"]] == ["Nuit blanche"]
    assert view["tracks"][0]["album"] == "Néons"
    assert album_view({"name": "sans id"}, ArtRegistry()) is None


def test_playlist_entries_hold_the_track_under_either_key():
    entries = [
        {"track": track_raw()},
        {"item": track_raw(id=ID_C, name="Autre")},  # nom de la clé depuis février 2026
        {"track": None},  # titre supprimé
        {"track": {"id": None, "name": "local"}},
        "x",
        None,
    ]
    assert [t["name"] for t in entries_tracks(entries, ArtRegistry())] == ["Nuit blanche", "Autre"]


def test_a_playlist_page():
    meta = {
        "id": ID_A,
        "name": "Soirée",
        "owner": {"display_name": "Spotify"},
        "tracks": {"total": 2},
    }
    view = playlist_view(meta, [{"track": track_raw()}], ArtRegistry())
    assert view is not None
    assert view["item"]["name"] == "Soirée"
    assert len(view["tracks"]) == 1
    assert playlist_view({"name": "sans id"}, [], ArtRegistry()) is None


def test_an_artist_page():
    view = artist_view(
        {"id": ID_C, "name": "Mizu"},
        [album_raw(), None],
        [track_raw(), {"id": None}],
        ArtRegistry(),
    )
    assert view is not None
    assert view["item"]["name"] == "Mizu"
    assert len(view["albums"]) == 1
    assert len(view["tracks"]) == 1
    assert artist_view({"name": "sans id"}, [], [], ArtRegistry()) is None


def test_every_section_of_the_library_is_shaped_for_its_kind():
    art = ArtRegistry()
    playlists = library_section("playlists", {"items": [{"id": ID_A, "name": "P"}, None]}, art)
    assert (playlists["section"], playlists["kind"], len(playlists["items"])) == (
        "playlists",
        "playlist",
        1,
    )
    albums = library_section(
        "albums", {"items": [{"album": album_raw()}, {"album": None}, "x"]}, art
    )
    assert (albums["kind"], [a["name"] for a in albums["items"]]) == ("album", ["Néons"])
    artists = library_section(
        "artists", {"artists": {"items": [{"id": ID_C, "name": "Mizu"}]}}, art
    )
    assert (artists["kind"], [a["name"] for a in artists["items"]]) == ("artist", ["Mizu"])
    liked = library_section("liked", {"items": [{"track": track_raw()}]}, art)
    assert (liked["kind"], [t["name"] for t in liked["items"]]) == ("track", ["Nuit blanche"])


def test_recently_played_shows_a_track_once_even_if_it_was_replayed():
    raw = {
        "items": [
            {"track": track_raw()},
            {"track": track_raw(id=ID_C, name="Autre")},
            {"track": track_raw()},
        ]
    }
    recent = library_section("recent", raw, ArtRegistry())
    assert [t["id"] for t in recent["items"]] == [ID_B, ID_C]
    liked = library_section("liked", raw, ArtRegistry())
    assert len(liked["items"]) == 3  # les titres likés, eux, restent tels quels


@pytest.mark.parametrize("section", ["playlists", "albums", "artists", "liked", "recent"])
def test_an_empty_or_broken_library_answer_gives_an_empty_section(section):
    for raw in (None, {}, {"items": None}, []):
        assert library_section(section, raw, ArtRegistry())["items"] == []
