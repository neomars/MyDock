"""Son : lecture de pactl, état publié et actions.

Les réponses de `pactl -f json` viennent d'un VRAI pactl 16.1 face à un VRAI PipeWire (voir
`data/pactl-16.1/`) ; le portable fictif de la démonstration fournit le matériel que le bac à sable
n'a pas (prises HDMI sans écran, Bluetooth, micro de webcam). Le serveur de son réel est exercé de
bout en bout dans `test_audio_pipewire.py`.
"""

import asyncio
import contextlib
import json
import os
from pathlib import Path

import pytest

from mydock_agent.collectors import audio
from mydock_agent.collectors.audio import (
    ALL_PARTS,
    AudioProvider,
    PactlError,
    PactlMissing,
    RawAudio,
    SubprocessPactl,
    build_snapshot,
    channel_values,
    device_kind,
    device_label,
    parse_event,
    percent_of,
    register_audio_actions,
    scaled_volumes,
    volume_percent,
)
from mydock_agent.demo.audio import DemoPactl, device, port, stream, volume
from mydock_agent.testing import eventually
from mydock_common.actions import ActionContext, ActionError, ActionRegistry
from mydock_common.protocol import Act

DATA = Path(__file__).parent / "data" / "pactl-16.1"


def load(name: str):
    return json.loads((DATA / f"{name}.json").read_text(encoding="utf-8"))


def real_raw() -> RawAudio:
    info = load("info")
    return RawAudio(
        default_sink=info["default_sink_name"],
        default_source=info["default_source_name"],
        sinks=load("sinks"),
        sources=load("sources"),
        inputs=load("sink-inputs"),
        outputs=load("source-outputs"),
    )


# --- volumes -----------------------------------------------------------------------------------


def test_percent_rounds_like_pulseaudio():
    assert [percent_of(v) for v in (0, 327, 328, 19661, 32768, 65536, 98304)] == [
        0,
        0,
        1,
        30,
        50,
        100,
        150,
    ]


def test_the_displayed_volume_is_that_of_the_loudest_channel():
    left_right = {"front-left": {"value": 65536}, "front-right": {"value": 32768}}
    assert volume_percent(left_right) == 100
    assert channel_values(left_right) == [65536, 32768]


@pytest.mark.parametrize(
    "junk", [None, {}, [], "50%", {"front-left": "loud"}, {"a": {"value": True}}]
)
def test_an_unreadable_volume_is_unknown_rather_than_a_guess(junk):
    assert volume_percent(junk) is None


@pytest.mark.parametrize(
    ("current", "percent", "expected"),
    [
        ([65536, 65536], 50, [32768, 32768]),
        ([65536, 32768], 60, [39322, 19661]),  # l'équilibre 100/50 est conservé
        ([32768, 65536], 60, [19661, 39322]),
        ([0, 0], 40, [26214, 26214]),  # rien à conserver : tous les canaux au niveau demandé
        ([], 100, [65536]),
        ([20000], 0, [0]),
        ([65536, 0], 50, [32768, 0]),  # un canal coupé reste coupé
        ([65536] * 6, 100, [65536] * 6),
    ],
)
def test_scaled_volumes_keep_the_left_right_balance(current, percent, expected):
    assert scaled_volumes(current, percent) == expected


@pytest.mark.parametrize("percent", range(0, 101, 7))
def test_the_loudest_channel_lands_exactly_on_the_requested_percent(percent):
    values = scaled_volumes([50000, 31000, 42000], percent)
    assert percent_of(max(values)) == percent


# --- événements --------------------------------------------------------------------------------


def test_events_are_parsed_in_the_C_locale_only():
    assert parse_event("Event 'change' on sink #45\n") == ("change", "sink", 45)
    assert parse_event("Event 'new' on sink-input #90") == ("new", "sink-input", 90)
    assert parse_event("Event 'remove' on source-output #12  \n") == ("remove", "source-output", 12)
    # Une session en français traduit la ligne : c'est pourquoi l'agent force LC_ALL=C.
    assert parse_event("Événement « change » sur sink #45") is None
    assert parse_event("") is None
    assert parse_event("Got SIGINT, exiting.") is None


# --- familles et noms --------------------------------------------------------------------------


def dev(name, description="", *, form=None, bus=None, ports=(), active=None):
    props = {}
    if form:
        props["device.form_factor"] = form
    if bus:
        props["device.bus"] = bus
    return {
        "name": name,
        "description": description or name,
        "properties": props,
        "ports": [port(n, d, t, a) for n, d, t, a in ports],
        "active_port": active,
    }


