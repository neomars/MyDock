"""L'autorité racine d'Apple : obtenue sur apple.com par une connexion vérifiée, contrôlée, gardée, et réservée à ses serveurs."""

from __future__ import annotations

import shutil
import socket
import ssl
import subprocess
import urllib.error
from types import SimpleNamespace

import pytest

from mydock_dock import aerial, apple_ca, net
from mydock_dock.aerial import AerialError, AerialFetcher, target_name

needs_ffmpeg = pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg absent")


def der(pem_path):
    return ssl.PEM_cert_to_DER_cert(pem_path.read_text())


# --- le contrôle de ce qu'on a reçu ------------------------------------------------------------------------------------


def test_the_root_is_read_from_der_or_pem(apple_pki):
    assert apple_ca.parse_root(apple_pki.root_der) == apple_pki.root_der
    assert apple_ca.parse_root(apple_pki.root_pem.read_bytes()) == apple_pki.root_der


def test_only_a_self_signed_certificate_named_apple_root_ca_is_accepted(apple_pki, pki):
    with pytest.raises(apple_ca.AppleRootError, match="pas une autorité racine"):
        apple_ca.parse_root(der(apple_pki.inter_pem))  # un intermédiaire : signé par la racine
    with pytest.raises(apple_ca.AppleRootError, match="pas une autorité racine"):
        apple_ca.parse_root(der(pki.leaf_pem))
    with pytest.raises(apple_ca.AppleRootError, match="s'appelle « Apple Test Root"):
        apple_ca.parse_root(der(pki.ca_pem))  # une racine, mais pas celle d'Apple


@pytest.mark.parametrize(
    "junk",
    [
        b"",
        b"<html>404</html>",
        b"\x30\x03\x02\x01\x01",
        b"-----BEGIN CERTIFICATE-----\nAAAA\n-----END CERTIFICATE-----\n",
        b"\xff" * 50,
    ],
)
def test_anything_else_is_refused(junk):
    with pytest.raises(apple_ca.AppleRootError, match="pas un certificat"):
        apple_ca.parse_root(junk)


def test_the_fingerprint_is_short_and_comparable():
    sample = b"abc"
    assert (
        apple_ca.fingerprint(sample)
        == "BA7816BF8F01CFEA414140DE5DAE2223B00361A396177A9CB410FF61F20015AD"
    )
    assert apple_ca.short_fingerprint(sample) == "BA7816BF…F20015AD"


# --- le cache ---------------------------------------------------------------------------------------------------------------


def test_a_kept_root_is_read_back_and_rechecked(tmp_path, apple_pki):
    assert apple_ca.load_cached(tmp_path / "trust") is None
    apple_ca.save_cached(tmp_path / "trust", apple_pki.root_der)
    assert apple_ca.load_cached(tmp_path / "trust") == apple_pki.root_der
    (tmp_path / "trust" / apple_ca.CACHE_NAME).write_text(
        "abîmé"
    )  # un fichier modifié n'est plus cru
    assert apple_ca.load_cached(tmp_path / "trust") is None


def test_the_cache_never_keeps_a_half_written_file(tmp_path, apple_pki):
    apple_ca.save_cached(tmp_path / "trust", apple_pki.root_der)
    assert [p.name for p in (tmp_path / "trust").iterdir()] == [apple_ca.CACHE_NAME]


# --- le téléchargement : connexion vérifiée, https, même serveur -------------------------------------------------------


@pytest.fixture
def web(pki, tls_server, monkeypatch):
    """Un faux www.apple.com : un certificat que le Pi reconnaît (SSL_CERT_FILE), et ce qu'on lui fait servir."""
    monkeypatch.setenv("SSL_CERT_FILE", str(pki.ca_pem))
    routes: dict = {}
    with tls_server(pki.leaf_pem, pki.leaf_key, routes) as port:
        yield SimpleNamespace(
            port=port, routes=routes, url=lambda path: f"https://localhost:{port}{path}"
        )


def test_the_root_is_downloaded_and_checked(web, apple_pki):
    web.routes["/appleca/Root.cer"] = (
        200,
        {"Content-Type": "application/x-x509-ca-cert"},
        apple_pki.root_der,
    )
    assert apple_ca.fetch_root((web.url("/appleca/Root.cer"),)) == apple_pki.root_der


