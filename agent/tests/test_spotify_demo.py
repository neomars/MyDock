"""La démonstration de Spotify : mêmes réponses que le vrai service, aucun réseau, et le lecteur fictif obéit."""

import base64

import pytest

from mydock_agent.demo.music import DemoMusic
from mydock_agent.demo.spotify import CATALOG, DemoSpotify, fold, spotify_id
from mydock_agent.spotify.actions import register_spotify_actions
from mydock_agent.spotify.catalog import ID_RE, URI_RE
from mydock_common.actions import ActionContext, ActionError, ActionRegistry
from mydock_common.protocol import Act


def make():
    music = DemoMusic()
    return music, DemoSpotify(music)


def test_ids_are_stable_and_look_like_the_real_ones():
    assert spotify_id("a") == spotify_id("a")
    assert spotify_id("a") != spotify_id("b")
    assert ID_RE.fullmatch(spotify_id("n'importe quoi"))


def test_every_address_of_the_catalog_is_a_valid_spotify_address():
    _, demo = make()
    for track in demo.tracks:
        assert URI_RE.fullmatch(track["uri"])
    for album in demo.albums.values():
        assert URI_RE.fullmatch(album["uri"])
    for artist in demo.artists.values():
        assert URI_RE.fullmatch(artist["uri"])
    for playlist in demo.playlists.values():
        assert URI_RE.fullmatch(playlist["uri"])
    assert len({t["id"] for t in demo.tracks}) == len(demo.tracks)  # pas de doublon


async def test_the_status_is_that_of_a_connected_premium_account():
    _, demo = make()
    status = await demo.status()
    assert (status["connected"], status["premium"], status["running"]) == (True, True, True)


async def test_the_search_ignores_case_and_accents():
    _, demo = make()
    result = await demo.search("FANTOMES")
    assert {a["name"] for a in result["artists"]} == {"Les Fantômes"}
    assert {a["name"] for a in result["albums"]} == {"Néons", "Fréquences"}
    assert len(result["tracks"]) == 7  # tous les titres de l'artiste
    result = await demo.search("saison")
    assert [a["name"] for a in result["albums"]] == ["Saisons"]
    assert (await demo.search("zzzzzz"))["tracks"] == []
    assert fold("Éè Ça") == "ee ca"


async def test_nothing_private_leaks_into_the_answers():
    _, demo = make()
    result = await demo.search("a")
    for kind in ("tracks", "albums", "artists", "playlists"):
        for item in result[kind]:
            assert not any(key.startswith("_") for key in item)
            assert "seconds" not in item
            assert "artist_id" not in item


async def test_albums_artists_and_playlists_have_pages():
    _, demo = make()
    album_id = next(iter(demo.albums))
    album = await demo.view("album", album_id)
    assert album["item"]["id"] == album_id
    assert album["tracks"]
    artist_id = next(iter(demo.artists))
    artist = await demo.view("artist", artist_id)
    assert artist["albums"]
    assert artist["tracks"]
    playlist_id = next(iter(demo.playlists))
    assert (await demo.view("playlist", playlist_id))["tracks"]
    with pytest.raises(ActionError) as info:
        await demo.view("album", spotify_id("inconnu"))
    assert info.value.code == "not_found"


async def test_the_library_has_its_five_sections():
    _, demo = make()
    kinds = {
        "playlists": "playlist",
        "albums": "album",
        "artists": "artist",
        "liked": "track",
        "recent": "track",
    }
    for section, kind in kinds.items():
        library = await demo.library(section)
        assert (library["section"], library["kind"]) == (section, kind)
        assert library["items"]


async def test_every_cover_of_the_catalog_is_a_small_generated_svg():
    _, demo = make()
    arts = {t["art"] for t in demo.tracks} | {a["art"] for a in demo.artists.values()}
    arts |= {p["art"] for p in demo.playlists.values()}
    for art in arts:
        image = await demo.image(art)
        assert image["mime"] == "image/svg+xml"
        assert base64.b64decode(image["data"]).startswith(b"<svg")
    with pytest.raises(ActionError) as info:
        await demo.image("0123456789abcdef")
    assert info.value.code == "unknown_art"


async def test_playing_a_track_makes_the_fake_player_play_it():
    music, demo = make()
    track = demo.tracks[3]
    assert await demo.play(track["uri"]) == {"via": "api", "started": False}
    player = music.spotify
    assert (player.title, player.artist, player.album) == (
        track["name"],
        track["artists"],
        track["album"],
    )
    assert player.status == "playing"
    assert player.length_ms == track["seconds"] * 1000
    assert music.snapshot()["active"] == "spotify"


async def test_playing_an_album_a_playlist_or_an_artist_starts_with_its_first_track():
    music, demo = make()
    album = next(iter(demo.albums.values()))
    await demo.play(album["uri"])
    assert music.spotify.title == album["_tracks"][0]["name"]
    playlist = next(iter(demo.playlists.values()))
    await demo.play(playlist["uri"])
    assert music.spotify.title == playlist["_tracks"][0]["name"]
    artist = next(iter(demo.artists.values()))
    await demo.play(artist["uri"])
    assert music.spotify.artist == artist["name"]


async def test_playing_something_unknown_is_not_found():
    _, demo = make()
    for uri in (
        f"spotify:track:{spotify_id('x')}",
        f"spotify:album:{spotify_id('x')}",
        "n'importe quoi",
    ):
        with pytest.raises(ActionError) as info:
            await demo.play(uri)
        assert info.value.code == "not_found"


async def test_launching_is_a_no_op_because_the_demo_player_is_always_open():
    _, demo = make()
    assert await demo.launch() == {"running": True, "started": False}


async def test_the_demo_answers_the_same_actions_as_the_real_service():
    _, demo = make()
    registry = ActionRegistry()
    register_spotify_actions(registry, demo)
    answer = await registry.invoke(
        Act("a1", "spotify.search", {"query": "mizu"}, None), ActionContext(1, "192.168.1.60")
    )
    assert answer["ok"] is True
    assert answer["r"]["artists"][0]["name"] == "Mizu"
    assert len(CATALOG) == 4


async def test_the_demo_has_connect_devices_and_a_transfer_moves_the_music():
    _, demo = make()
    first = (await demo.devices())["devices"]
    assert first[0]["this_pc"] is True  # ce PC d'abord
    assert [d["name"] for d in first if d["active"]] == [first[0]["name"]]
    assert {d["type"] for d in first} >= {"Computer", "Speaker", "Smartphone", "TV"}
    speaker = next(d for d in first if d["type"] == "Speaker")
    assert await demo.transfer(speaker["id"]) == {"device": speaker["id"]}
    after = {d["id"]: d for d in (await demo.devices())["devices"]}
    assert [i for i, d in after.items() if d["active"]] == [speaker["id"]]
    assert await demo.set_volume(speaker["id"], 61) == {"device": speaker["id"], "volume": 61}
    assert (await demo.devices())["devices"][1:][0]["volume"] in range(0, 101)
    with pytest.raises(ActionError) as info:
        await demo.transfer("0" * 40)
    assert info.value.code == "unknown_device"
    with pytest.raises(ActionError):
        await demo.set_volume("0" * 40, 10)
