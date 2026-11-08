# ruff: noqa: F821, F722
"""Musique : l'agent face à un VRAI bus de session D-Bus et de VRAIS lecteurs MPRIS.

Un dbus-daemon privé est lancé pour le test, avec des faux lecteurs qui parlent exactement le
protocole de Spotify, VLC ou Brave (dbus-fast côté lecteur). C'est la meilleure preuve possible
sans le vrai Spotify : toute la chaîne D-Bus (noms, signaux, appels de méthodes, propriétés) est réelle.
"""

import asyncio
import shutil
import subprocess

import pytest
from dbus_fast import Variant
from dbus_fast.aio import MessageBus
from dbus_fast.service import PropertyAccess, ServiceInterface, dbus_property, method

from mydock_agent.collectors import music
from mydock_agent.collectors.music import DbusFastBus, MusicProvider, register_music_actions
from mydock_agent.testing import eventually, png_bytes
from mydock_common.actions import ActionContext, ActionRegistry
from mydock_common.protocol import Act

pytestmark = pytest.mark.skipif(shutil.which("dbus-daemon") is None, reason="dbus-daemon absent")

TRACK = "/org/test/track/1"


class Root(ServiceInterface):
    def __init__(self, identity: str):
        super().__init__("org.mpris.MediaPlayer2")
        self._identity = identity

    @dbus_property(access=PropertyAccess.READ)
    def Identity(self) -> "s":
        return self._identity


class Player(ServiceInterface):
    """Un lecteur MPRIS minimal mais fidèle : propriétés, signaux et méthodes de la spécification."""

    def __init__(self, title="Morceau", artists=("Artiste",), length_us=200_000_000, art_url=None):
        super().__init__("org.mpris.MediaPlayer2.Player")
        self.status = "Playing"
        self.title, self.artists, self.length_us, self.art_url = (
            title,
            list(artists),
            length_us,
            art_url,
        )
        self.position_us = 12_000_000
        self.calls: list[tuple] = []

    def metadata_value(self):
        meta = {
            "mpris:trackid": Variant("o", TRACK),
            "mpris:length": Variant("x", self.length_us),
            "xesam:title": Variant("s", self.title),
            "xesam:artist": Variant("as", self.artists),
            "xesam:album": Variant("s", "Album"),
        }
        if self.art_url:
            meta["mpris:artUrl"] = Variant("s", self.art_url)
        return meta

    @dbus_property(access=PropertyAccess.READ)
    def PlaybackStatus(self) -> "s":
        return self.status

    @dbus_property(access=PropertyAccess.READ)
    def Metadata(self) -> "a{sv}":
        return self.metadata_value()

    @dbus_property(access=PropertyAccess.READ)
    def Position(self) -> "x":
        return self.position_us

    @dbus_property(access=PropertyAccess.READ)
    def Rate(self) -> "d":
        return 1.0

    @dbus_property(access=PropertyAccess.READ)
    def CanPlay(self) -> "b":
        return True

    @dbus_property(access=PropertyAccess.READ)
    def CanPause(self) -> "b":
        return True

    @dbus_property(access=PropertyAccess.READ)
    def CanGoNext(self) -> "b":
        return True

    @dbus_property(access=PropertyAccess.READ)
    def CanGoPrevious(self) -> "b":
        return True

    @dbus_property(access=PropertyAccess.READ)
    def CanSeek(self) -> "b":
        return True

    @method()
    def PlayPause(self):
        self.calls.append(("PlayPause",))
        self.status = "Paused" if self.status == "Playing" else "Playing"
        self.emit_properties_changed({"PlaybackStatus": self.status})

    @method()
    def Next(self):
        self.calls.append(("Next",))
        self.title = "Morceau suivant"
        self.emit_properties_changed({"Metadata": self.metadata_value()})

    @method()
    def Previous(self):
        self.calls.append(("Previous",))

    @method()
    def Seek(self, offset: "x"):
        self.calls.append(("Seek", offset))

    @method()
    def SetPosition(self, track_id: "o", position: "x"):
        self.calls.append(("SetPosition", track_id, position))
        self.position_us = position

    def change_title(self, title):
        self.title = title
        self.emit_properties_changed({"Metadata": self.metadata_value()})


