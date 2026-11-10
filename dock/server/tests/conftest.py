"""Des certificats de test : une autorité que le Pi ne connaît pas, un serveur qu'elle a signé, et un serveur au mauvais nom."""

from __future__ import annotations

import contextlib
import http.server
import shutil
import ssl
import subprocess
import threading
from dataclasses import dataclass
from pathlib import Path

import pytest


@dataclass(frozen=True)
class Pki:
    ca_pem: Path
    leaf_pem: Path  # signé par l'autorité, SAN localhost et 127.0.0.1
    leaf_key: Path
    wrong_pem: Path  # signé par la même autorité, mais pour un autre nom
    wrong_key: Path


def _openssl(*args: str) -> None:
    subprocess.run(["openssl", *args], check=True, capture_output=True)


@pytest.fixture(scope="session")
def pki(tmp_path_factory) -> Pki:
    if not shutil.which("openssl"):
        pytest.skip("openssl absent")
    root = tmp_path_factory.mktemp("pki")
    ca_key, ca_pem = root / "ca.key", root / "ca.pem"
    _openssl("req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "2", "-subj", "/CN=Apple Test Root/O=Test Org",
             "-addext", "basicConstraints=critical,CA:TRUE", "-addext", "keyUsage=critical,keyCertSign,cRLSign",
             "-keyout", str(ca_key), "-out", str(ca_pem))  # fmt: skip

    def leaf(name: str, subject: str, san: str) -> tuple[Path, Path]:
        key, csr, pem, ext = (
            root / f"{name}.key",
            root / f"{name}.csr",
            root / f"{name}.pem",
            root / f"{name}.cnf",
        )
        ext.write_text(f"subjectAltName={san}\nbasicConstraints=CA:FALSE\n")
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
        _openssl("x509", "-req", "-in", str(csr), "-CA", str(ca_pem), "-CAkey", str(ca_key), "-CAcreateserial",
                 "-days", "2", "-extfile", str(ext), "-out", str(pem))  # fmt: skip
        return pem, key

    leaf_pem, leaf_key = leaf("leaf", "/CN=sylvan.test", "DNS:localhost,IP:127.0.0.1")
    wrong_pem, wrong_key = leaf("wrong", "/CN=autre.test", "DNS:autre.test")
    return Pki(ca_pem, leaf_pem, leaf_key, wrong_pem, wrong_key)


@contextlib.contextmanager
def _tls_server(
    cert: Path, key: Path, routes: dict[str, tuple[int, dict[str, str], bytes]] | None = None
):
    """Un serveur https local (127.0.0.1, port libre) qui n'envoie que son propre certificat ; rend son port."""
    table = routes or {}

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            status, headers, body = table.get(self.path, (404, {}, b""))
            self.send_response(status)
            for name, value in headers.items():
                self.send_header(name, value)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_):
            pass

    class Quiet(http.server.ThreadingHTTPServer):
        def handle_error(
            self, request, client_address
        ):  # une poignée de main refusée par le client n'est pas une erreur
            pass

    server = Quiet(("127.0.0.1", 0), Handler)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(cert, key)
    server.socket = context.wrap_socket(server.socket, server_side=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server.server_port
    finally:
        server.shutdown()
        server.server_close()


@pytest.fixture
def tls_server():
    """`with tls_server(cert, key, routes) as port:` : un serveur https local pour la durée du bloc."""
    return _tls_server
