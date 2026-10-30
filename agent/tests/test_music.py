"""Musique (MPRIS) : état des lecteurs, choix du lecteur actif, actions et pochettes (faux bus D-Bus)."""

import asyncio
import base64
import io
import json

import pytest
from dbus_fast import Variant

from mydock_agent.collectors import music
from mydock_agent.collectors.music import (
    BusError,
    MusicProvider,
    PlayerState,
    Signal,
    art_id,
    base_id,
    build_snapshot,
    choose_active,
    clean_text,
    register_music_actions,
    unwrap,
)
from mydock_agent.testing import eventually, png_bytes
from mydock_common.actions import ActionContext, ActionRegistry
from mydock_common.protocol import Act

SPOTIFY = "org.mpris.MediaPlayer2.spotify"
BRAVE = "org.mpris.MediaPlayer2.brave.instance9888"


def player_props(**overrides):
    props = {
        "PlaybackStatus": "Playing",
        "Metadata": {
            "mpris:trackid": "/com/spotify/track/42",
            "mpris:length": 215_000_000,
            "mpris:artUrl": "https://i.scdn.co/image/ab67616d",
            "xesam:title": "Morceau",
            "xesam:artist": ["Artiste", "Invité"],
            "xesam:album": "Album",
        },
        "Position": 30_000_000,
        "Rate": 1.0,
        "CanPlay": True,
        "CanPause": True,
        "CanGoNext": True,
        "CanGoPrevious": True,
        "CanSeek": True,
    }
    props.update(overrides)
    return props


class FakeBus:
    """Un bus de session simulé : des lecteurs, leurs propriétés, et les appels reçus."""

    def __init__(self):
        self.names: dict[str, dict] = {}
        self.handlers = []
        self.matches: list[str] = []
        self.calls: list[tuple] = []
        self.connect_error: Exception | None = None
        self.disconnected = asyncio.Event()
        self.connected = False
        self.unavailable_properties: set[str] = set()
        self.fail_calls: set[str] = set()

    def add_player(self, name, owner, identity, **props):
        self.names[name] = {
            "owner": owner,
            "player": player_props(**props),
            "root": {"Identity": identity},
        }

    # --- interface Bus ---
    async def connect(self):
        if self.connect_error:
            raise self.connect_error
        self.connected = True

    async def disconnect(self):
        self.connected = False

    async def wait_for_disconnect(self):
        await self.disconnected.wait()

    def on_signal(self, handler):
        self.handlers.append(handler)

    async def add_match(self, rule):
        self.matches.append(rule)

    async def call(self, destination, path, interface, member, signature="", body=None):
        body = body or []
        self.calls.append((destination, interface, member, body))
        if member in self.fail_calls:
            raise BusError(f"{member} : erreur")
        if member == "ListNames":
            return [list(self.names)]
        if member == "GetNameOwner":
            if body[0] not in self.names:
                raise BusError("GetNameOwner : NameHasNoOwner")
            return [self.names[body[0]]["owner"]]
        entry = self.names.get(destination)
        if entry is None:
            raise BusError(f"{member} : ServiceUnknown")
        if member == "GetAll":
            return [dict(entry["player"] if body[0] == music.PLAYER_IFACE else entry["root"])]
        if member == "Get":
            prop = body[1]
            if prop in self.unavailable_properties or prop not in entry["player"]:
                raise BusError("Get : UnknownProperty")
            return [entry["player"][prop]]
        return []  # PlayPause, Next, Seek, SetPosition…

    # --- côté test ---
    def emit(self, member, sender, interface, body, path=music.OBJECT_PATH):
        for handler in self.handlers:
            handler(Signal(sender, path, interface, member, body))

    def changed(self, owner, changed, invalidated=()):
        self.emit(
            "PropertiesChanged",
            owner,
            music.PROPS_IFACE,
            [music.PLAYER_IFACE, changed, list(invalidated)],
        )


# --- fonctions pures -----------------------------------------------------------------------------