UNKNOWN = "availability unknown"


@pytest.mark.parametrize(
    ("device_", "kind", "label"),
    [
        (
            dev(
                "alsa_output.pci-0000_00_1f.3.analog-stereo",
                "Built-in Audio Analog Stereo",
                form="internal",
                bus="pci",
                ports=[("analog-output-speaker", "Speakers", "Speaker", UNKNOWN)],
                active="analog-output-speaker",
            ),
            "speakers",
            "Haut-parleurs",
        ),
        (  # carte à profils UCM : la prise est dans le nom, pas dans le type d'appareil
            dev("alsa_output.pci-0000_00_1f.3-platform-skl_hda_dsp_generic.HiFi__Speaker__sink"),
            "speakers",
            "Haut-parleurs",
        ),
        (
            dev("alsa_output.pci-0000_00_1f.3-platform-skl_hda_dsp_generic.HiFi__Headphones__sink"),
            "headphones",
            "Casque",
        ),
        (  # la prise active passe sur le casque : même carte, autre nom
            dev(
                "alsa_output.pci-0000_00_1f.3.analog-stereo",
                form="internal",
                ports=[
                    ("analog-output-speaker", "Speakers", "Speaker", UNKNOWN),
                    ("analog-output-headphones", "Headphones", "Headphones", "available"),
                ],
                active="analog-output-headphones",
            ),
            "headphones",
            "Casque",
        ),
        (
            dev(
                "alsa_output.pci-0000_00_1f.3.hdmi-stereo-extra2",
                "Built-in Audio Digital Stereo (HDMI 2)",
            ),
            "hdmi",
            "HDMI 2",
        ),
        (  # NVIDIA : aucun numéro dans le nom
            dev(
                "alsa_output.pci-0000_01_00.1.hdmi-stereo",
                "TU116 High Definition Audio Controller Digital Stereo (HDMI)",
            ),
            "hdmi",
            "HDMI",
        ),
        (
            dev("bluez_output.80_C3_BA_12_34_56.1", "WH-1000XM4", bus="bluetooth"),
            "bluetooth",
            "WH-1000XM4",
        ),
        (
            dev(
                "alsa_output.usb-Focusrite_Scarlett_2i2-00.analog-stereo",
                "Scarlett 2i2 USB Analog Stereo",
                bus="usb",
            ),
            "usb",
            "Scarlett 2i2 USB",
        ),
        (dev("easyeffects_sink", "Easy Effects Sink"), "other", "Easy Effects Sink"),
    ],
)
def test_outputs_get_a_family_and_a_french_name(device_, kind, label):
    assert device_kind(device_, source=False) == kind
    assert device_label(kind, device_["description"], source=False) == label


@pytest.mark.parametrize(
    ("device_", "kind", "label"),
    [
        (
            dev(
                "alsa_input.pci-0000_00_1f.3.analog-stereo",
                form="internal",
                ports=[("analog-input-internal-mic", "Internal Microphone", "Mic", UNKNOWN)],
                active="analog-input-internal-mic",
            ),
            "microphone",
            "Micro intégré",
        ),
        (
            dev("alsa_input.usb-046d_C920-02.analog-stereo", "C920 Analog Stereo", bus="usb"),
            "usb",
            "C920",
        ),
        (
            dev("alsa_input.pci-0000_00_1f.3-platform-skl_hda_dsp_generic.HiFi__Headset__source"),
            "headphones",
            "Micro du casque",
        ),
        (
            dev(
                "alsa_input.pci-0000_00_1f.3.analog-stereo",
                ports=[("analog-input-linein", "Line In", "Line", UNKNOWN)],
                active="analog-input-linein",
            ),
            "line",
            "Entrée ligne",
        ),
        (
            dev("bluez_input.80_C3_BA_12_34_56", "WH-1000XM4", bus="bluetooth"),
            "bluetooth",
            "WH-1000XM4",
        ),
    ],
)
def test_microphones_get_a_family_and_a_french_name(device_, kind, label):
    assert device_kind(device_, source=True) == kind
    assert device_label(kind, device_["description"], source=True) == label


