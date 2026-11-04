"""L'agent s'associe au dock : le client HTTP face à un faux portail, et `setup` qui s'en sert."""

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

from mydock_agent import __main__ as cli
from mydock_agent import firewall, setup
from mydock_agent.config import load_config
from mydock_agent.pairing import (
    Paired,
    PairingError,
    fetch_state,
    pair_with_dock,
    portal_url,
)
from mydock_agent.setup import PcAddress, Systemd, run_setup
from mydock_common.auth import is_valid_token_format

# --- un faux portail ---------------------------------------------------------------------------------


class FakePortal:
    """Un petit serveur HTTP qui répond ce qu'on lui dit et note ce qu'on lui envoie."""

    def __init__(self):
        self.state: object = {
            "app": "mydock",
            "version": "0.7.0",
            "hostname": "raspberrypi",
            "pairing": True,
        }
        self.reply: tuple[int, object] = (
            200,
            {
                "ok": True,
                "dock": {"version": "0.7.0", "hostname": "raspberrypi"},
                "pi_ips": ["192.168.1.60"],
            },
        )
        self.requests: list[dict] = []
        portal = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def _send(self, status, body):
                raw = body if isinstance(body, bytes) else json.dumps(body).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            def do_GET(self):
                portal.requests.append({"method": "GET", "path": self.path})
                self._send(200, portal.state)

            def do_POST(self):
                length = int(self.headers.get("Content-Length", 0))
                body = self.rfile.read(length)
                portal.requests.append(
                    {
                        "method": "POST",
                        "path": self.path,
                        "type": self.headers.get("Content-Type"),
                        "agent": self.headers.get("User-Agent"),
                        "body": json.loads(body),
                    }
                )
                self._send(*portal.reply)

        self.server = HTTPServer(("127.0.0.1", 0), Handler)
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=3)


@pytest.fixture
def portal(monkeypatch):
    # Un proxy de l'environnement ne doit jamais s'interposer entre le PC et le Pi.
    for name in ("HTTP_PROXY", "http_proxy", "HTTPS_PROXY", "https_proxy", "ALL_PROXY"):
        monkeypatch.setenv(name, "http://127.0.0.1:9")
    monkeypatch.delenv("NO_PROXY", raising=False)
    fake = FakePortal()
    yield fake
    fake.close()


TOKEN = "t" * 43


def pair(portal, **kw):
    return pair_with_dock(
        "127.0.0.1",
        portal.port,
        code="K7QM-2XPD",
        token=TOKEN,
        agent_port=8765,
        name="portable",
        **kw,
    )


# --- le client -------------------------------------------------------------------------------------------


def test_portal_addresses_are_written_without_the_default_port_and_with_ipv6_brackets():
    assert portal_url("192.168.1.50", 80) == "http://192.168.1.50/"
    assert portal_url("192.168.1.50", 8081, "/api/pair") == "http://192.168.1.50:8081/api/pair"
    assert portal_url("fe80::1", 80, "/x") == "http://[fe80::1]/x"


def test_the_state_of_a_mydock_portal_is_read(portal):
    state = fetch_state("127.0.0.1", portal.port)
    assert state["hostname"] == "raspberrypi"
    assert portal.requests == [{"method": "GET", "path": "/api/portal"}]


def test_something_else_that_answers_is_not_a_portal(portal):
    portal.state = {"app": "autre chose"}
    assert fetch_state("127.0.0.1", portal.port) is None
    portal.state = ["pas", "un", "objet"]
    assert fetch_state("127.0.0.1", portal.port) is None
    portal.state = b"pas du json"
    assert fetch_state("127.0.0.1", portal.port) is None


def test_nothing_listening_is_not_a_portal():
    assert fetch_state("127.0.0.1", 1, timeout=0.5) is None


def test_the_pairing_request_carries_the_code_the_token_the_port_and_the_name(portal):
    result = pair(portal)
    assert result == Paired(
        pi_ips=["192.168.1.60"], dock_hostname="raspberrypi", dock_version="0.7.0"
    )
    [request] = portal.requests
    assert request["method"] == "POST"
    assert request["path"] == "/api/pair"
    assert request["type"] == "application/json"
    assert request["body"] == {
        "code": "K7QM-2XPD",
        "token": TOKEN,
        "port": 8765,
        "name": "portable",
    }