def test_dbus_variants_are_opened_recursively():
    value = {
        "xesam:title": Variant("s", "Titre"),
        "xesam:artist": Variant("as", ["A", "B"]),
        "mpris:length": Variant("x", 1_000_000),
        "nested": Variant("a{sv}", {"k": Variant("i", 3)}),
    }
    assert unwrap(value) == {
        "xesam:title": "Titre",
        "xesam:artist": ["A", "B"],
        "mpris:length": 1_000_000,
        "nested": {"k": 3},
    }


def test_text_is_cleaned_and_bounded():
    assert clean_text("  Hello\x00\nWorld\t ") == "Hello World"
    assert clean_text("") is None
    assert clean_text(None) is None
    assert len(clean_text("x" * 1000)) == music.TEXT_LIMIT


@pytest.mark.parametrize(
    ("player_id", "base"),
    [
        ("spotify", "spotify"),
        ("brave.instance9888", "brave"),
        ("vlc", "vlc"),
        ("chromium.instance12.x", "chromium.instance12.x"),
    ],
)
def test_a_relaunched_browser_is_still_the_same_player(player_id, base):
    assert base_id(player_id) == base


def state(player_id, status="stopped", activity=0, **kw):
    player = PlayerState(
        id=player_id, owner=f":1.{abs(hash(player_id)) % 99}", activity=activity, **kw
    )
    player.status = status
    return player


def test_metadata_is_normalised():
    player = state("spotify")
    player.apply(player_props())
    assert (player.title, player.artist, player.album) == ("Morceau", "Artiste, Invité", "Album")
    assert player.length_ms == 215_000
    assert player.position_ms == 30_000
    assert player.status == "playing"
    assert player.track_id == "/com/spotify/track/42"
    assert (player.can_next, player.can_previous, player.can_seek) == (True, True, True)


def test_a_player_with_poor_metadata_still_displays():
    player = state("vlc")
    player.apply({"Metadata": {"xesam:albumArtist": ["Collectif"], "mpris:length": "bizarre"}})
    assert player.title is None
    assert player.artist == "Collectif"
    assert player.length_ms is None
    assert player.art_url is None
    player.apply({"PlaybackStatus": "Truc"})
    assert player.status == "stopped"


def test_position_is_extended_while_playing_and_frozen_while_paused():
    player = state("spotify", "playing")
    player.apply({"Metadata": {"mpris:length": 100_000_000}, "Position": 10_000_000})
    t0 = player.position_t
    assert player.current_position_ms(t0) == 10_000
    assert player.current_position_ms(t0 + 5) == 15_000
    assert player.current_position_ms(t0 + 500) == 100_000  # jamais au-delà de la durée
    player.status = "paused"
    assert player.current_position_ms(t0 + 5) == 10_000
    player.rate = 2.0
    player.status = "playing"
    assert player.current_position_ms(t0 + 5) == 20_000
    assert state("x").current_position_ms(0) is None


def test_the_artwork_is_referred_to_by_an_opaque_identifier_not_by_its_url():
    player = state("spotify")
    player.apply(player_props())
    data = player.to_dict()
    assert data["art"] == art_id("https://i.scdn.co/image/ab67616d")
    assert "scdn" not in json.dumps(data)  # le dock n'a jamais l'adresse à ouvrir


def test_what_is_playing_is_the_active_player():
    spotify, brave = state("spotify", "paused"), state("brave.instance1", "playing", activity=1)
    assert choose_active([spotify, brave], None) is brave


def test_spotify_wins_when_nothing_else_distinguishes_players():
    spotify, vlc = state("spotify", "paused"), state("vlc", "paused")
    assert choose_active([vlc, spotify], None) is spotify
    assert choose_active([vlc], None) is vlc
    assert choose_active([], None) is None


def test_the_player_that_played_last_wins_among_paused_ones():
    old, recent = state("vlc", "paused", activity=1), state("rhythmbox", "paused", activity=5)
    assert choose_active([old, recent], None) is recent


