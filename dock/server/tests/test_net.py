"""Les connexions sortantes : IPv4 et IPv6 en alternance, quelques secondes par adresse, au plus quatre essais."""

from __future__ import annotations

import socket
import threading

import pytest

from mydock_dock import net


def info(family, address, port=443):
    sockaddr = (address, port) if family == socket.AF_INET else (address, port, 0, 0)
    return (family, socket.SOCK_STREAM, 6, "", sockaddr)


V4 = [info(socket.AF_INET, f"192.0.2.{i}") for i in range(1, 4)]
V6 = [info(socket.AF_INET6, f"2001:db8::{i}") for i in range(1, 4)]


def test_addresses_alternate_starting_with_ipv4():
    ordered = net.ordered_addresses([*V6, *V4])
    assert [i[0] for i in ordered] == [socket.AF_INET, socket.AF_INET6] * 3
    assert [i[4][0] for i in ordered][:2] == ["192.0.2.1", "2001:db8::1"]


def test_a_single_family_is_kept_whole():
    assert net.ordered_addresses(V4) == V4
    assert net.ordered_addresses(V6) == V6
    assert net.ordered_addresses([]) == []


class FakeSocket:
    """Un faux connecteur : les adresses de `dead` n'répondent jamais, les autres répondent."""

    dead: set[str] = set()
    log: list[tuple[str, float | None]] = []

    def __init__(self, family, kind, proto):
        self.timeout = None
        self.target = ""

    def settimeout(self, value):
        self.timeout = value

    def connect(self, sockaddr):
        self.target = sockaddr[0]
        FakeSocket.log.append((self.target, self.timeout))
        if self.target in FakeSocket.dead:
            raise TimeoutError("timed out")

    def close(self):
        pass


@pytest.fixture
def fake(monkeypatch):
    FakeSocket.dead = set()
    FakeSocket.log = []
    monkeypatch.setattr(net.socket, "socket", FakeSocket)
    return FakeSocket


def resolver(monkeypatch, infos):
    monkeypatch.setattr(net.socket, "getaddrinfo", lambda *a, **k: list(infos))


def test_a_blackholed_ipv6_does_not_make_the_ipv4_wait(monkeypatch, fake):
    resolver(monkeypatch, [*V6, *V4])
    fake.dead = {i[4][0] for i in V6}
    sock = net.connect_fast(("exemple.org", 443), timeout=10.0)
    assert sock.target == "192.0.2.1"  # l'IPv4 passe en premier
    assert fake.log[0][0] == "192.0.2.1"
    assert all(
        t <= net.ATTEMPT_TIMEOUT for _, t in fake.log
    )  # jamais le délai entier de 10 s par adresse


def test_a_dead_ipv4_falls_back_to_ipv6_within_the_attempts(monkeypatch, fake):
    resolver(monkeypatch, [*V4, *V6])
    fake.dead = {i[4][0] for i in V4}
    sock = net.connect_fast(("exemple.org", 443), timeout=10.0)
    assert sock.target == "2001:db8::1"
    assert [a for a, _ in fake.log] == ["192.0.2.1", "2001:db8::1"]


def test_attempts_are_capped_and_the_last_error_is_raised(monkeypatch, fake):
    resolver(monkeypatch, [*V4, *V6])
    fake.dead = {i[4][0] for i in [*V4, *V6]}
    with pytest.raises(TimeoutError):
        net.connect_fast(("exemple.org", 443), timeout=10.0)
    assert len(fake.log) == net.MAX_ATTEMPTS


def test_no_address_is_an_error(monkeypatch, fake):
    resolver(monkeypatch, [])
    with pytest.raises(OSError, match="Aucune adresse"):
        net.connect_fast(("exemple.org", 443))


def test_the_read_timeout_is_restored_after_connecting(monkeypatch, fake):
    resolver(monkeypatch, V4)
    assert net.connect_fast(("exemple.org", 443), timeout=10.0).timeout == 10.0
    assert net.connect_fast(("exemple.org", 443), timeout=None).timeout is None


def test_it_really_connects_to_a_local_server():
    server = socket.socket()
    server.bind(("127.0.0.1", 0))
    server.listen(1)
    accepted = []
    thread = threading.Thread(target=lambda: accepted.append(server.accept()[0]), daemon=True)
    thread.start()
    sock = net.connect_fast(("127.0.0.1", server.getsockname()[1]), timeout=5.0)
    thread.join(2)
    assert accepted
    sock.close()
    accepted[0].close()
    server.close()


def test_a_refused_connection_raises_quickly():
    free = socket.socket()
    free.bind(("127.0.0.1", 0))
    port = free.getsockname()[1]
    free.close()
    with pytest.raises(ConnectionRefusedError):
        net.connect_fast(("127.0.0.1", port), timeout=5.0)


def test_the_opener_uses_the_fast_connection():
    opener = net.build_opener()
    handlers = [h for h in opener.handlers if isinstance(h, net.FastHTTPSHandler)]
    assert len(handlers) == 1
    connection = net.FastHTTPSConnection("exemple.org")
    assert connection._create_connection is net.connect_fast


# --- les certificats : ce que le serveur présente, et ce que le Pi en pense --------------------------------------------


def test_the_certificate_a_server_presents_can_be_read_without_trusting_it(pki, tls_server):
    from mydock_dock import x509

    with tls_server(pki.leaf_pem, pki.leaf_key) as port:
        chain = net.presented_chain("127.0.0.1", port)
        assert len(chain) == 1
        assert x509.names(chain[0]) == ("sylvan.test", "Apple Test Root (Test Org)")
        assert (
            net.describe_presented("127.0.0.1", port) == "sylvan.test ← Apple Test Root (Test Org)"
        )


def test_reading_a_certificate_from_nothing_gives_nothing():
    free = socket.socket()
    free.bind(("127.0.0.1", 0))
    port = free.getsockname()[1]
    free.close()
    assert net.presented_chain("127.0.0.1", port, timeout=1.0) == []
    assert net.describe_presented("127.0.0.1", port, timeout=1.0) == ""


def test_an_unknown_authority_is_refused_and_named(pki, tls_server):
    with tls_server(pki.leaf_pem, pki.leaf_key) as port:
        ok, text = net.check_tls("localhost", port)
    assert ok is False
    assert "certificat refusé (unable to get local issuer certificate)" in text
    assert "présenté : sylvan.test ← Apple Test Root (Test Org)" in text


def test_a_known_authority_is_accepted(pki, monkeypatch, tls_server):
    monkeypatch.setenv("SSL_CERT_FILE", str(pki.ca_pem))
    with tls_server(pki.leaf_pem, pki.leaf_key) as port:
        assert net.check_tls("localhost", port) == (True, "certificat vérifié")


def test_a_certificate_for_another_name_is_refused_even_when_the_authority_is_known(
    pki, monkeypatch, tls_server
):
    monkeypatch.setenv("SSL_CERT_FILE", str(pki.ca_pem))
    with tls_server(pki.wrong_pem, pki.wrong_key) as port:
        ok, text = net.check_tls("localhost", port)
    assert ok is False
    assert "certificat refusé" in text
    assert "autre.test" in text


def test_a_server_that_does_not_speak_tls_is_a_connection_problem():
    plain = socket.socket()
    plain.bind(("127.0.0.1", 0))
    plain.listen(1)
    threading.Thread(target=lambda: plain.accept()[0].close(), daemon=True).start()
    ok, text = net.check_tls("127.0.0.1", plain.getsockname()[1], timeout=2.0)
    plain.close()
    assert ok is False
    assert "certificat" not in text.replace("certificat vérifié", "") or "refusé" not in text