@pytest.mark.parametrize(
    ("status", "body", "code", "message", "ips"),
    [
        (
            403,
            {"ok": False, "error": "bad_code", "message": "Code incorrect."},
            "bad_code",
            "Code incorrect.",
            [],
        ),
        (
            409,
            {"ok": False, "error": "not_open", "message": "Aucune association en cours."},
            "not_open",
            "Aucune association en cours.",
            [],
        ),
        (
            502,
            {
                "ok": False,
                "error": "unreachable",
                "message": "Pas joint.",
                "pi_ips": ["192.168.1.61"],
            },
            "unreachable",
            "Pas joint.",
            ["192.168.1.61"],
        ),
        (500, b"<html>erreur</html>", "http_500", "Le dock a répondu 500.", []),
        (
            429,
            {
                "ok": False,
                "error": "too_many",
                "message": "Trop de demandes.",
                "pi_ips": "pas une liste",
            },
            "too_many",
            "Trop de demandes.",
            [],
        ),
    ],
)
def test_a_refusal_keeps_what_the_dock_said(portal, status, body, code, message, ips):
    portal.reply = (status, body)
    with pytest.raises(PairingError) as info:
        pair(portal)
    assert (info.value.code, info.value.message, info.value.pi_ips) == (code, message, ips)


def test_an_unreachable_portal_is_a_clear_error():
    with pytest.raises(PairingError) as info:
        pair_with_dock(
            "127.0.0.1", 1, code="K7QM-2XPD", token=TOKEN, agent_port=8765, name="x", timeout=1
        )
    assert info.value.code == "portal_unreachable"
    assert "ne répond pas" in info.value.message
    assert "http://127.0.0.1:1/" in info.value.message


def test_an_answer_that_is_not_ok_is_not_a_pairing(portal):
    portal.reply = (200, {"ok": False})
    with pytest.raises(PairingError) as info:
        pair(portal)
    assert info.value.code == "bad_answer"


# --- setup : l'étape d'association -------------------------------------------------------------------------


class FakeSystemd(Systemd):
    def __init__(self):
        self.calls: list[tuple[str, ...]] = []

    def reachable(self) -> bool:
        return True

    def run(self, *args, timeout=30.0):
        import subprocess

        self.calls.append(args)
        return subprocess.CompletedProcess(args, 0, "", "")


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setattr(setup, "pc_addresses", lambda: [PcAddress("enp3s0", "192.168.1.3", True)])
    monkeypatch.setattr(firewall, "ufw_state", lambda conf=firewall.UFW_CONF: False)
    return tmp_path


class Dock:
    """Un faux dock pour `setup` : un portail qui répond, et la liste de ce qu'on lui a présenté."""

    def __init__(self, *answers, state="open"):
        self.answers = list(answers)  # chaque élément : un Paired ou une PairingError à lever
        self.calls: list[dict] = []
        self.state = (
            {
                "app": "mydock",
                "version": "0.7.0",
                "hostname": "raspberrypi",
                "pairing": state == "open",
            }
            if state
            else None
        )

    def fetch(self, host, port, **kw):
        self.calls.append({"fetch": (host, port)})
        return self.state

    def pair(self, host, port, **kw):
        self.calls.append({"pair": (host, port), **kw})
        answer = self.answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer


def run(home: Path, dock: Dock, *, asks=(), **kw):
    lines: list[str] = []
    prompts: list[str] = []
    answers = iter(asks)

    def ask(prompt):
        prompts.append(prompt)
        return next(answers)

    kw.setdefault("systemd", FakeSystemd())
    kw.setdefault("wait", lambda _config: True)
    kw.setdefault("pi", ["192.168.1.60"])
    kw.setdefault("interactive", bool(asks))
    code = run_setup(
        config_path=home / ".config" / "mydock" / "agent.toml",
        out=lines.append,
        is_root=False,
        fetch=dock.fetch,
        pair_with=dock.pair,
        ask=ask,
        **kw,
    )
    return code, "\n".join(lines), prompts


def config_of(home: Path):
    return load_config(home / ".config" / "mydock" / "agent.toml")


PAIRED = Paired(pi_ips=["192.168.1.60"], dock_hostname="raspberrypi", dock_version="0.7.0")


def test_with_the_code_the_pc_is_paired_and_nothing_is_left_to_type(home):
    dock = Dock(PAIRED)
    code, text, _ = run(home, dock, code="K7QM-2XPD")
    assert code == 0
    [_, sent] = dock.calls
    assert sent["pair"] == ("192.168.1.60", 80)
    assert sent["code"] == "K7QM-2XPD"
    assert sent["token"] == config_of(home).token  # le jeton que cet agent a généré
    assert is_valid_token_format(sent["token"])
    assert sent["agent_port"] == 8765
    assert sent["name"]
    assert "✔ Dock trouvé : raspberrypi" in text
    assert "PC associé au dock « raspberrypi »" in text
    assert "À saisir à la main" not in text
    assert config_of(home).token not in text  # plus de jeton à recopier : il ne s'affiche même plus