def test_two_outputs_never_share_a_name_on_screen():
    sinks = [
        dev("hdmi_a", "Carte A HDMI", form=None),
        dev("hdmi_b", "Carte B HDMI", form=None),
        dev("bluez_output.AA.1", "Casque", bus="bluetooth"),
        dev("bluez_output.BB.1", "Casque", bus="bluetooth"),
    ]
    items = audio._devices(sinks, source=False, keep=None)
    labels = [i["label"] for i in items]
    assert len(set(labels)) == 4, labels
    assert {"Carte A HDMI", "Carte B HDMI"} <= set(labels)
    assert "Casque" in labels
    assert "Casque (2)" in labels


# --- les noms que pactl ne sait pas écrire -----------------------------------------------------
# Le JSON de pactl (16 et 17) remplace tout texte accentué par « (null) » : « Écouteurs de Marie »,
# une application, un titre. C'est vérifié contre un vrai PipeWire dans test_audio_pipewire.py.


def test_a_text_lost_by_pactl_is_never_displayed():
    assert audio.readable("(null)") is None
    assert audio.readable("") is None
    assert audio.readable("   ") is None
    assert audio.readable(None) is None
    assert (
        audio.readable("Écouteurs de Marie") == "Écouteurs de Marie"
    )  # un vrai texte accentué reste intact


@pytest.mark.parametrize(
    ("name", "source", "label"),
    [
        ("bluez_output.80_C3_BA_12_34_56.1", False, "Bluetooth 12:34:56"),
        ("bluez_input.80_c3_ba_ab_cd_ef", True, "Bluetooth AB:CD:EF"),
        (
            "alsa_output.usb-Focusrite_Scarlett_2i2_USB-00.analog-stereo",
            False,
            "Focusrite Scarlett 2i2 USB",
        ),
        (
            "alsa_input.usb-046d_HD_Pro_Webcam_C920-02.analog-stereo",
            True,
            "046d HD Pro Webcam C920",
        ),
        ("alsa_output.pci-0000_00_1f.3.analog-stereo", False, "Sortie audio"),
        ("alsa_input.pci-0000_00_1f.3.analog-stereo", True, "Entrée audio"),
        ("quelquechose", False, "Sortie audio"),
    ],
)
def test_a_lost_description_is_rebuilt_from_the_technical_name(name, source, label):
    assert audio.label_from_name(name, source=source) == label


def test_other_properties_name_a_device_whose_description_was_lost():
    device_ = dev("alsa_output.usb-X_Y-00.analog-stereo", "(null)", bus="usb")
    device_["properties"]["device.product.name"] = "Scarlett 2i2"
    assert audio.device_item(device_, source=False)["label"] == "Scarlett 2i2"
    device_["properties"]["device.product.name"] = "(null)"
    assert (
        audio.device_item(device_, source=False)["label"] == "X Y"
    )  # le nom technique, en dernier


def test_an_application_whose_name_was_lost_is_named_after_its_program():
    lost = "(null)"
    assert (
        audio.stream_name(
            {"properties": {"application.name": lost, "application.process.binary": "spotify"}}
        )
        == "Spotify"
    )
    assert (
        audio.stream_name(
            {"properties": {"application.name": lost, "media.name": lost, "node.name": "pw-cat"}}
        )
        == "pw-cat"
    )
    assert (
        audio.stream_name({"properties": {"application.name": lost, "media.name": lost}})
        == "Application"
    )


def _demo_raw() -> RawAudio:
    pactl = DemoPactl()
    return RawAudio(
        pactl.default_sink,
        pactl.default_source,
        pactl.sinks,
        pactl.sources,
        pactl.inputs,
        pactl.outputs,
    )


def lose_every_property(raw: RawAudio) -> RawAudio:
    """La même machine, mais tous les textes libres (descriptions, propriétés) sont perdus."""

    def scrub(item):
        item = json.loads(json.dumps(item))
        if "description" in item:
            item["description"] = "(null)"
        item["properties"] = {
            k: ("(null)" if isinstance(v, str) and k not in KEEP else v)
            for k, v in item.get("properties", {}).items()
        }
        for p in item.get("ports", []):
            p["description"] = "(null)"
        return item

    return RawAudio(
        raw.default_sink,
        raw.default_source,
        [scrub(s) for s in raw.sinks],
        [scrub(s) for s in raw.sources],
        [scrub(s) for s in raw.inputs],
        [scrub(s) for s in raw.outputs],
    )


# Ce que pactl écrit sans accent, parce que ce sont des identifiants et non des textes libres.
KEEP = {"device.class", "device.form_factor", "device.bus", "media.role", "node.name"}