def test_the_next_address_is_tried_when_the_first_fails(web, apple_pki):
    web.routes["/ok.cer"] = (200, {}, apple_pki.root_der)
    assert apple_ca.fetch_root((web.url("/absent.cer"), web.url("/ok.cer"))) == apple_pki.root_der


def test_every_failure_is_listed(web):
    web.routes["/page.cer"] = (200, {"Content-Type": "text/html"}, b"<html>")
    with pytest.raises(apple_ca.AppleRootError) as caught:
        apple_ca.fetch_root((web.url("/absent.cer"), web.url("/page.cer")))
    message = str(caught.value)
    assert "Impossible d'obtenir l'autorité racine d'Apple" in message
    assert "404" in message
    assert "pas un certificat" in message


def test_a_redirect_to_the_same_server_is_followed_and_any_other_is_not(web, apple_pki):
    web.routes["/ancien.cer"] = (301, {"Location": web.url("/nouveau.cer")}, b"")
    web.routes["/nouveau.cer"] = (200, {}, apple_pki.root_der)
    assert apple_ca.fetch_root((web.url("/ancien.cer"),)) == apple_pki.root_der
    web.routes["/ailleurs.cer"] = (
        302,
        {"Location": f"https://127.0.0.1:{web.port}/nouveau.cer"},
        b"",
    )  # autre nom de serveur
    with pytest.raises(apple_ca.AppleRootError):
        apple_ca.fetch_root((web.url("/ailleurs.cer"),))
    web.routes["/clair.cer"] = (
        302,
        {"Location": "http://localhost:1/nouveau.cer"},
        b"",
    )  # jamais en clair
    with pytest.raises(apple_ca.AppleRootError):
        apple_ca.fetch_root((web.url("/clair.cer"),))


def test_an_oversized_file_is_refused(web):
    web.routes["/gros.cer"] = (200, {}, b"\x30" * (apple_ca.MAX_BYTES + 10))
    with pytest.raises(apple_ca.AppleRootError, match="trop gros"):
        apple_ca.fetch_root((web.url("/gros.cer"),))


def test_a_site_whose_certificate_is_not_recognised_gives_no_root(
    pki, apple_pki, tls_server, monkeypatch
):
    monkeypatch.setenv("SSL_CERT_FILE", str(pki.ca_pem))  # le Pi ne connaît que cette autorité
    routes = {"/Root.cer": (200, {}, apple_pki.root_der)}
    with (
        tls_server(
            apple_pki.chain_pem, apple_pki.leaf_key, routes
        ) as port,  # un faux site, d'une autre autorité
        pytest.raises(apple_ca.AppleRootError, match=r"certificate verify failed|local issuer"),
    ):
        apple_ca.fetch_root((f"https://localhost:{port}/Root.cer",))


def test_obtain_uses_the_cache_before_the_network(web, apple_pki, tmp_path):
    web.routes["/Root.cer"] = (200, {}, apple_pki.root_der)
    urls = (web.url("/Root.cer"),)
    assert apple_ca.obtain_root(tmp_path / "trust", urls) == (apple_pki.root_der, "apple.com")
    web.routes.clear()  # le site ne répond plus : le cache suffit
    assert apple_ca.obtain_root(tmp_path / "trust", urls) == (apple_pki.root_der, "cache")


# --- le contexte TLS : une racine de plus, rien de désactivé ---------------------------------------------------------


def test_the_context_adds_a_root_and_disables_nothing(apple_pki):
    context = net.context_with_roots(apple_pki.root_pem.read_text())
    assert context.verify_mode == ssl.CERT_REQUIRED
    assert context.check_hostname is True
    assert (
        not context.verify_flags & ssl.VERIFY_X509_STRICT
    )  # seule la pédanterie de la RFC 5280 est levée
    assert (
        context.cert_store_stats()["x509_ca"]
        > ssl.create_default_context().cert_store_stats()["x509_ca"]
    )