def test_an_explicit_choice_is_kept_even_if_another_player_plays():
    spotify, brave = state("spotify", "paused"), state("brave.instance1", "playing", activity=3)
    assert choose_active([spotify, brave], "spotify") is spotify
    assert choose_active([spotify, brave], "brave") is brave  # par nom de base
    assert choose_active([spotify, brave], "absent") is brave  # choix périmé : on suit la lecture


def test_the_snapshot_lists_the_active_player_first_and_is_json():
    spotify, brave = state("spotify", "paused"), state("brave.instance1", "playing", activity=1)
    snapshot = build_snapshot([spotify, brave], None, now=0)
    assert snapshot["active"] == "brave.instance1"
    assert [p["id"] for p in snapshot["players"]] == ["brave.instance1", "spotify"]
    json.dumps(snapshot)
    assert build_snapshot([], None) == {"players": [], "active": None}


# --- le fournisseur, sur un faux bus -----------------------------------------------------------------


async def start(bus: FakeBus, provider: MusicProvider | None = None):
    provider = provider or MusicProvider(lambda: bus)
    published: list[dict] = []

    async def publish(data):
        published.append(data)
        return True

    task = asyncio.create_task(provider.run(publish))
    await eventually(lambda: published)
    return provider, published, task


async def stop(task):
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)


async def test_players_already_open_are_found_at_startup():
    bus = FakeBus()
    bus.add_player(SPOTIFY, ":1.10", "Spotify")
    bus.add_player(BRAVE, ":1.20", "Brave", PlaybackStatus="Paused")
    bus.add_player("org.freedesktop.Notifications", ":1.3", "Pas un lecteur")
    _, published, task = await start(bus)
    try:
        first = published[0]
        assert first["active"] == "spotify"
        names = {p["id"]: p["name"] for p in first["players"]}
        assert names == {"spotify": "Spotify", "brave.instance9888": "Brave"}
        assert any("NameOwnerChanged" in rule and "arg0namespace" in rule for rule in bus.matches)
        assert any("PropertiesChanged" in rule for rule in bus.matches)
    finally:
        await stop(task)


async def test_no_player_at_all_is_a_clear_empty_state():
    _, published, task = await start(FakeBus())
    try:
        assert published[0] == {"players": [], "active": None}
    finally:
        await stop(task)


async def test_a_property_change_updates_the_state():
    bus = FakeBus()
    bus.add_player(SPOTIFY, ":1.10", "Spotify")
    _, published, task = await start(bus)
    try:
        bus.changed(":1.10", {"Metadata": {"xesam:title": "Autre", "mpris:length": 100_000_000}})
        await eventually(lambda: published[-1]["players"][0]["title"] == "Autre")
        assert published[-1]["players"][0]["length_ms"] == 100_000
    finally:
        await stop(task)


async def test_pausing_rereads_the_position_because_it_is_not_signalled():
    bus = FakeBus()
    bus.add_player(SPOTIFY, ":1.10", "Spotify")
    _, published, task = await start(bus)
    try:
        bus.names[SPOTIFY]["player"]["Position"] = 77_000_000  # le lecteur est à 1 min 17
        bus.changed(":1.10", {"PlaybackStatus": "Paused"})
        await eventually(lambda: published[-1]["players"][0]["status"] == "paused")
        assert published[-1]["players"][0]["position_ms"] == 77_000
    finally:
        await stop(task)


async def test_invalidated_properties_are_read_again():
    bus = FakeBus()
    bus.add_player(SPOTIFY, ":1.10", "Spotify")
    _, published, task = await start(bus)
    try:
        bus.names[SPOTIFY]["player"]["PlaybackStatus"] = "Paused"
        bus.changed(":1.10", {}, invalidated=["PlaybackStatus"])
        await eventually(lambda: published[-1]["players"][0]["status"] == "paused")
    finally:
        await stop(task)