@pytest.mark.parametrize("make", [real_raw, _demo_raw])
def test_nothing_displayed_can_be_null_text_even_if_every_free_text_is_lost(make):
    snapshot = build_snapshot(lose_every_property(make()))
    assert snapshot["sinks"]
    assert snapshot["apps"]
    assert "(null)" not in json.dumps(snapshot, ensure_ascii=False)


# --- l'état publié -----------------------------------------------------------------------------


def test_a_real_pactl_answer_becomes_the_published_state():
    snapshot = build_snapshot(real_raw())
    assert snapshot["max"] == 100
    assert (snapshot["default_sink"], snapshot["default_source"]) == ("casque", "micro_test")
    assert [(s["id"], s["kind"], s["label"]) for s in snapshot["sinks"]] == [
        ("casque", "headphones", "Casque"),
        ("hdmi_ecran", "hdmi", "HDMI"),
    ]
    # Les « moniteurs » (enregistrer ce que joue le PC) ne sont pas des micros.
    assert [s["id"] for s in snapshot["sources"]] == ["micro_test"]
    # Ni le son d'événement du bureau, ni l'indicateur de niveau de pavucontrol : pas des applications.
    assert [(a["name"], a["playing"], a["sink"]) for a in snapshot["apps"]] == [
        ("Spotify", True, "casque")
    ]
    assert [(r["name"], r["source"]) for r in snapshot["recorders"]] == [("Discord", "micro_test")]
    json.dumps(snapshot)


def test_a_recording_stream_on_a_monitor_is_not_a_microphone_in_use():
    raw = real_raw()
    micro = next(s for s in raw.sources if s["name"] == "micro_test")
    monitor = next(s for s in raw.sources if s["name"].endswith(".monitor"))
    for output in raw.outputs:
        output["source"] = monitor["index"]
    assert build_snapshot(raw)["recorders"] == []
    for output in raw.outputs:
        output["source"] = micro["index"]
        output["corked"] = True  # en pause : il n'écoute rien
    assert build_snapshot(raw)["recorders"] == []


def test_unplugged_outputs_and_the_dummy_output_are_hidden_unless_they_are_the_default():
    pactl = DemoPactl()
    raw = RawAudio(sinks=pactl.sinks, sources=pactl.sources)
    assert "alsa_output.pci-0000_00_1f.3.hdmi-stereo-extra2" not in [
        s["id"] for s in build_snapshot(raw)["sinks"]
    ]
    raw.default_sink = "alsa_output.pci-0000_00_1f.3.hdmi-stereo-extra2"
    assert raw.default_sink in [s["id"] for s in build_snapshot(raw)["sinks"]]
    dummy = device(1, "auto_null", "Dummy Output", [100, 100], props={})
    assert build_snapshot(RawAudio(default_sink="auto_null", sinks=[dummy]))["sinks"] == []
    assert build_snapshot(RawAudio(default_sink="auto_null", sinks=[dummy]))["default_sink"] is None


def test_spotify_stays_first_and_the_others_keep_their_order_of_arrival():
    raw = RawAudio(
        sinks=[device(5, "out", "Out", [100, 100], props={})],
        inputs=[
            stream(30, "Firefox", [100, 100], to=5),
            stream(10, "Discord", [100, 100], to=5),
            stream(20, "spotify", [100, 100], to=5),
        ],
    )
    names = [a["name"] for a in build_snapshot(raw)["apps"]]
    assert names == ["Spotify", "Discord", "Firefox"]  # « spotify » est écrit comme il faut


def test_applications_are_named_after_the_program_not_its_plumbing():
    cases = {
        "ALSA plug-in [python3.13]": "python3.13",
        "  Mon\x00lecteur\n": "Mon lecteur",
        "x" * 200: "x" * audio.NAME_LIMIT,
    }
    for given, shown in cases.items():
        assert audio.stream_name({"properties": {"application.name": given}}) == shown
    assert audio.stream_name({"properties": {}}) == "Application"
    assert audio.stream_name({"properties": {"media.name": "Lecture"}}) == "Lecture"


def test_a_device_with_an_option_like_name_is_never_offered():
    bad = device(1, "-h", "Piège", [100, 100], props={})
    ok = device(2, "carte", "Carte", [100, 100], props={})
    assert [s["id"] for s in build_snapshot(RawAudio(sinks=[bad, ok]))["sinks"]] == ["carte"]