class ModalPlayer(Player):
    """Un lecteur comme Spotify : lecture aléatoire et répétition modifiables, et `OpenUri`."""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.shuffle = False
        self.loop = "None"

    @dbus_property(access=PropertyAccess.READWRITE)
    def Shuffle(self) -> "b":
        return self.shuffle

    @Shuffle.setter
    def Shuffle(self, value: "b"):
        self.calls.append(("Shuffle", value))
        self.shuffle = value
        self.emit_properties_changed({"Shuffle": value})

    @dbus_property(access=PropertyAccess.READWRITE)
    def LoopStatus(self) -> "s":
        return self.loop

    @LoopStatus.setter
    def LoopStatus(self, value: "s"):
        self.calls.append(("LoopStatus", value))
        self.loop = value
        self.emit_properties_changed({"LoopStatus": value})

    @method()
    def OpenUri(self, uri: "s"):
        self.calls.append(("OpenUri", uri))


async def open_player(address: str, name: str, identity: str, factory=Player, **kwargs):
    """Connecte un faux lecteur au bus et prend le nom MPRIS, comme le ferait une vraie application."""
    bus = await MessageBus(bus_address=address).connect()
    player = factory(**kwargs)
    bus.export("/org/mpris/MediaPlayer2", Root(identity))
    bus.export("/org/mpris/MediaPlayer2", player)
    await bus.request_name(f"org.mpris.MediaPlayer2.{name}")
    return bus, player


@pytest.fixture
async def daemon(tmp_path):
    """Un dbus-daemon de session privé (jamais le vrai bus de la machine)."""
    process = subprocess.Popen(  # noqa: ASYNC220 - démarrage bref d'un démon de test
        [
            "dbus-daemon",
            "--session",
            "--nofork",
            "--nopidfile",
            "--print-address=1",
            f"--address=unix:path={tmp_path / 'bus'}",
        ],
        stdout=subprocess.PIPE,
        text=True,
    )
    address = process.stdout.readline().strip()
    assert address.startswith("unix:"), address
    yield process, address
    process.terminate()
    process.wait(timeout=5)


async def run_provider(address: str):
    provider = MusicProvider(lambda: DbusFastBus(address))
    published: list[dict] = []

    async def publish(data):
        published.append(data)
        return True

    task = asyncio.create_task(provider.run(publish))
    await eventually(lambda: published, timeout=5)
    registry = ActionRegistry()
    register_music_actions(registry, provider)
    return provider, registry, published, task


async def act(registry, name, params=None, act_id="a1"):
    return await registry.invoke(
        Act(act_id, name, params or {}, None), ActionContext(1, "127.0.0.1")
    )


async def shutdown(task, *buses):
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)
    for bus in buses:
        bus.disconnect()


async def test_a_real_player_is_discovered_with_its_metadata(daemon):
    _, address = daemon
    bus, _player = await open_player(address, "spotify", "Spotify", artists=["Artiste", "Invité"])
    _, _, published, task = await run_provider(address)
    try:
        [spotify] = published[-1]["players"]
        assert published[-1]["active"] == "spotify"
        assert spotify["name"] == "Spotify"
        assert spotify["title"] == "Morceau"
        assert spotify["artist"] == "Artiste, Invité"
        assert spotify["album"] == "Album"
        assert spotify["length_ms"] == 200_000
        assert spotify["status"] == "playing"
        assert spotify["position_ms"] >= 12_000
        assert spotify["can"] == {
            "play": True,
            "pause": True,
            "next": True,
            "previous": True,
            "seek": True,
        }
    finally:
        await shutdown(task, bus)


async def test_real_property_changes_reach_the_state_without_any_polling(daemon):
    _, address = daemon
    bus, player = await open_player(address, "spotify", "Spotify")
    _, _, published, task = await run_provider(address)
    try:
        player.change_title("Un autre titre")
        await eventually(
            lambda: published[-1]["players"][0]["title"] == "Un autre titre", timeout=5
        )
        player.status = "Paused"
        player.position_us = 99_000_000
        player.emit_properties_changed({"PlaybackStatus": "Paused"})
        await eventually(lambda: published[-1]["players"][0]["status"] == "paused", timeout=5)
        assert published[-1]["players"][0]["position_ms"] == 99_000  # relevée à la pause
    finally:
        await shutdown(task, bus)


