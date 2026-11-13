"""Le portail du réseau local : la page d'installation, le téléchargement du paquet du PC, l'association par code."""

import hashlib
import re

import httpx
import pytest
from websockets.asyncio.client import connect
from websockets.exceptions import InvalidStatus

from mydock_agent.testing import TOKEN, running_agent
from mydock_dock import portal
from mydock_dock.agent_link import LinkState
from mydock_dock.clients import Client
from mydock_dock.config import DockConfig
from mydock_dock.pairing import MAX_WRONG, THROTTLE_MAX
from mydock_dock.portal import (
    PORTAL_CSP,
    SCRIPT,
    STYLE,
    clean_name,
    create_portal_app,
    explain_bind_error,
    human_size,
    render_page,
    setup_command,
)
from mydock_dock.testing import call, eventually, free_port, recv_json, running_dock, snapshot_of

DEB = "mydock-agent_0.7.0_all.deb"
DEB_CONTENT = b"!<arch>\ndebian-binary" + bytes(range(256)) * 40
OTHER_TOKEN = "autre-jeton-" + "y" * 31


@pytest.fixture
def clients_dir(tmp_path):
    folder = tmp_path / "clients"
    folder.mkdir()
    (folder / DEB).write_bytes(DEB_CONTENT)
    return folder


@pytest.fixture
async def dock(tmp_path, clients_dir):
    """Un dock neuf : aucun PC associé, le portail ouvert, un code à l'écran."""
    async with running_dock(tmp_path, portal=True, clients_dir=clients_dir) as running:
        await eventually(lambda: running.server.pairing.is_open())
        yield running


def web(dock) -> httpx.AsyncClient:
    return httpx.AsyncClient(trust_env=False, base_url=dock.portal_origin, timeout=10)


def code_of(dock) -> str:
    return dock.server.pairing.info()["code"]


def pair_request(code: str, port: int, **fields):
    return {"code": code, "token": TOKEN, "port": port, "name": "portable", **fields}


# --- la page ---------------------------------------------------------------------------------------


async def test_the_page_offers_the_package_and_the_two_commands(dock):
    async with web(dock) as client:
        reply = await client.get("/")
    assert reply.status_code == 200
    assert reply.headers["content-type"].startswith("text/html")
    assert reply.headers["cache-control"] == "no-store"
    page = reply.text
    assert f'href="/clients/{DEB}"' in page
    assert f"sudo apt install ./{DEB}" in page
    assert re.search(
        r"mydock-agent setup --pi \d+\.\d+\.\d+\.\d+ --portal-port \d+ --open-firewall", page
    )
    assert hashlib.sha256(DEB_CONTENT).hexdigest() in page
    assert "0.7.0" in page


async def test_the_page_is_self_contained_and_its_policy_allows_only_its_own_style_and_script(dock):
    async with web(dock) as client:
        reply = await client.get("/")
    policy = reply.headers["content-security-policy"]
    assert policy == PORTAL_CSP
    for forbidden in ("unsafe-inline", "unsafe-eval", "http:", "https:", "*"):
        assert forbidden not in policy
    assert "default-src 'none'" in policy
    assert "frame-ancestors 'none'" in policy
    style = re.search(r"<style>(.*?)</style>", reply.text, re.S).group(1)
    script = re.search(r"<script>(.*?)</script>", reply.text, re.S).group(1)
    for text in (style, script):
        digest = hashlib.sha256(text.encode()).digest()
        import base64

        assert f"'sha256-{base64.b64encode(digest).decode()}'" in policy
    assert style == STYLE
    assert script == SCRIPT
    assert not re.search(r'(src|href)="https?:', reply.text)  # aucune ressource de l'extérieur
    assert reply.headers["x-content-type-options"] == "nosniff"
    assert reply.headers["x-frame-options"] == "DENY"
    assert reply.headers["referrer-policy"] == "no-referrer"


async def test_the_page_never_repeats_what_the_request_says(dock):
    async with web(dock) as client:
        reply = await client.get(
            "/?x=<script>alert(1)</script>",
            headers={
                "Host": "evil.example",
                "Referer": "http://evil.example/",
                "X-Forwarded-For": "6.6.6.6",
            },
        )
    assert "evil.example" not in reply.text
    assert "alert(1)" not in reply.text
    assert "6.6.6.6" not in reply.text