def test_the_chain_of_the_fake_apple_is_unknown_to_the_pi_until_the_root_is_given(
    apple_pki, pki, tls_server, monkeypatch
):
    """Le cas réel : le serveur envoie son certificat et l'intermédiaire, mais pas la racine."""
    monkeypatch.setenv("SSL_CERT_FILE", str(pki.ca_pem))
    with tls_server(apple_pki.chain_pem, apple_pki.leaf_key) as port:
        with (
            pytest.raises(ssl.SSLCertVerificationError) as refused,
            socket.create_connection(("localhost", port)) as raw,
            ssl.create_default_context().wrap_socket(raw, server_hostname="localhost"),
        ):
            pass
        assert (
            refused.value.verify_code == 20
        )  # « unable to get local issuer certificate » : celui de la photo
        with (
            socket.create_connection(("localhost", port)) as raw,
            net.context_with_roots(apple_pki.root_pem.read_text()).wrap_socket(
                raw, server_hostname="localhost"
            ) as tls,
        ):
            assert tls.version()


def _openssl(*args: str) -> None:
    subprocess.run(["openssl", *args], check=True, capture_output=True)


@pytest.fixture(scope="module")
def edge_pki(tmp_path_factory):
    """Deux cas limites : une chaîne à l'ancienne, sans identifiants de clé (le mode strict de Python la refuse), et
    un certificat périmé depuis hier."""
    if not shutil.which("openssl"):
        pytest.skip("openssl absent")
    root = tmp_path_factory.mktemp("edge_pki")
    ca_key, ca_pem = root / "ca.key", root / "ca.pem"
    _openssl("req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "2", "-subj", "/CN=Apple Root CA/O=Apple Inc.",
             "-addext", "basicConstraints=critical,CA:TRUE", "-addext", "keyUsage=critical,keyCertSign,cRLSign",
             "-keyout", str(ca_key), "-out", str(ca_pem))  # fmt: skip

    def sign(name, subject, signer, extensions, days="2"):
        key, csr, pem, ext = (root / f"{name}.{kind}" for kind in ("key", "csr", "pem", "cnf"))
        ext.write_text(extensions)
        _openssl(
            "req",
            "-newkey",
            "rsa:2048",
            "-nodes",
            "-subj",
            subject,
            "-keyout",
            str(key),
            "-out",
            str(csr),
        )
        _openssl("x509", "-req", "-in", str(csr), "-CA", str(signer[0]), "-CAkey", str(signer[1]), "-CAcreateserial",
                 "-days", days, "-extfile", str(ext), "-out", str(pem))  # fmt: skip
        return pem, key

    no_ids = "authorityKeyIdentifier=none\nsubjectKeyIdentifier=none\n"
    server = (
        "subjectAltName=DNS:localhost\nbasicConstraints=CA:FALSE\nextendedKeyUsage=serverAuth\n"
    )
    inter_pem, inter_key = sign(
        "inter", "/CN=Apple Server Authentication CA/O=Apple Inc.", (ca_pem, ca_key),
        "basicConstraints=critical,CA:TRUE,pathlen:0\nkeyUsage=critical,keyCertSign,cRLSign\n" + no_ids,
    )  # fmt: skip
    leaf_pem, leaf_key = sign(
        "leaf", "/CN=sylvan.apple.com/O=Apple Inc.", (inter_pem, inter_key), server + no_ids
    )
    chain = root / "chain.pem"
    chain.write_text(leaf_pem.read_text() + inter_pem.read_text())
    expired_pem, expired_key = sign(
        "old", "/CN=sylvan.apple.com/O=Apple Inc.", (ca_pem, ca_key), server, days="-1"
    )
    return SimpleNamespace(
        root_pem=ca_pem,
        chain_pem=chain,
        leaf_key=leaf_key,
        expired_pem=expired_pem,
        expired_key=expired_key,
    )


def handshake(context, port, host="localhost"):
    with (
        socket.create_connection(("localhost", port)) as raw,
        context.wrap_socket(raw, server_hostname=host) as tls,
    ):
        return tls.version()


@pytest.mark.skipif(
    not ssl.create_default_context().verify_flags & ssl.VERIFY_X509_STRICT,
    reason="Python avant 3.13 : le mode strict n'est pas actif par défaut",
)
def test_an_old_style_chain_is_refused_by_strict_mode_and_accepted_with_the_added_root(
    edge_pki, tls_server
):
    """L'autorité d'Apple date de 2006 : si sa chaîne ne respecte pas toute la RFC 5280, seul le mode strict la refuse."""
    pem = edge_pki.root_pem.read_text()
    with tls_server(edge_pki.chain_pem, edge_pki.leaf_key) as port:
        strict = ssl.create_default_context()
        strict.load_verify_locations(cadata=pem)
        with pytest.raises(ssl.SSLCertVerificationError) as refused:
            handshake(strict, port)
        assert refused.value.verify_code in (
            85,
            86,
        )  # « Missing Authority / Subject Key Identifier »
        assert handshake(net.context_with_roots(pem), port)