def test_the_number_of_listed_applications_is_bounded():
    sink = device(1, "out", "Out", [100, 100], props={})
    streams = [stream(100 + i, f"App {i}", [100, 100], to=1) for i in range(60)]
    assert len(build_snapshot(RawAudio(sinks=[sink], inputs=streams))["apps"]) == audio.MAX_APPS


# --- le fournisseur ----------------------------------------------------------------------------


@contextlib.asynccontextmanager
async def running(pactl: DemoPactl | None = None):
    pactl = pactl or DemoPactl()
    provider = AudioProvider(pactl)
    published: list[dict] = []

    async def publish(data):
        published.append(data)
        return True

    task = asyncio.create_task(provider.run(publish))
    try:
        yield provider, pactl, published
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


async def settle():
    await asyncio.sleep(audio.COALESCE_SECONDS * 4)


def sink_volume(snapshot, name):
    return next(s["volume"] for s in snapshot["sinks"] if s["id"] == name)


async def test_the_state_is_published_as_soon_as_the_server_answers():
    async with running() as (provider, pactl, published):
        await eventually(lambda: published)
        assert published[0]["default_sink"] == pactl.default_sink
        assert pactl.subscribers == 1
        assert provider.topic == "audio"
        assert provider.lazy is True  # personne n'écoute : pas de `pactl subscribe`
    assert pactl.subscribers == 0  # le flux d'événements est libéré à l'arrêt


async def test_a_change_made_elsewhere_is_published_once():
    async with running() as (_provider, pactl, published):
        await eventually(lambda: published)
        before = len(published)
        await pactl.command("set-sink-volume", pactl.default_sink, "20000")  # le clavier du PC
        await eventually(lambda: len(published) > before)
        await settle()
        assert len(published) == before + 1
        assert sink_volume(published[-1], pactl.default_sink) == 31


async def test_a_burst_of_events_costs_a_single_reading():
    async with running() as (_provider, pactl, published):
        await eventually(lambda: published)
        pactl.queries.clear()
        before = len(published)
        for _ in range(30):
            pactl.touch("sink", 51)
        await settle()
        assert pactl.queries == [("list", "sinks")]
        assert (
            len(published) <= before + 1
        )  # rien n'a changé : le hub n'enverrait rien de toute façon


@pytest.mark.parametrize(
    ("facility", "reads"),
    [
        ("sink", [("list", "sinks")]),
        ("source", [("list", "sources")]),
        ("sink-input", [("list", "sink-inputs")]),
        ("source-output", [("list", "source-outputs")]),
        ("server", [("info",)]),
        ("client", []),  # chaque appel à pactl crée un client : jamais de boucle
        ("module", []),
        ("card", []),
    ],
)
async def test_only_the_part_that_changed_is_read_again(facility, reads):
    async with running() as (_provider, pactl, published):
        await eventually(lambda: published)
        pactl.queries.clear()
        pactl.touch(facility, 7)
        await settle()
        assert pactl.queries == reads


async def test_the_provider_recovers_when_the_sound_server_restarts(monkeypatch):
    monkeypatch.setattr(audio, "RETRY_SECONDS", 0.05)
    async with running() as (_provider, pactl, published):
        await eventually(lambda: published)
        pactl.drop()
        await eventually(lambda: "unavailable" in published[-1])
        assert "s'est arrêté" in published[-1]["unavailable"]
        await eventually(lambda: "unavailable" not in published[-1] and pactl.subscribers == 1)
        assert published[-1]["sinks"]


async def test_a_missing_pactl_is_explained_then_forgotten_once_installed(monkeypatch):
    monkeypatch.setattr(audio, "RETRY_SECONDS", 0.05)
    pactl = DemoPactl()
    pactl.fail = PactlMissing("pactl est introuvable.")
    async with running(pactl) as (_provider, _pactl, published):
        await eventually(lambda: published)
        assert "pulseaudio-utils" in published[0]["unavailable"]
        pactl.fail = None
        await eventually(lambda: "unavailable" not in published[-1])


async def test_a_server_that_refuses_is_reported_with_pactls_own_words(monkeypatch):
    monkeypatch.setattr(audio, "RETRY_SECONDS", 0.05)
    pactl = DemoPactl()
    pactl.fail = PactlError("Connection failure: Connection refused")
    async with running(pactl) as (_provider, _pactl, published):
        await eventually(lambda: published)
        message = published[0]["unavailable"]
        assert "Connection refused" in message
        assert "Nouvelle tentative dans" in message


# --- les actions -------------------------------------------------------------------------------