async def test_without_a_package_the_page_says_so_instead_of_a_dead_link(tmp_path):
    async with running_dock(tmp_path, portal=True) as dock, web(dock) as client:  # aucun paquet
        page = (await client.get("/")).text
    assert "Aucun paquet pour PC" in page
    assert "/clients/" not in page


def test_the_commands_name_this_pi_and_only_add_the_portal_port_when_it_is_not_80():
    assert (
        setup_command("192.168.1.50", 80) == "mydock-agent setup --pi 192.168.1.50 --open-firewall"
    )
    assert (
        setup_command("192.168.1.50", None)
        == "mydock-agent setup --pi 192.168.1.50 --open-firewall"
    )
    assert setup_command("192.168.1.50", 8081) == (
        "mydock-agent setup --pi 192.168.1.50 --portal-port 8081 --open-firewall"
    )


def test_everything_written_in_the_page_is_escaped():
    hostile = Client("deb", "<b>x</b>", '"><script>x</script>.deb', None, "1.0", 10, "<i>")  # type: ignore[arg-type]
    page = render_page(hostname="<img src=x onerror=1>", address="1.2.3.4", port=80, client=hostile)
    assert "<script>x</script>" not in page
    assert "<img src=x" not in page
    assert "&lt;img src=x onerror=1&gt;" in page


def test_sizes_are_written_for_people():
    assert human_size(1) == "1 Ko"
    assert human_size(86_000) == "84 Ko"
    assert human_size(3 * 1024 * 1024 + 200_000) == "3,2 Mo"


@pytest.mark.parametrize(
    ("typed", "shown"),
    [("portable", "portable"), ("  mon   PC  ", "mon PC"), ("a<b>c&'\"`d", "abcd"), ("x" * 200, "x" * 60),
     ("\x00\x1b[31mrouge", "[31mrouge"), (None, "PC"), (12, "PC"), ("", "PC"), ("<>", "PC")],
)  # fmt: skip
def test_the_name_a_pc_gives_itself_is_cleaned(typed, shown):
    assert clean_name(typed) == shown


# --- l'adresse du Pi écrite dans la commande ----------------------------------------------------------------


async def test_the_command_uses_the_address_this_pi_has_toward_the_pc(dock, monkeypatch):
    monkeypatch.setattr(portal, "local_address_towards", lambda peer: "192.168.1.50")
    transport = httpx.ASGITransport(
        app=create_portal_app(dock.server), client=("192.168.1.77", 40000)
    )
    async with httpx.AsyncClient(transport=transport, base_url="http://pi.test") as client:
        page = (await client.get("/")).text
    assert "mydock-agent setup --pi 192.168.1.50" in page


async def test_a_browser_on_the_pi_itself_is_not_given_a_loopback_address(dock, monkeypatch):
    monkeypatch.setattr(portal, "local_address_towards", lambda peer: "127.0.0.1")
    monkeypatch.setattr(portal, "local_ipv4_addresses", lambda: ["192.168.1.9"])
    transport = httpx.ASGITransport(app=create_portal_app(dock.server), client=("127.0.0.1", 40000))
    async with httpx.AsyncClient(transport=transport, base_url="http://pi.test") as client:
        page = (await client.get("/")).text
    assert "--pi 192.168.1.9" in page
    assert "--pi 127.0.0.1" not in page


# --- le téléchargement -------------------------------------------------------------------------------------


async def test_the_package_downloads_as_an_attachment_with_the_exact_bytes(dock):
    async with web(dock) as client:
        reply = await client.get(f"/clients/{DEB}")
    assert reply.status_code == 200
    assert reply.content == DEB_CONTENT
    assert reply.headers["content-type"] == "application/vnd.debian.binary-package"
    assert f'filename="{DEB}"' in reply.headers["content-disposition"]
    assert reply.headers["content-disposition"].startswith("attachment")
    assert reply.headers["x-content-type-options"] == "nosniff"
    assert int(reply.headers["content-length"]) == len(DEB_CONTENT)


async def test_a_head_request_gives_the_size_without_the_body(dock):
    async with web(dock) as client:
        reply = await client.head(f"/clients/{DEB}")
    assert reply.status_code == 200
    assert int(reply.headers["content-length"]) == len(DEB_CONTENT)
    assert reply.content == b""


