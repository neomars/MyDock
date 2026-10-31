import gzip
import http.client
import json
import socket
import ssl
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace

import pytest

from mydock_dock.markets.errors import BadResponse, NetworkError
from mydock_dock.markets.http import HttpResponse, UrllibFetcher, build_url


class Handler(BaseHTTPRequestHandler):
    seen: list[dict] = []

    def log_message(self, *args):
        pass

    def reply(self, status, body=b"", headers=None):
        self.send_response(status)
        for name, value in (headers or {}).items():
            self.send_header(name, value)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        Handler.seen.append({"path": self.path, "headers": dict(self.headers)})
        path = self.path.split("?")[0]
        if path == "/json":
            self.reply(200, json.dumps({"ok": True}).encode(), {"Content-Type": "application/json"})
        elif path == "/gzip":
            self.reply(200, gzip.compress(b'{"zipped": true}'), {"Content-Encoding": "gzip"})
        elif path == "/bomb":
            self.reply(200, gzip.compress(b"0" * 5_000_000), {"Content-Encoding": "gzip"})
        elif path == "/badgzip":
            self.reply(200, b"pas du gzip", {"Content-Encoding": "gzip"})
        elif path == "/big":
            self.reply(200, b"x" * 3_000)
        elif path == "/limited":
            self.reply(429, b"slow down", {"Retry-After": "7"})
        elif path == "/missing":
            self.reply(404, b'{"error": "inconnu"}')
        elif path == "/redirect-same":
            self.reply(302, b"", {"Location": "/json"})
        elif path == "/redirect-other":
            self.reply(302, b"", {"Location": "http://autre.example.invalid/json"})
        elif path == "/slow":
            time.sleep(1.5)
            self.reply(200, b"trop tard")
        else:
            self.reply(500, b"?")


@pytest.fixture
def server():
    Handler.seen = []
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield SimpleNamespace(url=f"http://127.0.0.1:{httpd.server_address[1]}")
    httpd.shutdown()
    httpd.server_close()
    thread.join(2)


async def test_a_json_reply_comes_back_with_its_status_and_lowercase_headers(server):
    response = await UrllibFetcher().get(server.url + "/json")
    assert response.status == 200
    assert json.loads(response.body) == {"ok": True}
    assert response.header("Content-Type") == "application/json"
    assert "content-type" in response.headers


async def test_the_request_carries_the_parameters_and_our_identity(server):
    await UrllibFetcher().get(
        server.url + "/json", params={"symbols": '["A B","C"]', "x": "é"}, headers={"X-Test": "1"}
    )
    seen = Handler.seen[0]
    assert seen["path"] == "/json?symbols=%5B%22A%20B%22%2C%22C%22%5D&x=%C3%A9"
    assert seen["headers"]["User-Agent"].startswith("MyDock-dock/")
    assert seen["headers"]["Accept-Encoding"] == "gzip"
    assert seen["headers"]["X-Test"] == "1"


async def test_the_caller_can_replace_the_user_agent(server):
    await UrllibFetcher().get(server.url + "/json", headers={"User-Agent": "Mozilla/5.0 test"})
    assert Handler.seen[0]["headers"]["User-Agent"] == "Mozilla/5.0 test"


async def test_a_compressed_reply_is_decompressed(server):
    response = await UrllibFetcher().get(server.url + "/gzip")
    assert json.loads(response.body) == {"zipped": True}


async def test_a_decompression_bomb_is_refused(server):
    with pytest.raises(BadResponse, match="démesurée"):
        await UrllibFetcher(max_bytes=100_000).get(server.url + "/bomb")


async def test_a_corrupt_compressed_reply_is_refused(server):
    with pytest.raises(BadResponse, match="compressée illisible"):
        await UrllibFetcher().get(server.url + "/badgzip")


async def test_a_reply_larger_than_the_limit_is_abandoned(server):
    with pytest.raises(BadResponse, match="démesurée"):
        await UrllibFetcher(max_bytes=1_000).get(server.url + "/big")
    assert (await UrllibFetcher(max_bytes=4_000).get(server.url + "/big")).status == 200