def test_the_added_root_still_checks_the_dates(edge_pki, tls_server):
    with (
        tls_server(edge_pki.expired_pem, edge_pki.expired_key) as port,
        pytest.raises(ssl.SSLCertVerificationError) as refused,
    ):
        handshake(net.context_with_roots(edge_pki.root_pem.read_text()), port)
    assert refused.value.verify_code == 10  # « certificate has expired »


def test_the_added_root_still_checks_the_server_name(apple_pki, tls_server):
    with (
        tls_server(apple_pki.chain_pem, apple_pki.leaf_key) as port,
        pytest.raises(ssl.SSLCertVerificationError) as refused,
    ):
        handshake(net.context_with_roots(apple_pki.root_pem.read_text()), port, host="autre.test")
    assert refused.value.verify_code == 62  # « Hostname mismatch »


def test_the_root_given_for_one_server_is_not_used_for_any_other(
    apple_pki, pki, tls_server, monkeypatch
):
    """Même chaîne, même port, deux noms : seul le nom auquel on a donné la racine est vérifié avec elle."""
    monkeypatch.setenv("SSL_CERT_FILE", str(pki.ca_pem))
    opener = net.build_opener(
        host_contexts={"localhost": net.context_with_roots(apple_pki.root_pem.read_text())}
    )
    with tls_server(apple_pki.chain_pem, apple_pki.leaf_key, {"/x": (200, {}, b"ok")}) as port:
        with opener.open(f"https://localhost:{port}/x", timeout=5) as reply:
            assert reply.read() == b"ok"
        with pytest.raises(urllib.error.URLError) as refused:
            opener.open(f"https://127.0.0.1:{port}/x", timeout=5)
    assert isinstance(refused.value.reason, ssl.SSLCertVerificationError)
    assert refused.value.reason.verify_code == 20


# --- le téléchargeur, de bout en bout ----------------------------------------------------------------------------------------


@pytest.fixture
def world(tmp_path, pki, apple_pki, tls_server, monkeypatch, clips):
    """Un faux sylvan.apple.com (chaîne privée, racine non envoyée) et un faux www.apple.com (certificat reconnu)."""
    if not clips:
        pytest.skip("ffmpeg absent")
    monkeypatch.setenv("SSL_CERT_FILE", str(pki.ca_pem))
    films = {
        f"/Aerials/{n}.mov": (200, {"Content-Type": "video/quicktime"}, clips["small.mp4"])
        for n in ("a", "b")
    }
    site = {
        "/appleca/Root.cer": (
            200,
            {"Content-Type": "application/x-x509-ca-cert"},
            apple_pki.root_der,
        )
    }
    with (
        tls_server(apple_pki.chain_pem, apple_pki.leaf_key, films) as film_port,
        tls_server(pki.leaf_pem, pki.leaf_key, site) as site_port,
    ):
        monkeypatch.setattr(aerial, "BASE_URL", f"https://localhost:{film_port}/Aerials")
        monkeypatch.setattr(aerial, "APPLE_HOSTS", frozenset({"localhost"}))
        monkeypatch.setattr(aerial, "CATALOG", (("a.mov", "Vue test 1"), ("b.mov", "Vue test 2")))
        monkeypatch.setattr(
            apple_ca, "ROOT_URLS", (f"https://localhost:{site_port}/appleca/Root.cer",)
        )
        yield SimpleNamespace(
            site=site,
            film_port=film_port,
            site_port=site_port,
            trust=tmp_path / "trust",
            saver=tmp_path / "saver",
            films=films,
        )


def fetcher(world, *, apple=True, sources=()):
    return AerialFetcher(
        world.saver, lambda: True, apple=lambda: apple, sources=lambda: list(sources),
        trust_dir=world.trust, which=shutil.which, start_delay=0, retry_delay=0.01,
    )  # fmt: skip