@pytest.mark.parametrize(
    "path",
    [
        "/clients/mydock-agent_9.9.9_all.deb",  # bien formé, mais absent
        "/clients/",
        "/clients",
        "/clients/..%2f..%2fetc%2fpasswd",
        "/clients/%2e%2e/%2e%2e/etc/passwd",
        "/clients/../../etc/passwd",
        f"/clients/{DEB}%00.txt",
        f"/clients/{DEB}/",
        "/clients/MYDOCK-AGENT_0.7.0_ALL.DEB",
        "/clients/evil.deb",
        "/clients/" + "a" * 5000,
    ],
)
async def test_only_a_listed_package_can_be_downloaded_whatever_the_address(dock, path):
    async with web(dock) as client:
        reply = await client.get(path)
    assert reply.status_code == 404
    assert b"root:" not in reply.content


# --- l'état public ------------------------------------------------------------------------------------------


async def test_the_public_state_says_only_what_a_visitor_may_know(dock):
    async with web(dock) as client:
        reply = await client.get("/api/portal")
    data = reply.json()
    assert set(data) == {
        "ok",
        "app",
        "version",
        "hostname",
        "paired",
        "connected",
        "pairing",
        "clients",
    }
    assert data["app"] == "mydock"
    assert data["paired"] is False
    assert data["pairing"] is True
    assert data["clients"][0]["file"] == DEB
    assert data["clients"][0]["sha256"] == hashlib.sha256(DEB_CONTENT).hexdigest()
    body = reply.text
    assert code_of(dock).replace("-", "") not in body.replace("-", "")
    assert TOKEN not in body
    assert "path" not in data["clients"][0]
    assert reply.headers["cache-control"] == "no-store"


# --- ce que le portail ne sert pas --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "path",
    ["/ws", "/api/health", "/api/screen", "/assets/index.js", "/index.html", "/favicon.ico", "/api/pair/x",
     "/.env", "/etc/mydock/dock.toml", "/docs", "/openapi.json", "/api"],
)  # fmt: skip
async def test_every_other_address_is_a_plain_404(dock, path):
    async with web(dock) as client:
        reply = await client.get(path)
    assert reply.status_code == 404


@pytest.mark.parametrize("method", ["PUT", "DELETE", "PATCH"])
async def test_other_methods_are_refused(dock, method):
    async with web(dock) as client:
        for path in (
            "/",
            f"/clients/{DEB}",
            "/api/portal",
            "/api/pair",
            "/jeux",
            "/api/retro/login",
            "/api/retro/upload",
        ):
            assert (await client.request(method, path)).status_code == 405, (method, path)
        assert (await client.post("/")).status_code == 405
        assert (await client.post(f"/clients/{DEB}")).status_code == 405


async def test_the_control_interface_is_not_reachable_from_the_portal(dock):
    """Le WebSocket de pilotage vit sur un autre port, local ; le portail le refuse."""
    with pytest.raises(InvalidStatus):
        async with connect(
            f"ws://127.0.0.1:{dock.server.portal.port}/ws", proxy=None, open_timeout=3
        ):
            pass


async def test_a_visitor_cannot_learn_the_pc_or_the_token_from_any_answer(tmp_path, clients_dir):
    async with (
        running_agent() as agent,
        running_dock(tmp_path, agent_port=agent.port, portal=True, clients_dir=clients_dir) as dock,
    ):
        await eventually(lambda: dock.server.link.connected)
        async with web(dock) as client:
            bodies = [
                (await client.get("/")).text,
                (await client.get("/api/portal")).text,
                (await client.post("/api/pair", json={"code": "AAAA-AAAA"})).text,
            ]
        for body in bodies:
            assert TOKEN not in body
            assert "127.0.0.1:" + str(agent.port) not in body
        assert '"paired":true' in bodies[1].replace(" ", "")


# --- l'association ----------------------------------------------------------------------------------------


async def test_a_pc_pairs_with_the_code_and_the_dock_configures_itself(dock):
    async with running_agent() as agent:
        code = code_of(dock)
        async with web(dock) as client:
            reply = await client.post("/api/pair", json=pair_request(code.lower(), agent.port))
        assert reply.status_code == 200, reply.text
        answer = reply.json()
        assert answer["ok"] is True
        assert answer["pc"] == {"host": "127.0.0.1", "port": agent.port}
        assert answer["dock"]["version"]
        assert answer["pi_ips"]  # ce que l'agent doit autoriser
        assert reply.headers["cache-control"] == "no-store"
        assert TOKEN not in reply.text  # le jeton ne revient pas

        # le dock s'est configuré : adresse, port, jeton, enregistrés sur le disque
        assert dock.config.agent_host == "127.0.0.1"
        assert dock.config.agent_port == agent.port
        assert dock.config.token == TOKEN
        saved = DockConfig.load(dock.config.path)
        assert (saved.agent_host, saved.agent_port, saved.token) == ("127.0.0.1", agent.port, TOKEN)
        assert dock.server.store.pc["hostname"] == "portable"
        await eventually(lambda: dock.server.link.connected)
        # la session est terminée, l'écran l'a appris
        assert not dock.server.pairing.is_open()
        assert dock.server.pairing.info()["last"]["result"] == "paired"
        assert dock.server.pairing.info()["last"]["name"] == "portable"