def test_without_a_code_the_user_is_asked_and_the_code_goes_through_as_typed(home):
    dock = Dock(PAIRED)
    code, text, prompts = run(home, dock, asks=["  k7qm-2xpd "])
    assert code == 0
    assert len(prompts) == 1
    assert "Code affiché sur l'écran du dock" in prompts[0]
    assert dock.calls[1]["code"] == "k7qm-2xpd"  # le dock normalise : l'agent ne touche à rien
    assert "PC associé" in text


def test_a_wrong_code_is_asked_again_up_to_three_times_then_the_manual_way_is_shown(home):
    wrong = PairingError("bad_code", "Code incorrect.")
    dock = Dock(wrong, wrong, wrong)
    code, text, prompts = run(home, dock, asks=["AAAA-AAAA", "BBBB-BBBB", "CCCC-CCCC"])
    assert code == 0  # l'agent est installé et marche : seule l'association manque
    assert len(prompts) == 3
    assert text.count("✘ Code incorrect.") == 3
    assert "À saisir à la main sur le dock" in text
    assert config_of(home).token in text  # repli : le jeton s'affiche, une seule fois


def test_a_wrong_code_then_the_right_one_pairs(home):
    dock = Dock(PairingError("bad_code", "Code incorrect."), PAIRED)
    _, text, prompts = run(home, dock, asks=["AAAA-AAAA", "K7QM-2XPD"])
    assert len(prompts) == 2
    assert "✘ Code incorrect." in text
    assert "PC associé" in text


def test_an_empty_answer_skips_the_pairing_and_says_how_to_come_back(home):
    dock = Dock()
    _, text, _ = run(home, dock, asks=[""])
    assert "Association ignorée" in text
    assert "--code" in text
    assert all("pair" not in call for call in dock.calls)
    assert "À saisir à la main" in text


def test_without_a_terminal_and_without_a_code_the_command_to_rerun_is_given(home):
    dock = Dock()
    _, text, _ = run(home, dock, interactive=False)
    assert "mydock-agent setup --pi 192.168.1.60 --code CODE" in text
    assert all("pair" not in call for call in dock.calls)


def test_a_silent_portal_is_reported_with_its_address_and_the_manual_way_remains(home):
    dock = Dock(state=None)
    code, text, _ = run(home, dock, code="K7QM-2XPD", portal_port=8081)
    assert code == 0
    assert "Le portail du dock ne répond pas (http://192.168.1.60:8081/)" in text
    assert "--portal-port" in text
    assert "À saisir à la main" in text
    assert dock.calls == [{"fetch": ("192.168.1.60", 8081)}]


def test_a_dock_without_a_code_on_screen_tells_where_to_ask_for_one(home):
    dock = Dock(PAIRED, state="closed")
    _, text, _ = run(home, dock, code="K7QM-2XPD")
    assert "Aucun code n'est affiché sur le dock" in text
    assert "Associer un PC" in text
    assert "PC associé" in text  # on essaie quand même : le code a pu être ouvert entre-temps


@pytest.mark.parametrize("error", ["not_open", "locked", "too_many", "bad_request", "save_failed"])
def test_a_refusal_other_than_a_wrong_code_stops_there_with_the_docks_own_words(home, error):
    dock = Dock(PairingError(error, f"Message du dock pour {error}."))
    code, text, _ = run(home, dock, code="K7QM-2XPD")
    assert code == 0
    assert f"✘ Message du dock pour {error}." in text
    assert len([c for c in dock.calls if "pair" in c]) == 1  # pas de nouvel essai


def test_a_wrong_code_given_on_the_command_line_is_not_retried_in_a_loop(home):
    dock = Dock(PairingError("bad_code", "Code incorrect."))
    _, text, prompts = run(home, dock, code="AAAA-AAAA")
    assert prompts == []
    assert "✘ Code incorrect." in text
    assert len([c for c in dock.calls if "pair" in c]) == 1


# --- l'adresse sous laquelle le Pi se présente ----------------------------------------------------------------


