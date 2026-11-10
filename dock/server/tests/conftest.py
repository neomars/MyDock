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


@dataclass(frozen=True)
class ApplePki:
    """Comme la chaîne d'Apple : serveur ← « Apple Server Authentication CA » ← « Apple Root CA », racine non envoyée."""

    root_pem: Path
    root_der: bytes
    inter_pem: Path
    chain_pem: Path  # ce que le serveur envoie : son certificat, puis l'intermédiaire
    leaf_key: Path


@pytest.fixture(scope="session")
def apple_pki(tmp_path_factory) -> ApplePki:
    if not shutil.which("openssl"):
        pytest.skip("openssl absent")
    root = tmp_path_factory.mktemp("apple_pki")

    def make(
        name: str, subject: str, signer: tuple[Path, Path] | None, extensions: str
    ) -> tuple[Path, Path]:
        key, pem, ext = root / f"{name}.key", root / f"{name}.pem", root / f"{name}.cnf"
        ext.write_text(extensions)
        if signer is None:
            _openssl("req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "2", "-subj", subject,
                     "-addext", "basicConstraints=critical,CA:TRUE", "-addext", "keyUsage=critical,keyCertSign,cRLSign",
                     "-keyout", str(key), "-out", str(pem))  # fmt: skip
        else:
            csr = root / f"{name}.csr"
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
                     "-days", "2", "-extfile", str(ext), "-out", str(pem))  # fmt: skip
        return pem, key

    root_pem, root_key = make("aroot", "/CN=Apple Root CA/O=Apple Inc.", None, "")
    inter_pem, inter_key = make(
        "ainter", "/CN=Apple Server Authentication CA/O=Apple Inc.", (root_pem, root_key),
        "basicConstraints=critical,CA:TRUE,pathlen:0\nkeyUsage=critical,keyCertSign,cRLSign\n",
    )  # fmt: skip
    leaf_pem, leaf_key = make(
        "aleaf", "/CN=sylvan.apple.com/O=Apple Inc.", (inter_pem, inter_key),
        "subjectAltName=DNS:localhost,IP:127.0.0.1\nbasicConstraints=CA:FALSE\nextendedKeyUsage=serverAuth\n",
    )  # fmt: skip
    chain = root / "achain.pem"
    chain.write_text(leaf_pem.read_text() + inter_pem.read_text())
    return ApplePki(
        root_pem, ssl.PEM_cert_to_DER_cert(root_pem.read_text()), inter_pem, chain, leaf_key
    )


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
    table = (
        routes if routes is not None else {}
    )  # le test peut y ajouter des routes une fois le serveur lancé

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


HAVE_FFMPEG = bool(shutil.which("ffmpeg") and shutil.which("ffprobe"))


@pytest.fixture(scope="session")
def clips(tmp_path_factory):
    """De vrais petits films : H.264 en MP4 léger, H.264 trop large, WebM VP8, MPEG-4 en AVI."""
    if not HAVE_FFMPEG:
        return {}
    root = tmp_path_factory.mktemp("clips")

    def make(name: str, size: str, *codec: str) -> bytes:
        path = root / name
        subprocess.run(
            ["ffmpeg", "-loglevel", "error", "-y", "-f", "lavfi", "-i", f"testsrc2=size={size}:rate=12:duration=1", *codec, str(path)],
            check=True,
        )  # fmt: skip
        return path.read_bytes()

    return {
        "small.mp4": make("small.mp4", "320x180", "-c:v", "libx264", "-pix_fmt", "yuv420p"),
        "wide.mp4": make("wide.mp4", "1920x1080", "-c:v", "libx264", "-pix_fmt", "yuv420p"),
        "small.webm": make("small.webm", "320x180", "-c:v", "libvpx"),
        "old.avi": make("old.avi", "320x180", "-c:v", "mpeg4"),
    }