async def test_the_screen_of_the_dock_sees_the_code_and_then_the_pairing(dock):
    async with dock.connect() as ws:
        await recv_json(ws)  # hello
        first = await snapshot_of(ws, "dock")
        assert first["data"]["pairing"]["open"] is True
        assert first["data"]["pairing"]["code"] == code_of(dock)
        assert first["data"]["portal"]["port"] == dock.server.portal.port
        assert first["data"]["portal"]["urls"]
        assert first["data"]["portal"]["client"] == {"file": DEB, "version": "0.7.0"}
        async with running_agent() as agent, web(dock) as client:
            reply = await client.post("/api/pair", json=pair_request(code_of(dock), agent.port))
            assert reply.status_code == 200
            done = await snapshot_of(ws, "dock")
            while done["data"]["pairing"].get("last") is None:
                done = await snapshot_of(ws, "dock")
            assert done["data"]["pairing"] == {
                "open": False,
                "last": {**done["data"]["pairing"]["last"], "result": "paired", "name": "portable"},
            }
            assert done["data"]["pc"]["host"] == "127.0.0.1"
            assert done["data"]["pc"]["token_set"] is True


async def test_a_wrong_code_is_refused_and_five_of_them_cancel_the_association(dock):
    async with web(dock) as client:
        for attempt in range(MAX_WRONG - 1):
            reply = await client.post("/api/pair", json=pair_request("AAAA-AAAA", 8765))
            assert (reply.status_code, reply.json()["error"]) == (403, "bad_code"), attempt
        last = await client.post("/api/pair", json=pair_request("AAAA-AAAA", 8765))
        assert (last.status_code, last.json()["error"]) == (403, "locked")
        later = await client.post(
            "/api/pair",
            json=pair_request(
                code_of(dock) if dock.server.pairing.is_open() else "AAAA-AAAA", 8765
            ),
        )
        assert later.json()["error"] == "not_open"
    assert not dock.config.agent_host  # personne n'est associé
    assert dock.server.pairing.info()["last"]["result"] == "locked"


async def test_a_pairing_must_be_opened_on_the_dock_first_once_a_pc_is_associated(
    tmp_path, clients_dir
):
    async with (
        running_agent() as agent,
        running_dock(tmp_path, agent_port=agent.port, portal=True, clients_dir=clients_dir) as dock,
    ):
        await eventually(lambda: dock.server.link.connected)
        assert not dock.server.pairing.is_open()  # un PC est déjà là : pas de code spontané
        async with web(dock) as client:
            reply = await client.post("/api/pair", json=pair_request("K7QM-2XPD", agent.port))
        assert (reply.status_code, reply.json()["error"]) == (409, "not_open")
        assert "Associer un PC" in reply.json()["message"]


async def test_the_screen_can_open_a_pairing_to_replace_the_pc_and_close_it(tmp_path, clients_dir):
    async with (
        running_agent() as agent,
        running_dock(tmp_path, agent_port=agent.port, portal=True, clients_dir=clients_dir) as dock,
        dock.connect() as ws,
    ):
        await eventually(lambda: dock.server.link.connected)
        await recv_json(ws)
        assert (await call(ws, "o1", "dock.pairing.open"))["ok"] is True
        assert dock.server.pairing.is_open()
        shown = await snapshot_of(ws, "dock")
        while not shown["data"]["pairing"]["open"]:
            shown = await snapshot_of(ws, "dock")
        assert (await call(ws, "c1", "dock.pairing.close"))["ok"] is True
        assert not dock.server.pairing.is_open()
        assert dock.server.pairing.info()["last"]["result"] == "closed"