def test_when_the_pi_reaches_us_from_another_address_it_is_authorized_and_the_agent_restarted(home):
    unreachable = PairingError("refused", "Le PC a refusé.", pi_ips=["192.168.1.61"])
    dock = Dock(unreachable, Paired(pi_ips=["192.168.1.61"], dock_hostname="raspberrypi"))
    systemd = FakeSystemd()
    code, text, _ = run(home, dock, code="K7QM-2XPD", systemd=systemd)
    assert code == 0
    assert [str(n) for n in config_of(home).allowed_clients] == [
        "192.168.1.60/32",
        "192.168.1.61/32",
    ]
    assert "192.168.1.61" in text
    assert (
        systemd.calls.count(("restart", "mydock-agent.service")) == 2
    )  # le démarrage, puis la correction
    assert len([c for c in dock.calls if "pair" in c]) == 2
    assert "PC associé" in text


def test_an_address_the_dock_announces_after_success_is_added_too(home):
    dock = Dock(Paired(pi_ips=["192.168.1.60", "10.0.0.7"], dock_hostname="raspberrypi"))
    run(home, dock, code="K7QM-2XPD")
    assert [str(n) for n in config_of(home).allowed_clients] == ["192.168.1.60/32", "10.0.0.7/32"]


def test_only_private_valid_addresses_are_taken_from_the_dock_and_no_more_than_three(home):
    announced = [
        "8.8.8.8",
        "pas une adresse",
        "0.0.0.0",
        "::1",
        "10.0.0.1",
        "10.0.0.2",
        "10.0.0.3",
        "10.0.0.4",
    ]
    dock = Dock(Paired(pi_ips=announced, dock_hostname="raspberrypi"))
    run(home, dock, code="K7QM-2XPD")
    allowed = [str(n) for n in config_of(home).allowed_clients]
    assert allowed == ["192.168.1.60/32", "10.0.0.1/32", "10.0.0.2/32", "10.0.0.3/32"]


def test_a_second_refusal_after_the_correction_shows_the_firewall_hint(home):
    first = PairingError("unreachable", "Pas joint.", pi_ips=["192.168.1.61"])
    second = PairingError("unreachable", "Toujours pas joint.", pi_ips=["192.168.1.61"])
    dock = Dock(first, second)
    code, text, _ = run(home, dock, code="K7QM-2XPD")
    assert code == 0
    assert "✘ Toujours pas joint." in text
    assert "sudo ufw allow from 192.168.1.61 to any port 8765 proto tcp" in text
    assert "À saisir à la main" in text


def test_an_unreachable_agent_with_an_address_already_allowed_goes_straight_to_the_hint(home):
    dock = Dock(PairingError("unreachable", "Pas joint.", pi_ips=["192.168.1.60"]))
    _, text, _ = run(home, dock, code="K7QM-2XPD")
    assert len([c for c in dock.calls if "pair" in c]) == 1  # rien à corriger : on ne boucle pas
    assert "sudo ufw allow from 192.168.1.60" in text


# --- quand on n'associe pas -----------------------------------------------------------------------------------


def test_no_pairing_is_attempted_without_a_pi_address(home):
    dock = Dock()
    _, text, _ = run(home, dock, pi=[], code="K7QM-2XPD")
    assert dock.calls == []
    assert "Aucun Pi donné" in text


def test_a_network_instead_of_an_address_cannot_be_paired(home):
    dock = Dock()
    _, text, _ = run(home, dock, pi=["192.168.1.0/24"], code="K7QM-2XPD")
    assert dock.calls == []
    assert "donne l'adresse du Pi" in text


def test_no_pair_leaves_the_dock_alone(home):
    dock = Dock()
    _, text, _ = run(home, dock, code="K7QM-2XPD", pair=False)
    assert dock.calls == []
    assert "À saisir à la main" in text


def test_the_pairing_waits_for_the_agent_to_run(home):
    dock = Dock()
    _, text, _ = run(home, dock, code="K7QM-2XPD", start_service=False)
    assert dock.calls == []
    assert "Association au dock ignorée" in text
    failing, _text2, _ = run(home, dock, code="K7QM-2XPD", wait=lambda _c: False)
    assert failing == 1
    assert dock.calls == []


# --- la ligne de commande ---------------------------------------------------------------------------------------


def test_the_command_line_passes_the_code_the_portal_port_and_no_pair(monkeypatch):
    seen = {}
    monkeypatch.setattr(cli, "run_setup", lambda **kw: seen.update(kw) or 0)
    assert (
        cli.main(["setup", "--pi", "192.168.1.60", "--code", "K7QM-2XPD", "--portal-port", "8081"])
        == 0
    )
    assert (seen["code"], seen["portal_port"], seen["pair"]) == ("K7QM-2XPD", 8081, True)
    assert cli.main(["setup", "--pi", "192.168.1.60", "--no-pair"]) == 0
    assert (seen["code"], seen["portal_port"], seen["pair"]) == (None, 80, False)