async def test_an_http_error_is_a_reply_not_an_exception(server):
    limited = await UrllibFetcher().get(server.url + "/limited")
    assert (limited.status, limited.retry_after) == (429, 7.0)
    missing = await UrllibFetcher().get(server.url + "/missing")
    assert (missing.status, json.loads(missing.body)) == (404, {"error": "inconnu"})
    assert (await UrllibFetcher().get(server.url + "/inconnue")).status == 500


async def test_a_redirect_to_the_same_machine_is_followed_but_not_to_another(server):
    followed = await UrllibFetcher().get(server.url + "/redirect-same")
    assert (followed.status, json.loads(followed.body)) == (200, {"ok": True})
    refused = await UrllibFetcher().get(server.url + "/redirect-other")
    assert refused.status == 302  # la redirection hors de la machine n'est pas suivie


async def test_a_slow_server_ends_in_a_timeout_message(server):
    started = time.monotonic()
    with pytest.raises(NetworkError, match="ne répond pas"):
        await UrllibFetcher().get(server.url + "/slow", timeout=0.3)
    assert time.monotonic() - started < 1.2


async def test_a_closed_port_is_reported_as_an_impossible_connection():
    with pytest.raises(NetworkError, match=r"Connexion à 127\.0\.0\.1 impossible"):
        await UrllibFetcher().get("http://127.0.0.1:9/")


def failing_opener(exc):
    def open_(*_args, **_kwargs):
        raise exc

    return SimpleNamespace(open=open_)


@pytest.mark.parametrize(
    ("failure", "message"),
    [
        (
            urllib.error.URLError(socket.gaierror(-2, "Name or service not known")),
            "introuvable.*Internet",
        ),
        (urllib.error.URLError(ssl.SSLError("certificate verify failed")), "certificat"),
        (urllib.error.URLError(TimeoutError("timed out")), "ne répond pas"),
        (urllib.error.URLError(ConnectionRefusedError(111, "refused")), "impossible"),
        (ConnectionResetError(104, "reset"), "interrompue"),
        (http.client.RemoteDisconnected("fermé sans réponse"), "interrompue"),
    ],
)
async def test_network_failures_have_a_message_for_the_screen(failure, message):
    fetcher = UrllibFetcher()
    fetcher._opener = failing_opener(failure)
    with pytest.raises(NetworkError, match=message):
        await fetcher.get("https://exemple.invalid/x")


def test_build_url_encodes_and_keeps_the_path():
    assert build_url("https://a.example/p", None) == "https://a.example/p"
    assert build_url("https://a.example/p", {}) == "https://a.example/p"
    assert (
        build_url("https://a.example/p", {"a": "1 2", "b": "&="})
        == "https://a.example/p?a=1%202&b=%26%3D"
    )


def test_retry_after_is_read_only_when_it_is_a_sensible_number_of_seconds():
    def reply(value):
        return HttpResponse(429, {"retry-after": value}, b"")

    assert reply("12").retry_after == 12.0
    assert reply("0").retry_after == 0.0
    assert reply("-5").retry_after is None
    assert reply("Wed, 21 Oct 2026 07:28:00 GMT").retry_after is None
    assert reply("999999").retry_after is None
    assert HttpResponse(429, {}, b"").retry_after is None


def test_a_redirect_never_goes_back_to_clear_text_nor_to_another_machine():
    from mydock_dock.markets.http import _SameHostRedirects

    handler = _SameHostRedirects()
    secure = urllib.request.Request("https://api.exemple.test/a")
    plain = urllib.request.Request("http://api.exemple.test/a")

    def follow(request, target):
        return handler.redirect_request(request, None, 302, "Found", {}, target)

    assert follow(secure, "https://api.exemple.test/b") is not None
    assert follow(secure, "http://api.exemple.test/b") is None  # retour en clair
    assert follow(secure, "https://autre.exemple.test/b") is None
    assert follow(secure, "ftp://api.exemple.test/b") is None
    assert follow(plain, "https://api.exemple.test/b") is not None  # monter en https reste permis