async def test_a_correct_code_with_an_unreachable_agent_explains_and_keeps_the_session(dock):
    code = code_of(dock)
    async with web(dock) as client:
        reply = await client.post("/api/pair", json=pair_request(code, free_port()))
    assert reply.status_code == 502
    body = reply.json()
    assert body["error"] == "unreachable"
    assert "127.0.0.1" in body["message"]
    assert body["pi_ips"], "le PC doit apprendre quelle adresse du Pi autoriser"
    assert dock.server.pairing.is_open()  # le code reste bon : on peut corriger et recommencer
    assert dock.server.pairing.info()["wrong_left"] == MAX_WRONG  # et il n'a pas coûté d'essai
    assert not dock.config.agent_host


async def test_an_agent_that_refuses_the_token_is_reported_as_refused(dock):
    async with running_agent() as agent, web(dock) as client:
        reply = await client.post(
            "/api/pair", json={**pair_request(code_of(dock), agent.port), "token": OTHER_TOKEN}
        )
    assert reply.status_code == 502
    assert reply.json()["error"] == "refused"
    assert dock.server.pairing.is_open()
    assert not dock.config.agent_host


@pytest.mark.parametrize(
    "fields",
    [{"token": "court"}, {"token": 12345}, {"token": None}, {"port": 0}, {"port": 70000},
     {"port": "8765"}, {"port": True}, {"port": None}],
)  # fmt: skip
async def test_a_right_code_with_a_bad_token_or_port_is_a_bad_request_and_costs_no_attempt(
    dock, fields
):
    async with web(dock) as client:
        reply = await client.post("/api/pair", json={**pair_request(code_of(dock), 8765), **fields})
    assert (reply.status_code, reply.json()["error"]) == (400, "bad_request")
    assert dock.server.pairing.info()["wrong_left"] == MAX_WRONG
    assert dock.server.pairing.is_open()


async def test_the_request_must_be_small_json(dock):
    async with web(dock) as client:
        as_text = await client.post(
            "/api/pair", content=b'{"code":"x"}', headers={"Content-Type": "text/plain"}
        )
        assert as_text.status_code == 415
        broken = await client.post(
            "/api/pair", content=b"{pas du json", headers={"Content-Type": "application/json"}
        )
        assert broken.status_code == 400
        not_object = await client.post("/api/pair", json=["K7QM-2XPD"])
        assert not_object.status_code == 400
        big = await client.post("/api/pair", json={"code": "x" * 5000})
        assert big.status_code == 413

        async def stream():  # sans Content-Length : le plafond vaut aussi pour un corps en morceaux
            for _ in range(10):
                yield b" " * 1000

        chunked = await client.post(
            "/api/pair", content=stream(), headers={"Content-Type": "application/json"}
        )
        assert chunked.status_code == 413
    assert (
        dock.server.pairing.info()["wrong_left"] == MAX_WRONG
    )  # aucune de ces demandes n'a compté


async def test_a_single_address_cannot_hammer_the_door(tmp_path, clients_dir):
    async with (
        running_agent() as agent,
        running_dock(tmp_path, agent_port=agent.port, portal=True, clients_dir=clients_dir) as dock,
        web(dock) as client,
    ):
        statuses = [
            (await client.post("/api/pair", json={"code": "K7QM-2XPD"})).status_code
            for _ in range(THROTTLE_MAX + 3)
        ]
    assert statuses[:THROTTLE_MAX] == [409] * THROTTLE_MAX
    assert statuses[THROTTLE_MAX:] == [429] * 3


async def test_the_code_never_leaves_through_the_portal(dock):
    code = code_of(dock)
    raw = code.replace("-", "")
    async with web(dock) as client:
        answers = [
            await client.get("/"),
            await client.get("/api/portal"),
            await client.post("/api/pair", json=pair_request("AAAA-AAAA", 8765)),
            await client.post(
                "/api/pair", content=b"{", headers={"Content-Type": "application/json"}
            ),
            await client.get("/clients/nope.deb"),
        ]
    for answer in answers:
        assert code not in answer.text
        assert raw not in answer.text
        assert code not in str(answer.headers)