@needs_ffmpeg
async def test_the_films_download_once_the_missing_root_is_added(world, apple_pki):
    f = fetcher(world)
    await f.fetch_missing()
    assert {p.name for p in world.saver.glob("*.mp4")} == {
        target_name("Vue test 1"),
        target_name("Vue test 2"),
    }
    status = f.status()
    assert status["done"] == 2
    info = status["apple_root"]
    assert info["origin"] == "apple.com"
    assert info["sha256"] == apple_ca.short_fingerprint(apple_pki.root_der)
    assert (
        info["known"] == "no"
    )  # ce n'est pas la vraie racine d'Apple : son empreinte n'est pas celle qu'on connaît
    assert (world.trust / apple_ca.CACHE_NAME).is_file()


@needs_ffmpeg
async def test_a_second_start_reuses_the_kept_root_without_asking_apple_again(
    world, apple_pki, monkeypatch
):
    await fetcher(world).fetch_missing()
    for film in world.saver.glob("*.mp4"):
        film.unlink()
    monkeypatch.setattr(apple_ca, "ROOT_URLS", ("https://localhost:1/inaccessible.cer",))
    again = fetcher(world)
    await again.fetch_missing()
    assert again.status()["done"] == 2
    assert again.status()["apple_root"]["origin"] == "cache"


async def test_the_apple_site_unreachable_is_said_on_the_apple_line(world, monkeypatch):
    monkeypatch.setattr(apple_ca, "ROOT_URLS", ("https://localhost:1/inaccessible.cer",))
    f = fetcher(world)
    with pytest.raises(AerialError, match="2 film"):
        await f.fetch_missing()
    row = f.status()["sources"][0]
    assert "Impossible d'obtenir l'autorité racine d'Apple" in (row["error"] or "")
    assert row["done"] == 0
    assert f.status()["apple_root"] is None


async def test_a_wrong_root_is_not_trusted(world, apple_pki):
    world.site["/appleca/Root.cer"] = (
        200,
        {},
        der(apple_pki.inter_pem),
    )  # un intermédiaire à la place de la racine
    f = fetcher(world)
    with pytest.raises(AerialError, match="2 film"):
        await f.fetch_missing()
    assert "pas une autorité racine" in (f.status()["sources"][0]["error"] or "")
    assert not list(world.saver.glob("*.mp4"))
    assert not (world.trust / apple_ca.CACHE_NAME).exists()


async def test_a_page_instead_of_the_root_is_not_trusted(world):
    world.site["/appleca/Root.cer"] = (
        200,
        {"Content-Type": "text/html"},
        b"<html>Maintenance</html>",
    )
    f = fetcher(world)
    with pytest.raises(AerialError, match="2 film"):
        await f.fetch_missing()
    assert "pas un certificat" in (f.status()["sources"][0]["error"] or "")


@needs_ffmpeg
async def test_the_root_of_apple_is_never_given_to_the_addresses_you_type(world):
    url = f"https://localhost:{world.film_port}/Aerials/a.mov"  # le même serveur et la même chaîne, mais saisis à la main
    f = fetcher(world, apple=False, sources=[url])
    with pytest.raises(AerialError, match="1 film"):
        await f.fetch_missing()
    assert "refusé par le Pi" in (f.status()["sources"][0]["error"] or "")
    assert f.status()["apple_root"] is None
    assert not list(world.saver.glob("*.mp4"))


async def test_the_root_is_not_looked_for_when_the_refusal_is_about_something_else(
    world, pki, tls_server, apple_pki, monkeypatch
):
    """Un certificat au mauvais nom (même d'une autorité connue) ne déclenche jamais la recherche de la racine d'Apple."""
    asked = []
    original = apple_ca.obtain_root
    monkeypatch.setattr(
        apple_ca, "obtain_root", lambda *a, **k: (asked.append(1), original(*a, **k))[1]
    )
    films = {"/Aerials/a.mov": (200, {}, b"x")}
    with tls_server(
        pki.wrong_pem, pki.wrong_key, films
    ) as port:  # signé par l'autorité connue, mais pour « autre.test »
        monkeypatch.setattr(aerial, "BASE_URL", f"https://localhost:{port}/Aerials")
        f = fetcher(world)
        with pytest.raises(AerialError, match="2 film"):
            await f.fetch_missing()
    assert asked == []
    assert "refusé par le Pi" in (f.status()["sources"][0]["error"] or "")


async def test_another_server_than_apples_never_gets_its_root(world, monkeypatch):
    monkeypatch.setattr(aerial, "APPLE_HOSTS", frozenset({"un-autre-serveur.test"}))
    f = fetcher(world)
    with pytest.raises(AerialError, match="2 film"):
        await f.fetch_missing()
    assert f.status()["apple_root"] is None
    assert "refusé par le Pi" in (f.status()["sources"][0]["error"] or "")


