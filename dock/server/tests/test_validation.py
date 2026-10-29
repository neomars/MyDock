import pytest

from mydock_dock.validation import ValidationFailure, normalize_mac, validate_host, validate_port


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("ubuntu.local", "ubuntu.local"),
        ("  Mon-PC.Local. ", "mon-pc.local"),
        ("192.168.1.20", "192.168.1.20"),
        ("pc", "pc"),
        ("a" * 63 + ".local", "a" * 63 + ".local"),
        ("pc\n", "pc"),  # espaces et retours en bout de saisie sont retirés
        ("ubuntu.local.", "ubuntu.local"),  # point final d'un nom complet
    ],
)
def test_valid_hosts(raw, expected):
    assert validate_host(raw) == expected


@pytest.mark.parametrize(
    "raw",
    [
        "",
        "   ",
        "http://ubuntu.local",
        "ubuntu.local:8765",
        "ubuntu.local/ws",
        "fe80::1",
        "[::1]",
        "mon pc",
        "-pc.local",
        "pc-.local",
        "pc..local",
        "pc..",
        ".",
        "999.1.1.1",
        "1.2.3",
        "a" * 64 + ".local",
        "pc;rm -rf /",
        "pc\nevil",
        None,
        1234,
    ],
)
def test_invalid_hosts(raw):
    with pytest.raises(ValidationFailure):
        validate_host(raw)


@pytest.mark.parametrize("port", [1, 80, 8765, 65535])
def test_valid_ports(port):
    assert validate_port(port) == port


@pytest.mark.parametrize("port", [0, -1, 65536, "8765", 80.5, None, True])
def test_invalid_ports(port):
    with pytest.raises(ValidationFailure, match="entre 1 et 65535"):
        validate_port(port)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("02:00:5E:10:00:01", "02:00:5e:10:00:01"),
        ("02-00-5e-10-00-01", "02:00:5e:10:00:01"),
        ("02005e100001", "02:00:5e:10:00:01"),
        ("  02:00:5e:10:00:01 ", "02:00:5e:10:00:01"),
    ],
)
def test_valid_macs(raw, expected):
    assert normalize_mac(raw) == expected


@pytest.mark.parametrize(
    "raw",
    [
        "",
        "02:00:5e:10:00",
        "02:00:5e:10:00:01:02",
        "02:00:5e:10:00:0g",
        "02:00-5e:10:00:01",
        "00:00:00:00:00:00",
        "ff:ff:ff:ff:ff:ff",
        None,
        42,
    ],
)
def test_invalid_macs(raw):
    with pytest.raises(ValidationFailure):
        normalize_mac(raw)
