"""L'association par code : forme du code, durée de vie, essais limités, une demande par poignée de secondes."""

import pytest

from mydock_dock.pairing import (
    ALPHABET,
    CODE_LENGTH,
    MAX_TRACKED_PEERS,
    MAX_WRONG,
    SESSION_SECONDS,
    THROTTLE_MAX,
    THROTTLE_WINDOW,
    Pairing,
    PairingRefused,
    format_code,
    new_code,
    normalize_code,
)


class Clock:
    """Une horloge qu'on avance à la main : monotone et murale vont ensemble."""

    def __init__(self):
        self.now = 1_000.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
def pairing(clock):
    events: list[str] = []
    instance = Pairing(on_change=lambda: events.append("change"), clock=clock, wall=clock)
    instance.events = events  # type: ignore[attr-defined]
    return instance


def refusal(call) -> PairingRefused:
    with pytest.raises(PairingRefused) as info:
        call()
    return info.value


def open_with_code(pairing) -> str:
    pairing.start()
    return pairing.info()["code"]


# --- le code -----------------------------------------------------------------------------------


def test_a_code_is_eight_unambiguous_characters():
    for _ in range(200):
        code = new_code()
        assert len(code) == CODE_LENGTH
        assert set(code) <= set(ALPHABET)
    assert not set("01OIL") & set(ALPHABET)  # rien qui se confonde à l'écran ou au clavier
    assert len({new_code() for _ in range(200)}) > 190  # tirés au hasard, pas une suite


def test_a_code_is_shown_in_two_groups_of_four():
    assert format_code("K7QM2XPD") == "K7QM-2XPD"


@pytest.mark.parametrize(
    "typed",
    ["K7QM-2XPD", "k7qm-2xpd", " K7QM 2XPD ", "K7QM2XPD", "k7-qm-2x-pd"],
)
def test_a_typed_code_is_forgiving_about_case_spaces_and_hyphens(typed):
    assert normalize_code(typed) == "K7QM2XPD"


@pytest.mark.parametrize(
    "typed",
    ["", "K7QM-2XP", "K7QM-2XPDD", "K7QM-2XP0", "K7QM-2XPO", "K7QM-2XP1", "K7QM-2XPI", "é7QM-2XPD",
     "K7QM-2XPD" * 10, None, 12345678, ["K7QM2XPD"], b"K7QM2XPD"],
)  # fmt: skip
def test_a_malformed_code_is_not_a_code(typed):
    assert normalize_code(typed) is None


# --- la session ----------------------------------------------------------------------------------


def test_a_dock_without_session_shows_nothing_to_type(pairing):
    assert pairing.info() == {"open": False, "last": None}
    assert not pairing.is_open()


def test_opening_a_session_shows_a_code_that_lasts_ten_minutes(pairing, clock):
    pairing.start()
    info = pairing.info()
    assert info["open"] is True
    assert len(info["code"]) == CODE_LENGTH + 1  # « XXXX-XXXX »
    assert info["wrong_left"] == MAX_WRONG
    assert info["expires_at"] == int((clock.now + SESSION_SECONDS) * 1000)
    clock.advance(SESSION_SECONDS - 1)
    assert pairing.is_open()
    clock.advance(2)
    assert not pairing.is_open()
    assert pairing.info() == {
        "open": False,
        "last": {"result": "expired", "at": int(clock.now * 1000)},
    }


def test_the_screen_is_told_about_every_change_including_expiry(pairing, clock):
    pairing.start()
    assert pairing.events == ["change"]
    clock.advance(SESSION_SECONDS + 1)
    pairing.tick()  # le serveur l'appelle chaque seconde : l'écran apprend l'expiration
    assert pairing.events == ["change", "change"]
    pairing.tick()
    assert pairing.events == ["change", "change"]  # une seule annonce


def test_a_new_session_replaces_the_old_code_and_forgets_the_mistakes(pairing):
    first = open_with_code(pairing)
    for _ in range(2):
        assert refusal(lambda: pairing.check("192.0.2.5", "AAAA-AAAA")).code == "bad_code"
    pairing.start()
    second = pairing.info()
    assert second["wrong_left"] == MAX_WRONG
    assert second["code"] != first
    assert refusal(lambda: pairing.check("192.0.2.5", first)).code == "bad_code"


def test_closing_a_session_says_so(pairing, clock):
    open_with_code(pairing)
    pairing.stop("closed")
    assert not pairing.is_open()
    assert pairing.info()["last"] == {"result": "closed", "at": int(clock.now * 1000)}
    pairing.stop("closed")  # sans session : rien à dire de plus
    assert pairing.events.count("change") == 2