@needs_ffmpeg
async def test_a_redirect_to_another_server_is_checked_without_the_apple_root(world):
    """Le serveur d'Apple renvoie vers un autre nom : celui-là n'a droit qu'aux autorités du Pi, et le message le nomme."""
    world.films["/Aerials/a.mov"] = (
        302,
        {"Location": f"https://127.0.0.1:{world.film_port}/Aerials/b.mov"},
        b"",
    )
    f = fetcher(world)
    with pytest.raises(AerialError, match="1 film"):
        await f.fetch_missing()
    error = f.status()["sources"][0]["error"] or ""
    assert (
        "Certificat de 127.0.0.1 refusé par le Pi" in error
    )  # le serveur réellement joint, pas celui d'où l'on vient
    assert "a pourtant été ajoutée" not in error
    assert f.status()["done"] == 1  # l'autre film vient bien du serveur d'Apple


@needs_ffmpeg
async def test_the_root_is_not_fetched_when_the_pi_already_trusts_the_chain(
    world, pki, apple_pki, tmp_path, monkeypatch
):
    bundle = tmp_path / "bundle.pem"
    bundle.write_text(pki.ca_pem.read_text() + apple_pki.root_pem.read_text())
    monkeypatch.setenv("SSL_CERT_FILE", str(bundle))
    monkeypatch.setattr(
        apple_ca, "ROOT_URLS", ("https://localhost:1/inaccessible.cer",)
    )  # y aller échouerait
    f = fetcher(world)
    await f.fetch_missing()
    assert f.status()["done"] == 2
    assert f.status()["apple_root"] is None
    assert not (world.trust / apple_ca.CACHE_NAME).exists()


@needs_ffmpeg
async def test_a_root_that_did_not_sign_the_chain_is_said_to_be_added_all_the_same(world, edge_pki):
    """Une autre « Apple Root CA » (même nom, autre clé) : acceptée par le contrôle, mais elle ne signe pas cette chaîne."""
    world.site["/appleca/Root.cer"] = (200, {}, der(edge_pki.root_pem))
    f = fetcher(world)
    # Le premier film passe par la recherche de la racine, le second la trouve déjà : les deux disent la même chose.
    with pytest.raises(AerialError, match=r"Vue test 2 : .*a pourtant été ajoutée"):
        await f.fetch_missing()
    assert "a pourtant été ajoutée" in (f.status()["sources"][0]["error"] or "")
    assert not list(world.saver.glob("*.mp4"))


@needs_ffmpeg
async def test_an_expired_certificate_is_not_blamed_on_the_missing_root(
    edge_pki, pki, tmp_path, tls_server, monkeypatch
):
    monkeypatch.setenv("SSL_CERT_FILE", str(pki.ca_pem))
    trust = tmp_path / "trust"
    apple_ca.save_cached(
        trust, der(edge_pki.root_pem)
    )  # la racine est déjà gardée : le dock la donne tout de suite
    films = {"/Aerials/a.mov": (200, {}, b"x")}
    with tls_server(edge_pki.expired_pem, edge_pki.expired_key, films) as port:
        monkeypatch.setattr(aerial, "BASE_URL", f"https://localhost:{port}/Aerials")
        monkeypatch.setattr(aerial, "APPLE_HOSTS", frozenset({"localhost"}))
        monkeypatch.setattr(aerial, "CATALOG", (("a.mov", "Vue test 1"),))
        f = AerialFetcher(
            tmp_path / "saver", lambda: True, apple=lambda: True, sources=lambda: [],
            trust_dir=trust, start_delay=0, retry_delay=0.01,
        )  # fmt: skip
        with pytest.raises(AerialError, match=r"certificate has expired") as caught:
            await f.fetch_missing()
    assert "a pourtant été ajoutée" not in str(caught.value)


def test_the_real_apple_names_are_what_the_screen_showed():
    """Le nom attendu est celui que l'écran du Pi a affiché pour la chaîne d'Apple."""
    assert apple_ca.EXPECTED_NAME == "Apple Root CA (Apple Inc.)"
    assert apple_ca.ROOT_URLS[0].startswith("https://www.apple.com/")
    assert all(u.startswith("https://") for u in apple_ca.ROOT_URLS)