async def test_a_seek_signal_moves_the_position():
    bus = FakeBus()
    bus.add_player(SPOTIFY, ":1.10", "Spotify", PlaybackStatus="Paused")
    _, published, task = await start(bus)
    try:
        bus.emit("Seeked", ":1.10", music.PLAYER_IFACE, [150_000_000])
        await eventually(lambda: published[-1]["players"][0]["position_ms"] == 150_000)
    finally:
        await stop(task)


async def test_a_player_appearing_and_leaving_follows_the_bus_owner_changes():
    bus = FakeBus()
    bus.add_player(SPOTIFY, ":1.10", "Spotify", PlaybackStatus="Paused")
    _, published, task = await start(bus)
    try:
        bus.add_player(BRAVE, ":1.20", "Brave")
        bus.emit(
            "NameOwnerChanged",
            "org.freedesktop.DBus",
            music.DBUS_NAME,
            [BRAVE, "", ":1.20"],
            "/org/freedesktop/DBus",
        )
        await eventually(lambda: len(published[-1]["players"]) == 2)
        assert published[-1]["active"] == "brave.instance9888"  # il joue, Spotify est en pause
        bus.emit(
            "NameOwnerChanged",
            "org.freedesktop.DBus",
            music.DBUS_NAME,
            [BRAVE, ":1.20", ""],
            "/org/freedesktop/DBus",
        )
        await eventually(lambda: len(published[-1]["players"]) == 1)
        assert published[-1]["active"] == "spotify"
    finally:
        await stop(task)


async def test_signals_from_strangers_and_other_interfaces_are_ignored():
    bus = FakeBus()
    bus.add_player(SPOTIFY, ":1.10", "Spotify")
    _, published, task = await start(bus)
    try:
        before = len(published)
        bus.changed(":1.99", {"PlaybackStatus": "Paused"})  # qui est-ce ?
        bus.emit(
            "PropertiesChanged",
            ":1.10",
            music.PROPS_IFACE,
            [music.ROOT_IFACE, {"Identity": "X"}, []],
        )
        bus.emit(
            "NameOwnerChanged",
            "org.freedesktop.DBus",
            music.DBUS_NAME,
            ["org.autre.Truc", "", ":1.5"],
            "/org/freedesktop/DBus",
        )
        bus.changed(":1.10", {"PlaybackStatus": "Paused"})  # celui-ci est vrai
        await eventually(lambda: published[-1]["players"][0]["status"] == "paused")
        assert [p["players"][0]["status"] for p in published[before:]] == ["paused"]
    finally:
        await stop(task)


async def test_a_burst_of_signals_gives_a_single_publication():
    bus = FakeBus()
    bus.add_player(SPOTIFY, ":1.10", "Spotify")
    _, published, task = await start(bus)
    try:
        before = len(published)
        for n in range(20):
            bus.changed(":1.10", {"Metadata": {"xesam:title": f"Titre {n}"}})
        await eventually(lambda: published[-1]["players"][0]["title"] == "Titre 19")
        await asyncio.sleep(0.2)
        assert len(published) - before <= 2  # regroupées, pas vingt messages
    finally:
        await stop(task)


async def test_a_player_without_position_support_is_shown_without_progress():
    bus = FakeBus()
    bus.add_player(BRAVE, ":1.20", "Brave")
    bus.unavailable_properties.add("Position")
    del bus.names[BRAVE]["player"]["Position"]
    _, published, task = await start(bus)
    try:
        assert published[0]["players"][0]["position_ms"] is None
    finally:
        await stop(task)