async def test_real_actions_reach_the_real_player(daemon):
    _, address = daemon
    bus, player = await open_player(address, "spotify", "Spotify")
    _, registry, published, task = await run_provider(address)
    try:
        assert (await act(registry, "music.play_pause"))["ok"] is True
        await eventually(lambda: ("PlayPause",) in player.calls, timeout=5)
        await eventually(lambda: published[-1]["players"][0]["status"] == "paused", timeout=5)

        assert (await act(registry, "music.next", act_id="a2"))["ok"] is True
        await eventually(
            lambda: published[-1]["players"][0]["title"] == "Morceau suivant", timeout=5
        )

        assert (await act(registry, "music.previous", act_id="a3"))["ok"] is True
        await eventually(lambda: ("Previous",) in player.calls, timeout=5)

        assert (await act(registry, "music.seek", {"position_ms": 45_000}, act_id="a4"))[
            "ok"
        ] is True
        await eventually(lambda: ("SetPosition", TRACK, 45_000_000) in player.calls, timeout=5)

        fresh = await act(registry, "music.position", act_id="a5")
        assert fresh["r"]["position_ms"] == 45_000
    finally:
        await shutdown(task, bus)


async def test_a_player_started_later_and_one_that_quits_are_followed(daemon):
    _, address = daemon
    _, _, published, task = await run_provider(address)
    try:
        assert published[-1] == {"players": [], "active": None}
        bus, _player = await open_player(address, "vlc", "VLC media player", title="Film")
        await eventually(lambda: published[-1]["active"] == "vlc", timeout=5)
        assert published[-1]["players"][0]["title"] == "Film"
        bus.disconnect()  # l'application se ferme : son nom disparaît du bus
        await eventually(lambda: published[-1] == {"players": [], "active": None}, timeout=5)
    finally:
        await shutdown(task)


async def test_two_browser_instances_with_changing_names_are_handled(daemon):
    _, address = daemon
    bus1, player1 = await open_player(address, "brave.instance9888", "Brave", title="Vidéo A")
    bus2, player2 = await open_player(address, "brave.instance10234", "Brave", title="Vidéo B")
    player2.status = "Paused"
    _, registry, published, task = await run_provider(address)
    try:
        ids = {p["id"] for p in published[-1]["players"]}
        assert ids == {"brave.instance9888", "brave.instance10234"}
        assert published[-1]["active"] == "brave.instance9888"  # celui qui joue
        # On choisit « brave » : le choix vaut pour le navigateur, quelle que soit l'instance.
        assert (await act(registry, "music.select", {"player": "brave"}))["ok"] is True
    finally:
        await shutdown(task, bus1, bus2)
    assert player1.calls == []
    assert player2.calls == []


async def test_a_cover_file_announced_by_a_real_player_is_served(daemon, tmp_path):
    cover = tmp_path / "pochette.png"
    cover.write_bytes(png_bytes())
    _, address = daemon
    bus, _player = await open_player(address, "spotify", "Spotify", art_url=cover.as_uri())
    _, registry, published, task = await run_provider(address)
    try:
        art = published[-1]["players"][0]["art"]
        assert art
        reply = await act(registry, "music.art", {"art": art})
        assert reply["ok"] is True, reply
        assert reply["r"]["mime"].startswith("image/")
    finally:
        await shutdown(task, bus)


async def test_no_session_bus_is_reported_to_the_dock_with_a_message(monkeypatch):
    monkeypatch.setattr(music, "RECONNECT_SECONDS", 60)
    provider = MusicProvider(lambda: DbusFastBus("unix:path=/nonexistent/bus"))
    published: list[dict] = []

    async def publish(data):
        published.append(data)
        return True

    task = asyncio.create_task(provider.run(publish))
    try:
        await eventually(lambda: published, timeout=5)
        assert "injoignable" in published[0]["unavailable"]
    finally:
        await shutdown(task)


async def test_the_dying_session_bus_is_reported_then_the_agent_reconnects(
    daemon, monkeypatch, tmp_path
):
    monkeypatch.setattr(music, "RECONNECT_SECONDS", 0.2)
    process, address = daemon
    bus, _player = await open_player(address, "spotify", "Spotify")
    _, _, published, task = await run_provider(address)
    try:
        process.terminate()  # fin de session : le bus disparaît
        process.wait(timeout=5)
        await eventually(lambda: "unavailable" in published[-1], timeout=5)
        # Le bus revient (nouvelle session) : l'agent se reconnecte tout seul.
        revived = subprocess.Popen(  # noqa: ASYNC220 - démarrage bref d'un démon de test
            [
                "dbus-daemon",
                "--session",
                "--nofork",
                "--nopidfile",
                "--print-address=1",
                f"--address={address}",
            ],
            stdout=subprocess.PIPE,
            text=True,
        )
        try:
            revived.stdout.readline()
            await eventually(lambda: "players" in published[-1], timeout=8)
        finally:
            revived.terminate()
            revived.wait(timeout=5)
    finally:
        await shutdown(task, bus)