def last(pactl: DemoPactl):
    return pactl.commands[-1]


async def test_setting_the_volume_of_an_output():
    async with running() as (provider, pactl, published):
        await eventually(lambda: published)
        await provider.set_volume("sink", pactl.default_sink, 30)
        assert last(pactl) == ("set-sink-volume", pactl.default_sink, "19661")
        await eventually(lambda: sink_volume(published[-1], pactl.default_sink) == 30)


async def test_the_left_right_balance_set_in_the_desktop_survives_the_dock():
    pactl = DemoPactl()
    pactl.sinks[0]["volume"] = volume([100, 50])
    async with running(pactl) as (provider, _pactl, published):
        await eventually(lambda: published)
        await provider.set_volume("sink", pactl.default_sink, 60)
        assert last(pactl) == ("set-sink-volume", pactl.default_sink, "39322", "19661")


async def test_a_mono_microphone_takes_a_single_value():
    pactl = DemoPactl()
    pactl.sources[0]["volume"] = volume([70])
    async with running(pactl) as (provider, _pactl, published):
        await eventually(lambda: published)
        await provider.set_volume("source", pactl.default_source, 50)
        assert last(pactl) == ("set-source-volume", pactl.default_source, "32768")


async def test_setting_the_volume_of_an_application():
    async with running() as (provider, pactl, published):
        await eventually(lambda: published)
        await provider.set_volume("app", "101", 40)
        assert last(pactl) == ("set-sink-input-volume", "101", "26214")
        await eventually(
            lambda: next(a for a in published[-1]["apps"] if a["id"] == "101")["volume"] == 40
        )


@pytest.mark.parametrize(
    ("kind", "ident", "verb"),
    [
        ("sink", "alsa_output.pci-0000_00_1f.3.analog-stereo", "set-sink-mute"),
        ("source", "alsa_input.pci-0000_00_1f.3.analog-stereo", "set-source-mute"),
        ("app", "103", "set-sink-input-mute"),
    ],
)
async def test_muting_and_unmuting(kind, ident, verb):
    async with running() as (provider, pactl, published):
        await eventually(lambda: published)
        await provider.set_mute(kind, ident, True)
        assert last(pactl) == (verb, ident, "1")
        await provider.set_mute(kind, ident, False)
        assert last(pactl) == (verb, ident, "0")


async def test_changing_the_default_output_and_microphone():
    async with running() as (provider, pactl, published):
        await eventually(lambda: published)
        await provider.set_default("sink", "bluez_output.80_C3_BA_12_34_56.1")
        await eventually(
            lambda: published[-1]["default_sink"] == "bluez_output.80_C3_BA_12_34_56.1"
        )
        assert last(pactl) == ("set-default-sink", "bluez_output.80_C3_BA_12_34_56.1")
        mic = "alsa_input.usb-046d_HD_Pro_Webcam_C920-02.analog-stereo"
        await provider.set_default("source", mic)
        await eventually(lambda: published[-1]["default_source"] == mic)


@pytest.mark.parametrize(
    ("kind", "ident"),
    [
        ("sink", "n_existe_pas"),
        ("sink", "alsa_output.pci-0000_00_1f.3.hdmi-stereo-extra2"),  # HDMI sans écran : caché
        (
            "source",
            "alsa_output.pci-0000_00_1f.3.analog-stereo.monitor",
        ),  # un moniteur n'est pas un micro
        ("app", "999"),
        ("app", "104"),  # le son d'événement du bureau n'est pas une application
        ("app", "202"),
    ],
)
async def test_only_what_the_screen_shows_can_be_controlled(kind, ident):
    async with running() as (provider, pactl, published):
        await eventually(lambda: published)
        with pytest.raises(ActionError) as caught:
            await provider.set_volume(kind, ident, 10)
        assert caught.value.code == "not_found"
        with pytest.raises(ActionError):
            await provider.set_mute(kind, ident, True)
        assert pactl.commands == []


async def test_an_action_works_even_when_nobody_is_subscribed():
    pactl = DemoPactl()
    provider = AudioProvider(pactl)  # jamais démarré : l'état est lu à la demande
    await provider.set_volume("sink", pactl.default_sink, 25)
    assert last(pactl) == ("set-sink-volume", pactl.default_sink, "16384")


