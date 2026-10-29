import pytest

from mydock_common import auth


def test_generated_tokens_are_long_unique_and_valid():
    tokens = {auth.generate_token() for _ in range(50)}
    assert len(tokens) == 50
    for token in tokens:
        assert len(token) >= 43
        assert auth.is_valid_token_format(token)


@pytest.mark.parametrize(
    "token",
    [
        "",
        "court",
        "a" * 23,
        "a" * 257,
        "espace " + "a" * 30,
        "accentué" + "a" * 30,
        "a" * 30 + "\n",
        None,
        12345,
    ],
)
def test_invalid_token_formats(token):
    assert not auth.is_valid_token_format(token)


def test_tokens_equal():
    token = auth.generate_token()
    assert auth.tokens_equal(token, token)
    assert not auth.tokens_equal(token, token + "x")
    assert not auth.tokens_equal(token, None)
    assert not auth.tokens_equal(token, "")
    assert not auth.tokens_equal("", "")  # un jeton vide ne doit jamais authentifier


def test_bearer_roundtrip():
    token = auth.generate_token()
    assert auth.parse_bearer(auth.bearer_headers(token)["Authorization"]) == token


@pytest.mark.parametrize(
    "value", [None, "", "Bearer", "Bearer ", "Basic abc", "abc", "bearerx abc"]
)
def test_parse_bearer_rejects(value):
    assert auth.parse_bearer(value) is None


def test_parse_bearer_is_case_insensitive_on_scheme():
    assert auth.parse_bearer("bearer abc") == "abc"