# --- lecture aléatoire, répétition et ouverture d'une adresse : par le vrai bus ---------------------------------


async def test_shuffle_and_repeat_are_read_from_and_written_to_a_real_player(daemon):
    _, address = daemon
    bus, player = await open_player(address, "spotify", "Spotify", factory=ModalPlayer)
    _, registry, published, task = await run_provider(address)
    try:
        assert published[-1]["players"][0]["shuffle"] is False
        assert published[-1]["players"][0]["loop"] == "none"

        assert (await act(registry, "music.shuffle", {"enabled": True}))["ok"] is True
        await eventually(lambda: ("Shuffle", True) in player.calls, timeout=5)
        await eventually(lambda: published[-1]["players"][0]["shuffle"] is True, timeout=5)

        for step, (mode, wire) in enumerate(
            (("playlist", "Playlist"), ("track", "Track"), ("none", "None"))
        ):
            assert (await act(registry, "music.repeat", {"mode": mode}, act_id=f"r{step}"))[
                "ok"
            ] is True
            await eventually(lambda wire=wire: ("LoopStatus", wire) in player.calls, timeout=5)
            await eventually(
                lambda mode=mode: published[-1]["players"][0]["loop"] == mode, timeout=5
            )

        assert (await act(registry, "music.shuffle", {"enabled": False}, act_id="s2"))["ok"] is True
        await eventually(lambda: published[-1]["players"][0]["shuffle"] is False, timeout=5)
    finally:
        await shutdown(task, bus)


async def test_a_real_player_without_shuffle_or_repeat_has_none_and_refuses_politely(daemon):
    _, address = daemon
    bus, player = await open_player(
        address, "brave.instance1", "Brave"
    )  # pas de Shuffle ni de LoopStatus
    _, registry, published, task = await run_provider(address)
    try:
        assert published[-1]["players"][0]["shuffle"] is None
        assert published[-1]["players"][0]["loop"] is None
        shuffle = await act(registry, "music.shuffle", {"enabled": True})
        assert (shuffle["ok"], shuffle["e"]["code"]) == (False, "unsupported")
        repeat = await act(registry, "music.repeat", {"mode": "track"}, act_id="a2")
        assert (repeat["ok"], repeat["e"]["code"]) == (False, "unsupported")
    finally:
        await shutdown(task, bus)
    assert player.calls == []


async def test_the_spotify_service_makes_a_real_player_open_an_album_without_an_account(
    daemon, tmp_path
):
    """Sans compte, « jouer » passe par `OpenUri` : l'appel arrive vraiment au lecteur, avec la bonne adresse."""
    from mydock_agent.spotify.auth import TokenStore
    from mydock_agent.spotify.service import SpotifyService

    class NoApps:
        apps: list = []

        async def refresh(self):
            return False

        async def launch(self, _app_id):  # pragma: no cover - le lecteur est déjà ouvert
            raise AssertionError("Spotify tourne déjà")

    album = "spotify:album:4aawyAB9vmqN3uQ7FjRGTy"
    track = "spotify:track:0ETFjACtuP2ADo6LFhL6HN"
    _, address = daemon
    bus, player = await open_player(address, "spotify", "Spotify", factory=ModalPlayer)
    provider, _, _, task = await run_provider(address)
    try:
        service = SpotifyService(
            music=provider, apps=NoApps(), store=TokenStore(tmp_path / "absent.toml")
        )
        assert provider.has_player("spotify")
        assert not provider.has_player("vlc")
        assert await service.play(album) == {"via": "player", "started": False}
        await eventually(lambda: ("OpenUri", album) in player.calls, timeout=5)
        # Un morceau touché dans son album : c'est l'album entier que le lecteur ouvre (« suivant » enchaîne).
        await service.play(track, album)
        await eventually(lambda: player.calls.count(("OpenUri", album)) == 2, timeout=5)
        await service.play(track)  # sans contexte : le morceau seul
        await eventually(lambda: ("OpenUri", track) in player.calls, timeout=5)
    finally:
        await shutdown(task, bus)