# --- « mydock-agent pair » : l'association seule ---------------------------------------------------------------------


def pair_cmd(home: Path, dock: Dock, *, asks=(), configure=True, **kw):
    """Lance `pair` ; par défaut sur un PC déjà configuré (comme après l'installation sans paquet)."""
    if configure:
        run_setup(
            config_path=home / ".config" / "mydock" / "agent.toml",
            pi=["192.168.1.60"],
            systemd=FakeSystemd(),
            wait=lambda _c: True,
            pair=False,
            is_root=False,
            out=lambda _line: None,
        )
    lines: list[str] = []
    answers = iter(asks)
    kw.setdefault("systemd", FakeSystemd())
    kw.setdefault("wait", lambda _config: True)
    kw.setdefault("interactive", bool(asks))
    kw.setdefault("pi", "192.168.1.60")
    code = setup.run_pair(
        config_path=home / ".config" / "mydock" / "agent.toml",
        out=lines.append,
        fetch=dock.fetch,
        pair_with=dock.pair,
        ask=lambda _prompt: next(answers),
        **kw,
    )
    return code, "\n".join(lines)


def test_pair_associates_a_configured_pc_without_touching_the_service(home):
    systemd = FakeSystemd()
    dock = Dock(PAIRED)
    code, text = pair_cmd(home, dock, code="K7QM-2XPD", systemd=systemd)
    assert code == 0
    [_, sent] = dock.calls
    assert sent["token"] == config_of(home).token
    assert sent["pair"] == ("192.168.1.60", 80)
    assert "PC associé au dock « raspberrypi »" in text
    assert systemd.calls == []  # ni démarrage, ni redémarrage : seulement l'association


def test_pair_asks_for_the_code_when_none_is_given(home):
    dock = Dock(PAIRED)
    code, _ = pair_cmd(home, dock, asks=["k7qm 2xpd"])
    assert code == 0
    assert dock.calls[1]["code"] == "k7qm 2xpd"


def test_pair_without_a_terminal_or_a_code_says_how_to_come_back(home):
    dock = Dock()
    code, text = pair_cmd(home, dock)
    assert code == 1
    assert "--code CODE" in text
    assert [c for c in dock.calls if "pair" in c] == []


def test_pair_on_an_unconfigured_pc_sends_to_setup_first(home):
    dock = Dock()
    code, text = pair_cmd(home, dock, configure=False)
    assert code == 2
    assert "mydock-agent setup" in text
    assert dock.calls == []


def test_pair_needs_the_agent_to_run(home):
    dock = Dock()
    code, text = pair_cmd(home, dock, code="K7QM-2XPD", wait=lambda _c: False)
    assert code == 1
    assert "L'agent ne répond pas" in text
    assert dock.calls == []


def test_pair_refuses_a_network_and_an_unresolvable_name(home, monkeypatch):
    dock = Dock()
    code, text = pair_cmd(home, dock, code="K7QM-2XPD", pi="192.168.1.0/24")
    assert code == 2
    assert "pas un réseau" in text

    def refuse(_name, *_a, **_k):
        raise OSError("nope")

    monkeypatch.setattr(setup.socket, "getaddrinfo", refuse)
    code, text = pair_cmd(home, dock, code="K7QM-2XPD", pi="raspberrypi.local")
    assert code == 2
    assert "Impossible de résoudre" in text
    assert dock.calls == []


def test_pair_reports_a_failed_association_with_a_non_zero_status(home):
    dock = Dock(PairingError("bad_code", "Code incorrect."))
    code, text = pair_cmd(home, dock, code="AAAA-AAAA")
    assert code == 1
    assert "Code incorrect" in text


def test_the_command_line_pair_passes_its_options(monkeypatch):
    seen = {}
    monkeypatch.setattr(cli, "run_pair", lambda **kw: seen.update(kw) or 0)
    assert cli.main(["pair", "--pi", "192.168.1.60", "--code", "K7QM-2XPD"]) == 0
    assert seen["pi"] == "192.168.1.60"
    assert (seen["code"], seen["portal_port"]) == ("K7QM-2XPD", 80)
    assert cli.main(["pair", "--pi", "192.168.1.60", "--portal-port", "8081"]) == 0
    assert (seen["code"], seen["portal_port"]) == (None, 8081)