async def test_a_player_that_announces_its_name_before_being_ready_is_retried(monkeypatch):
    monkeypatch.setattr(music, "ADD_RETRY_SECONDS", 0.01)
    bus = FakeBus()
    bus.add_player(SPOTIFY, ":1.10", "Spotify")
    _, published, task = await start(bus)
    try:
        original = bus.call
        attempts = {"n": 0}

        async def flaky(destination, path, interface, member, signature="", body=None):
            if member == "GetAll" and destination == BRAVE and body == [music.PLAYER_IFACE]:
                attempts["n"] += 1
                if attempts["n"] < 3:
                    raise BusError("GetAll : UnknownObject")
            return await original(destination, path, interface, member, signature, body)

        bus.call = flaky
        bus.add_player(BRAVE, ":1.20", "Brave", PlaybackStatus="Paused")
        bus.emit(
            "NameOwnerChanged",
            "org.freedesktop.DBus",
            music.DBUS_NAME,
            [BRAVE, "", ":1.20"],
            "/org/freedesktop/DBus",
        )
        await eventually(lambda: len(published[-1]["players"]) == 2)
        assert attempts["n"] == 3
    finally:
        await stop(task)


async def test_an_unreachable_session_bus_is_reported_then_retried(monkeypatch):
    monkeypatch.setattr(music, "RECONNECT_SECONDS", 0.05)
    bus = FakeBus()
    bus.connect_error = BusError("Bus de session D-Bus injoignable (FileNotFoundError).")
    _provider, published, task = await start(bus)
    try:
        assert "injoignable" in published[0]["unavailable"]
        bus.connect_error = None
        bus.add_player(SPOTIFY, ":1.10", "Spotify")
        await eventually(lambda: "players" in published[-1])
        assert published[-1]["active"] == "spotify"
    finally:
        await stop(task)


async def test_a_session_bus_that_closes_is_reported_then_reconnected(monkeypatch):
    monkeypatch.setattr(music, "RECONNECT_SECONDS", 0.05)
    bus = FakeBus()
    bus.add_player(SPOTIFY, ":1.10", "Spotify")
    _, published, task = await start(bus)
    try:
        bus.disconnected.set()
        await eventually(lambda: "unavailable" in published[-1])
        bus.disconnected.clear()
        await eventually(lambda: "players" in published[-1])
    finally:
        await stop(task)


# --- actions -----------------------------------------------------------------------------------------


async def run_action(registry, name, params=None, act_id="a1"):
    return await registry.invoke(
        Act(act_id, name, params or {}, None), ActionContext(1, "192.168.1.60")
    )


async def with_actions(bus):
    provider, published, task = await start(bus)
    registry = ActionRegistry()
    register_music_actions(registry, provider)
    return provider, registry, published, task


def player_calls(bus, member):
    return [c for c in bus.calls if c[2] == member]


async def test_play_pause_goes_to_the_active_player():
    bus = FakeBus()
    bus.add_player(SPOTIFY, ":1.10", "Spotify", PlaybackStatus="Paused")
    bus.add_player(BRAVE, ":1.20", "Brave")
    _, registry, _, task = await with_actions(bus)
    try:
        reply = await run_action(registry, "music.play_pause")
        assert reply["ok"] is True
        assert player_calls(bus, "PlayPause")[0][0] == BRAVE  # c'est lui qui joue
        await run_action(registry, "music.play_pause", {"player": "spotify"})
        assert player_calls(bus, "PlayPause")[1][0] == SPOTIFY
    finally:
        await stop(task)


async def test_next_and_previous_respect_what_the_player_can_do():
    bus = FakeBus()
    bus.add_player(SPOTIFY, ":1.10", "Spotify", CanGoNext=False)
    _, registry, _, task = await with_actions(bus)
    try:
        assert (await run_action(registry, "music.previous"))["ok"] is True
        assert player_calls(bus, "Previous")
        refused = await run_action(registry, "music.next")
        assert refused["ok"] is False
        assert refused["e"]["code"] == "unsupported"
        assert not player_calls(bus, "Next")
    finally:
        await stop(task)