async def test_a_vanished_element_is_reported_as_gone_and_other_failures_as_failures():
    pactl = DemoPactl()
    provider = AudioProvider(pactl)
    await provider.read_all()
    pactl.fail_commands = PactlError("Failed to set sink volume: No such entity", gone=True)
    with pytest.raises(ActionError) as caught:
        await provider.set_volume("sink", pactl.default_sink, 10)
    assert caught.value.code == "not_found"
    pactl.fail_commands = PactlError("Access denied")
    with pytest.raises(ActionError) as caught:
        await provider.set_mute("sink", pactl.default_sink, True)
    assert caught.value.code == "failed"
    assert "Access denied" in caught.value.message


async def test_an_action_says_so_when_there_is_no_sound_server():
    pactl = DemoPactl()
    pactl.fail = PactlError("Connection failure: Connection refused")
    provider = AudioProvider(pactl)
    with pytest.raises(ActionError) as caught:
        await provider.set_volume("sink", "x", 10)
    assert caught.value.code == "unavailable"
    assert "Connection refused" in caught.value.message


class SlowPactl(DemoPactl):
    async def command(self, *args):
        await asyncio.sleep(0.03)
        await super().command(*args)


async def test_a_dragged_slider_applies_its_last_value_and_skips_the_ones_in_between():
    pactl = SlowPactl()
    provider = AudioProvider(pactl)
    name = pactl.default_sink
    await asyncio.gather(*(provider.set_volume("sink", name, v) for v in (10, 20, 30, 40, 50, 60)))
    values = [audio.percent_of(int(c[2])) for c in pactl.commands]
    assert values[-1] == 60  # la dernière valeur gagne, toujours
    assert len(values) <= 2  # les valeurs intermédiaires périmées ne sont pas appliquées
    assert volume_percent(pactl.sinks[0]["volume"]) == 60


async def test_two_different_targets_do_not_swallow_each_other():
    pactl = SlowPactl()
    provider = AudioProvider(pactl)
    await asyncio.gather(
        provider.set_volume("sink", pactl.default_sink, 20),
        provider.set_volume("app", "101", 70),
        provider.set_volume("source", pactl.default_source, 45),
    )
    assert {c[0] for c in pactl.commands} == {
        "set-sink-volume",
        "set-sink-input-volume",
        "set-source-volume",
    }


# --- les actions, telles que le dock les envoie -------------------------------------------------


@pytest.fixture
def registry():
    pactl = DemoPactl()
    provider = AudioProvider(pactl)
    reg = ActionRegistry()
    register_audio_actions(reg, provider)
    reg.pactl = pactl  # le test regarde ce que le « serveur de son » a reçu
    return reg


async def call(reg, name, params):
    return await reg.invoke(Act("t1", name, params), ActionContext(1, "127.0.0.1"))


def test_the_audio_actions_are_not_sensitive(registry):
    assert registry.names == ["audio.set_default", "audio.set_mute", "audio.set_volume"]
    assert not any(registry.is_sensitive(n) for n in registry.names)


async def test_a_valid_request_is_executed(registry):
    name = registry.pactl.default_sink
    reply = await call(registry, "audio.set_volume", {"kind": "sink", "id": name, "percent": 42})
    assert reply["ok"] is True
    assert registry.pactl.commands[-1][:2] == ("set-sink-volume", name)
    reply = await call(registry, "audio.set_mute", {"kind": "app", "id": "103", "muted": True})
    assert reply["ok"] is True
    reply = await call(registry, "audio.set_default", {"kind": "sink", "id": name})
    assert reply["ok"] is True


@pytest.mark.parametrize(
    "params",
    [
        {"kind": "sink", "id": "x", "percent": 101},  # le dock n'amplifie pas
        {"kind": "sink", "id": "x", "percent": -1},
        {"kind": "sink", "id": "x", "percent": 50.5},
        {"kind": "sink", "id": "x", "percent": "50"},
        {"kind": "sink", "id": "x", "percent": True},
        {"kind": "monitor", "id": "x", "percent": 50},
        {"kind": "sink", "id": "-h", "percent": 50},  # ressemble à une option de pactl
        {"kind": "sink", "id": "a b", "percent": 50},
        {"kind": "sink", "id": "x" * 201, "percent": 50},
        {"kind": "sink", "id": "x", "percent": 50, "cmd": "rm -rf /"},
        {"kind": "sink", "id": "x"},
        {"id": "x", "percent": 50},
    ],
)
async def test_malformed_volume_requests_never_reach_the_sound_server(registry, params):
    reply = await call(registry, "audio.set_volume", params)
    assert reply["ok"] is False
    assert reply["e"]["code"] == "bad_params"
    assert registry.pactl.commands == []