def test_finishing_records_who_was_paired(pairing, clock):
    open_with_code(pairing)
    pairing.finish("portable", "192.0.2.5")
    assert pairing.info() == {
        "open": False,
        "last": {
            "result": "paired",
            "name": "portable",
            "host": "192.0.2.5",
            "at": int(clock.now * 1000),
        },
    }


# --- le code présenté ------------------------------------------------------------------------------


def test_without_a_session_every_code_is_refused_and_says_what_to_do(pairing):
    error = refusal(lambda: pairing.check("192.0.2.5", "K7QM-2XPD"))
    assert (error.code, error.status) == ("not_open", 409)
    assert "Associer un PC" in error.message


def test_the_right_code_passes_however_it_was_typed(pairing):
    code = open_with_code(pairing)
    pairing.check("192.0.2.5", code.lower().replace("-", " "))  # ne lève rien


def test_a_wrong_code_is_refused_and_counted(pairing):
    open_with_code(pairing)
    error = refusal(lambda: pairing.check("192.0.2.5", "AAAA-AAAA"))
    assert (error.code, error.status, error.message) == ("bad_code", 403, "Code incorrect.")
    assert pairing.info()["wrong_left"] == MAX_WRONG - 1


def test_a_code_of_the_wrong_shape_counts_as_a_mistake_too(pairing):
    open_with_code(pairing)
    for typed in ("", "pas un code", None, 42, "K7QM"):
        assert refusal(lambda typed=typed: pairing.check("192.0.2.5", typed)).code in {
            "bad_code",
            "locked",
        }
    assert not pairing.is_open()  # cinq essais, cinq erreurs : annulé


def test_five_mistakes_cancel_the_association_even_for_the_right_code_afterwards(pairing):
    code = open_with_code(pairing)
    for attempt in range(MAX_WRONG - 1):
        assert refusal(lambda: pairing.check("192.0.2.5", "AAAA-AAAA")).code == "bad_code", attempt
    last = refusal(lambda: pairing.check("192.0.2.5", "AAAA-AAAA"))
    assert (last.code, last.status) == ("locked", 403)
    assert not pairing.is_open()
    assert pairing.info()["last"]["result"] == "locked"
    assert refusal(lambda: pairing.check("192.0.2.5", code)).code == "not_open"  # trop tard


def test_the_mistakes_of_one_address_count_for_everyone(pairing):
    code = open_with_code(pairing)
    for peer in ("192.0.2.1", "192.0.2.2", "192.0.2.3", "192.0.2.4"):
        refusal(lambda peer=peer: pairing.check(peer, "AAAA-AAAA"))
    pairing.check("192.0.2.9", code)  # le bon code passe encore : 4 erreurs sur 5
    refusal(lambda: pairing.check("192.0.2.5", "AAAA-AAAA"))  # la cinquième annule
    assert not pairing.is_open()


def test_a_right_code_after_expiry_is_refused(pairing, clock):
    code = open_with_code(pairing)
    clock.advance(SESSION_SECONDS + 1)
    assert refusal(lambda: pairing.check("192.0.2.5", code)).code == "not_open"


# --- une demande après l'autre, pas un déluge ---------------------------------------------------------


def test_one_address_cannot_flood_the_door_even_with_no_session(pairing):
    for _ in range(THROTTLE_MAX):
        assert refusal(lambda: pairing.check("192.0.2.5", "K7QM-2XPD")).code == "not_open"
    flood = refusal(lambda: pairing.check("192.0.2.5", "K7QM-2XPD"))
    assert (flood.code, flood.status) == ("too_many", 429)
    assert (
        refusal(lambda: pairing.check("192.0.2.6", "K7QM-2XPD")).code == "not_open"
    )  # une autre : libre


def test_the_door_reopens_a_minute_later(pairing, clock):
    for _ in range(THROTTLE_MAX):
        refusal(lambda: pairing.check("192.0.2.5", "K7QM-2XPD"))
    assert refusal(lambda: pairing.check("192.0.2.5", "K7QM-2XPD")).code == "too_many"
    clock.advance(THROTTLE_WINDOW + 1)
    assert refusal(lambda: pairing.check("192.0.2.5", "K7QM-2XPD")).code == "not_open"


def test_the_memory_of_addresses_does_not_grow_forever(pairing, clock):
    for index in range(MAX_TRACKED_PEERS + 50):
        refusal(
            lambda index=index: pairing.check(f"10.0.{index // 250}.{index % 250}", "K7QM-2XPD")
        )
        clock.advance(THROTTLE_WINDOW + 1)  # chaque demande est « ancienne » pour la suivante
    assert len(pairing._hits) <= MAX_TRACKED_PEERS + 1
