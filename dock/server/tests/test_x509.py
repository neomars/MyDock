"""Lecture des noms d'un certificat : de quoi dire quelle autorité un serveur présente."""

from __future__ import annotations

import ssl
import subprocess

import pytest

from mydock_dock import x509


def der(pem) -> bytes:
    return ssl.PEM_cert_to_DER_cert(pem.read_text())


def test_the_names_of_a_certificate_and_its_issuer(pki):
    assert x509.names(der(pki.leaf_pem)) == ("sylvan.test", "Apple Test Root (Test Org)")
    assert x509.names(der(pki.ca_pem)) == (
        "Apple Test Root (Test Org)",
        "Apple Test Root (Test Org)",
    )  # auto-signé


def test_a_chain_reads_from_the_server_up_to_the_authority(pki):
    leaf, ca = der(pki.leaf_pem), der(pki.ca_pem)
    # le serveur n'envoie que le sien : l'autorité qui l'a signé n'est pas dans la chaîne, on la nomme quand même
    assert x509.chain_text([leaf]) == "sylvan.test ← Apple Test Root (Test Org)"
    assert (
        x509.chain_text([leaf, ca]) == "sylvan.test ← Apple Test Root (Test Org)"
    )  # racine envoyée : rien à ajouter
    assert x509.chain_text([]) == ""


def test_every_authority_of_the_system_is_readable():
    roots = ssl.create_default_context().get_ca_certs(binary_form=True)
    assert roots, "le Pi de développement doit avoir des autorités de confiance"
    for root in roots:
        subject, issuer = x509.names(root)
        assert (subject, issuer) != ("?", "?")


@pytest.mark.parametrize(
    "junk",
    [b"", b"abc", b"\x30", b"\x30\x82\xff\xff", b"\x30\x03\x02\x01\x01", b"\x02\x01\x00" * 4],
)
def test_garbage_is_refused_without_a_crash(junk):
    with pytest.raises(ValueError, match=r"certificat|DER|longueur"):
        x509.names(junk)


def test_a_truncated_certificate_is_refused(pki):
    whole = der(pki.leaf_pem)
    for cut in (10, 60, len(whole) // 2, len(whole) - 5):
        with pytest.raises(ValueError, match=r"certificat|DER|longueur"):
            x509.names(whole[:cut])


def test_a_chain_with_junk_in_it_keeps_what_it_can_read(pki):
    assert (
        x509.chain_text([b"pas un certificat", der(pki.leaf_pem)])
        == "sylvan.test ← Apple Test Root (Test Org)"
    )


def test_the_names_agree_with_openssl(pki):
    out = subprocess.run(
        ["openssl", "x509", "-in", str(pki.leaf_pem), "-noout", "-subject", "-issuer", "-nameopt", "RFC2253"],
        capture_output=True, text=True, check=True,
    ).stdout  # fmt: skip
    assert "CN=sylvan.test" in out
    assert "CN=Apple Test Root" in out