@pytest.mark.parametrize(
    ("name", "params"),
    [
        ("audio.set_mute", {"kind": "sink", "id": "x", "muted": "yes"}),
        ("audio.set_mute", {"kind": "sink", "id": "x", "muted": 1}),
        ("audio.set_default", {"kind": "app", "id": "101"}),  # une application n'est pas une sortie
        ("audio.set_default", {"kind": "sink", "id": "x", "extra": 1}),
    ],
)
async def test_malformed_mute_and_default_requests_are_refused(registry, name, params):
    reply = await call(registry, name, params)
    assert reply["e"]["code"] == "bad_params"
    assert registry.pactl.commands == []


# --- le vrai pactl, remplacé par un script ------------------------------------------------------


def fake_pactl(tmp_path: Path, body: str) -> str:
    path = tmp_path / "pactl"
    path.write_text("#!/bin/sh\n" + body, encoding="utf-8")
    path.chmod(0o755)
    return str(path)


def gone(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return True
    return False


async def test_pactl_is_always_run_in_the_C_locale_and_asked_for_json(tmp_path):
    script = fake_pactl(
        tmp_path,
        'printf \'{"lc": "%s", "language": "%s", "args": "%s"}\' "$LC_ALL" "${LANGUAGE-unset}" "$*"\n',
    )
    env = {"PATH": os.environ["PATH"], "LC_ALL": "fr_FR.UTF-8", "LANGUAGE": "fr:en"}
    answer = await SubprocessPactl(script, env=env).query("list", "sinks")
    assert answer == {"lc": "C", "language": "unset", "args": "-f json list sinks"}


async def test_a_failing_pactl_reports_its_first_error_line(tmp_path):
    script = fake_pactl(
        tmp_path,
        "echo 'Failed to get sink information: No such entity' >&2\necho second >&2\nexit 1\n",
    )
    with pytest.raises(PactlError) as caught:
        await SubprocessPactl(script).command("set-sink-volume", "x", "1")
    assert str(caught.value) == "Failed to get sink information: No such entity"
    assert caught.value.gone is True
    script = fake_pactl(tmp_path, "echo 'Connection failure: Connection refused' >&2\nexit 1\n")
    with pytest.raises(PactlError) as caught:
        await SubprocessPactl(script).query("info")
    assert caught.value.gone is False


async def test_a_pactl_that_hangs_is_killed(tmp_path, monkeypatch):
    monkeypatch.setattr(audio, "COMMAND_TIMEOUT", 0.2)
    pidfile = tmp_path / "pid"
    script = fake_pactl(tmp_path, f"echo $$ > {pidfile}\nexec sleep 30\n")
    with pytest.raises(PactlError, match="ne répond pas"):
        await SubprocessPactl(script).query("info")
    await eventually(lambda: gone(int(pidfile.read_text())), timeout=2)


async def test_a_missing_pactl_is_a_specific_error(tmp_path):
    with pytest.raises(PactlMissing):
        await SubprocessPactl(str(tmp_path / "absent")).query("info")


async def test_an_old_pactl_without_json_is_recognised(tmp_path):
    script = fake_pactl(tmp_path, "echo 'Server Name: pulseaudio'\n")
    with pytest.raises(PactlError, match="16"):
        await SubprocessPactl(script).query("info")


async def test_the_event_stream_is_read_line_by_line_and_stopped_with_the_context(tmp_path):
    pidfile = tmp_path / "pid"
    script = fake_pactl(
        tmp_path,
        f"echo $$ > {pidfile}\necho \"Event 'change' on sink #1\"\necho \"Event 'new' on client #2\"\nexec sleep 30\n",
    )
    async with SubprocessPactl(script).subscribe() as lines:
        assert await anext(lines) == "Event 'change' on sink #1\n"
        assert await anext(lines) == "Event 'new' on client #2\n"
    await eventually(lambda: gone(int(pidfile.read_text())), timeout=3)


async def test_the_event_stream_ends_when_the_sound_server_goes_away(tmp_path):
    script = fake_pactl(tmp_path, "echo \"Event 'change' on sink #1\"\nexit 1\n")
    async with SubprocessPactl(script).subscribe() as lines:
        assert [line async for line in lines] == ["Event 'change' on sink #1\n"]


def test_every_part_is_read_at_start():
    assert {"info", "sinks", "sources", "inputs", "outputs"} == ALL_PARTS