async def test_seeking_uses_set_position_with_the_track_id_and_clamps_to_the_length():
    bus = FakeBus()
    bus.add_player(SPOTIFY, ":1.10", "Spotify")
    _, registry, _, task = await with_actions(bus)
    try:
        await run_action(registry, "music.seek", {"position_ms": 60_000})
        assert player_calls(bus, "SetPosition")[-1][3] == ["/com/spotify/track/42", 60_000_000]
        await run_action(registry, "music.seek", {"position_ms": 9_000_000})
        assert player_calls(bus, "SetPosition")[-1][3][1] == 215_000_000  # durée du morceau
    finally:
        await stop(task)


async def test_seeking_falls_back_to_a_relative_move_without_a_track_id():
    bus = FakeBus()
    bus.add_player(
        BRAVE,
        ":1.20",
        "Brave",
        Metadata={"xesam:title": "Vidéo", "mpris:length": 300_000_000},
        Position=10_000_000,
    )
    _, registry, _, task = await with_actions(bus)
    try:
        await run_action(registry, "music.seek", {"position_ms": 70_000})
        assert player_calls(bus, "Seek")[-1][3] == [60_000_000]  # 70 s - 10 s
        assert not player_calls(bus, "SetPosition")
    finally:
        await stop(task)


async def test_seeking_is_refused_when_the_player_cannot():
    bus = FakeBus()
    bus.add_player(BRAVE, ":1.20", "Brave", CanSeek=False)
    _, registry, _, task = await with_actions(bus)
    try:
        reply = await run_action(registry, "music.seek", {"position_ms": 1000})
        assert reply["e"]["code"] == "unsupported"
    finally:
        await stop(task)


async def test_selecting_a_player_pins_it_until_another_one_starts_playing():
    bus = FakeBus()
    bus.add_player(SPOTIFY, ":1.10", "Spotify", PlaybackStatus="Paused")
    bus.add_player(BRAVE, ":1.20", "Brave")
    provider, registry, published, task = await with_actions(bus)
    try:
        assert published[-1]["active"] == "brave.instance9888"
        assert (await run_action(registry, "music.select", {"player": "spotify"}))["ok"] is True
        assert provider.snapshot()["active"] == "spotify"
        bus.changed(":1.20", {"PlaybackStatus": "Paused"})
        bus.changed(":1.10", {"PlaybackStatus": "Playing"})
        await eventually(lambda: published[-1]["active"] == "spotify")
        bus.changed(":1.10", {"PlaybackStatus": "Paused"})
        bus.changed(":1.20", {"PlaybackStatus": "Playing"})  # un autre démarre : on le suit
        await eventually(lambda: published[-1]["active"] == "brave.instance9888")
    finally:
        await stop(task)


async def test_actions_without_any_player_say_so():
    _, registry, _, task = await with_actions(FakeBus())
    try:
        for name in ("music.play_pause", "music.next", "music.previous", "music.position"):
            reply = await run_action(registry, name)
            assert reply["e"]["code"] == "no_player", name
        reply = await run_action(registry, "music.select", {"player": "spotify"})
        assert reply["e"]["code"] == "no_player"
    finally:
        await stop(task)


async def test_a_player_that_stops_answering_is_reported_not_crashed():
    bus = FakeBus()
    bus.add_player(SPOTIFY, ":1.10", "Spotify")
    _, registry, _, task = await with_actions(bus)
    try:
        bus.fail_calls.add("PlayPause")
        reply = await run_action(registry, "music.play_pause")
        assert reply["e"]["code"] == "failed"
        assert "Spotify" in reply["e"]["msg"]
    finally:
        await stop(task)


async def test_the_position_action_gives_a_fresh_reading():
    bus = FakeBus()
    bus.add_player(SPOTIFY, ":1.10", "Spotify", PlaybackStatus="Paused")
    _, registry, _, task = await with_actions(bus)
    try:
        bus.names[SPOTIFY]["player"]["Position"] = 99_000_000
        reply = await run_action(registry, "music.position")
        assert reply["r"] == {"id": "spotify", "status": "paused", "position_ms": 99_000}
    finally:
        await stop(task)