async def test_pairing_another_pc_replaces_the_first_and_forgets_its_wake_on_lan_address(
    tmp_path, clients_dir, monkeypatch
):
    async def reachable(settings):
        return LinkState.CONNECTED, "Connexion réussie.", {"version": "x"}

    monkeypatch.setattr("mydock_dock.app.probe", reachable)
    async with running_dock(
        tmp_path,
        agent_port=8765,
        agent_host="192.0.2.10",
        portal=True,
        clients_dir=clients_dir,
        wol_mac="02:00:5e:10:00:01",
    ) as dock:
        dock.server.store.update_pc(
            hostname="ancien",
            interfaces=[{"name": "enp3s0", "mac": "02:00:5e:10:00:01", "kind": "ethernet"}],
        )
        dock.server.pairing.start()
        result = await dock.server.complete_pairing("192.0.2.20", 8765, OTHER_TOKEN, "nouveau")
        assert result["pc"] == {"host": "192.0.2.20", "port": 8765}
        assert dock.config.agent_host == "192.0.2.20"
        assert dock.config.token == OTHER_TOKEN
        assert dock.config.wol_mac == ""  # l'adresse MAC de l'ancien PC ne vaut plus rien
        assert dock.server.store.pc["hostname"] == "nouveau"
        assert dock.server.store.pc["interfaces"] == []


async def test_pairing_the_same_pc_again_keeps_its_wake_on_lan_address(
    tmp_path, clients_dir, monkeypatch
):
    async def reachable(settings):
        return LinkState.CONNECTED, "Connexion réussie.", {"version": "x"}

    monkeypatch.setattr("mydock_dock.app.probe", reachable)
    async with running_dock(
        tmp_path,
        agent_port=8765,
        agent_host="192.0.2.10",
        portal=True,
        clients_dir=clients_dir,
        wol_mac="02:00:5e:10:00:01",
    ) as dock:
        await dock.server.complete_pairing("192.0.2.10", 8765, OTHER_TOKEN, "réinstallé")
        assert dock.config.wol_mac == "02:00:5e:10:00:01"
        assert dock.config.token == OTHER_TOKEN


# --- un portail qui ne peut pas s'ouvrir ne gêne rien ----------------------------------------------------------------


async def test_a_busy_port_is_reported_and_the_dock_keeps_working(tmp_path, clients_dir):
    import socket

    with socket.socket() as squatter:
        squatter.bind(("127.0.0.1", 0))
        squatter.listen(1)
        busy = squatter.getsockname()[1]
        async with running_dock(tmp_path, portal=True, clients_dir=clients_dir) as dock:
            # le port est fixé après coup : le serveur de test l'a déjà ouvert sur un port libre ;
            # on vérifie donc la règle par la fonction qui l'explique, et par un second portail.
            assert dock.server.portal.error is None
            from mydock_dock.portal import Portal

            second = Portal(dock.server)
            second.bind_host, second.bind_port = "127.0.0.1", busy
            second.start()
            assert second.port is None
            assert "déjà pris" in second.error
            assert not second.running
            await second.stop()  # arrêter un portail qui n'a jamais démarré ne plante pas
            async with dock.connect() as ws:
                assert (await call(ws, "p1", "dock.ping"))["ok"] is True  # l'interface continue


def test_bind_errors_are_explained_in_french():
    import errno

    denied = explain_bind_error(OSError(errno.EACCES, "Permission denied"), 80)
    assert "Droit refusé pour le port 80" in denied
    assert "[portal]" in denied
    busy = explain_bind_error(OSError(errno.EADDRINUSE, "Address already in use"), 80)
    assert "déjà pris" in busy
    other = explain_bind_error(OSError(errno.ENETDOWN, "Network is down"), 80)
    assert "Network is down" in other


async def test_the_dock_without_a_portal_opens_no_pairing_and_refuses_to_open_one(tmp_path):
    async with running_dock(tmp_path) as dock, dock.connect() as ws:  # portail éteint
        await recv_json(ws)
        info = (await snapshot_of(ws, "dock"))["data"]
        assert dock.server.portal.port is None
        assert not dock.server.pairing.is_open()
        refused = await call(ws, "o1", "dock.pairing.open")
        assert refused["ok"] is False
        assert refused["e"]["code"] == "portal_down"
        assert info["portal"]["port"] is None
        assert info["portal"]["urls"] == []
        assert info["pairing"]["open"] is False


async def test_stopping_the_dock_closes_the_portal(tmp_path, clients_dir):
    async with running_dock(tmp_path, portal=True, clients_dir=clients_dir) as dock:
        origin = dock.portal_origin
        async with httpx.AsyncClient(trust_env=False, timeout=3) as client:
            assert (await client.get(origin + "/api/portal")).status_code == 200
    async with httpx.AsyncClient(trust_env=False, timeout=3) as client:
        with pytest.raises(httpx.TransportError):
            await client.get(origin + "/api/portal")