@pytest.mark.parametrize(
    ("name", "params"),
    [
        ("music.seek", {}),
        ("music.seek", {"position_ms": -1}),
        ("music.seek", {"position_ms": "10"}),
        ("music.seek", {"position_ms": 1, "volume": 3}),
        ("music.play_pause", {"player": "../../etc"}),
        ("music.play_pause", {"player": "x" * 100}),
        ("music.select", {}),
        ("music.art", {"art": "pas-un-id"}),
        ("music.art", {"art": "https://example.org/x.png"}),
    ],
)
async def test_bad_parameters_are_refused_before_reaching_the_bus(name, params):
    bus = FakeBus()
    bus.add_player(SPOTIFY, ":1.10", "Spotify")
    _, registry, _, task = await with_actions(bus)
    try:
        calls = len(bus.calls)
        reply = await run_action(registry, name, params)
        assert reply["e"]["code"] == "bad_params"
        assert len(bus.calls) == calls
    finally:
        await stop(task)


# --- pochettes ----------------------------------------------------------------------------------------


def test_images_are_recognised_by_their_content_not_their_name():
    assert music.sniff_image(png_bytes()) == "image/png"
    assert music.sniff_image(b"\xff\xd8\xff\xe0JFIF") == "image/jpeg"
    assert music.sniff_image(b"GIF89a....") == "image/gif"
    assert music.sniff_image(b"RIFF\x00\x00\x00\x00WEBPVP8 ") == "image/webp"
    assert music.sniff_image(b"-----BEGIN OPENSSH PRIVATE KEY-----") is None
    assert music.sniff_image(b"") is None


def test_a_local_cover_file_is_read(tmp_path):
    cover = tmp_path / "pochette.png"
    cover.write_bytes(png_bytes())
    assert music.fetch_art(cover.as_uri()) == png_bytes()
    assert music.fetch_art(f"file://localhost{cover}") == png_bytes()


def test_a_cover_url_may_not_expose_other_files(tmp_path):
    secret = tmp_path / "id_ed25519"
    secret.write_text("-----BEGIN OPENSSH PRIVATE KEY-----\nxxx\n")
    with pytest.raises(music.ArtError, match="pas une image"):
        music.fetch_art(secret.as_uri())
    with pytest.raises(music.ArtError):
        music.fetch_art((tmp_path).as_uri())  # un dossier
    with pytest.raises(music.ArtError):
        music.fetch_art((tmp_path / "absent.png").as_uri())
    with pytest.raises(music.ArtError, match="distante"):
        music.fetch_art("file://autre-machine/tmp/x.png")


def test_a_huge_cover_is_refused(tmp_path, monkeypatch):
    monkeypatch.setattr(music, "ART_MAX_BYTES", 20)
    cover = tmp_path / "grosse.png"
    cover.write_bytes(png_bytes(30, 30))
    with pytest.raises(music.ArtError, match="volumineuse"):
        music.fetch_art(cover.as_uri())


@pytest.mark.parametrize(
    "address",
    ["127.0.0.1", "192.168.1.1", "10.0.0.5", "169.254.169.254", "::1", "fd00::1"],
)
def test_a_cover_on_a_private_address_is_never_fetched(address):
    def resolver(host, port, **kwargs):
        return [(2, 1, 6, "", (address, port))]

    class Spy:
        def open(self, *a, **k):
            raise AssertionError("aucune requête ne doit partir")

    with pytest.raises(music.ArtError, match="non publique"):
        music.fetch_art("https://interne.local/image.png", resolver=resolver, opener=Spy())


def test_only_https_covers_are_fetched():
    for url in ("http://i.scdn.co/x.png", "ftp://x/y.png", "javascript:alert(1)", "gopher://x"):
        with pytest.raises(music.ArtError, match="non prise en charge"):
            music.fetch_art(url)


def test_a_public_https_cover_is_downloaded_and_checked():
    def resolver(host, port, **kwargs):
        return [(2, 1, 6, "", ("151.101.1.1", port))]

    class Response(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    class Opener:
        def open(self, request, timeout):
            assert request.full_url == "https://i.scdn.co/image/abc"
            return Response(png_bytes())

    assert (
        music.fetch_art("https://i.scdn.co/image/abc", resolver=resolver, opener=Opener())
        == png_bytes()
    )

    class Html(Opener):
        def open(self, request, timeout):
            return Response(b"<html>pas une image</html>")

    with pytest.raises(music.ArtError, match="pas une image"):
        music.fetch_art("https://i.scdn.co/image/abc", resolver=resolver, opener=Html())


def test_a_cover_that_cannot_be_resolved_is_refused():
    def resolver(host, port, **kwargs):
        raise OSError("introuvable")

    with pytest.raises(music.ArtError, match="non publique"):
        music.fetch_art("https://nulle-part.invalid/x.png", resolver=resolver)


def test_covers_are_shrunk_for_the_small_screen_when_pillow_is_there():
    pytest.importorskip("PIL")
    from PIL import Image

    big = io.BytesIO()
    Image.new("RGB", (1600, 1600), (30, 60, 200)).save(big, "PNG")
    mime, data = music.prepare_art(big.getvalue())
    assert mime == "image/jpeg"
    with Image.open(io.BytesIO(data)) as image:
        assert max(image.size) == music.ART_SIZE


def test_transparent_covers_are_flattened_to_a_jpeg():
    pytest.importorskip("PIL")
    mime, data = music.prepare_art(png_bytes(8, 8, alpha=True))
    assert mime == "image/jpeg"
    assert data.startswith(b"\xff\xd8\xff")


def test_without_pillow_a_light_cover_goes_through_and_a_heavy_one_is_refused(monkeypatch):
    import builtins

    real_import = builtins.__import__

    def no_pillow(name, *args, **kwargs):
        if name == "PIL" or name.startswith("PIL."):
            raise ImportError("PIL absent")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", no_pillow)
    assert music.prepare_art(png_bytes()) == ("image/png", png_bytes())
    monkeypatch.setattr(music, "ART_SEND_LIMIT", 10)
    with pytest.raises(music.ArtError, match="python3-pil"):
        music.prepare_art(png_bytes(40, 40))


async def test_the_art_action_serves_only_covers_a_player_announced(tmp_path):
    cover = tmp_path / "pochette.png"
    cover.write_bytes(png_bytes())
    bus = FakeBus()
    bus.add_player(SPOTIFY, ":1.10", "Spotify")
    bus.names[SPOTIFY]["player"]["Metadata"]["mpris:artUrl"] = cover.as_uri()
    _provider, registry, published, task = await with_actions(bus)
    try:
        art = published[-1]["players"][0]["art"]
        reply = await run_action(registry, "music.art", {"art": art})
        assert reply["ok"] is True, reply
        assert reply["r"]["mime"] in ("image/png", "image/jpeg")
        assert base64.b64decode(reply["r"]["data"])[:2] in (b"\x89P", b"\xff\xd8")
        cover.unlink()  # le fichier disparaît : la deuxième lecture vient du cache
        again = await run_action(registry, "music.art", {"art": art}, act_id="a2")
        assert again["r"] == reply["r"]
        unknown = await run_action(registry, "music.art", {"art": "0123456789abcdef"}, act_id="a3")
        assert unknown["e"]["code"] == "unknown_art"
    finally:
        await stop(task)


async def test_a_broken_cover_is_an_error_message_not_a_crash(tmp_path):
    cover = tmp_path / "note.txt"
    cover.write_text("pas une image")
    bus = FakeBus()
    bus.add_player(SPOTIFY, ":1.10", "Spotify")
    bus.names[SPOTIFY]["player"]["Metadata"]["mpris:artUrl"] = cover.as_uri()
    _, registry, published, task = await with_actions(bus)
    try:
        reply = await run_action(registry, "music.art", {"art": published[-1]["players"][0]["art"]})
        assert reply["e"]["code"] == "art_failed"
    finally:
        await stop(task)
