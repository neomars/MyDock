"""De bout en bout : un vrai dock, son portail, un vrai agent, et `mydock-agent setup` pour de bon.

C'est le parcours de la personne qui installe : le Pi affiche un code, le PC le tape, et le dock se
configure seul. Seul systemd est remplacé (il n'y en a pas ici) ; le reste est réel : fichiers de
configuration en droits 600, HTTP sur le port du portail, poignée de main WebSocket avec le jeton.
"""

import asyncio
import stat
import subprocess

from mydock_agent import firewall, setup
from mydock_agent.config import init_config, load_config
from mydock_agent.setup import PcAddress, Systemd, run_setup
from mydock_agent.testing import running_agent
from mydock_dock.config import DockConfig
from mydock_dock.testing import eventually, running_dock


class FakeSystemd(Systemd):
    def __init__(self):
        self.calls: list[tuple[str, ...]] = []

    def reachable(self) -> bool:
        return True

    def run(self, *args, timeout=30.0):
        self.calls.append(args)
        return subprocess.CompletedProcess(args, 0, "", "")


async def test_setup_on_the_pc_pairs_it_with_the_dock_and_nothing_is_typed_twice(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(setup, "pc_addresses", lambda: [PcAddress("enp3s0", "192.168.1.3", True)])
    monkeypatch.setattr(firewall, "ufw_state", lambda conf=firewall.UFW_CONF: False)
    for name in ("HTTP_PROXY", "http_proxy", "HTTPS_PROXY", "https_proxy", "ALL_PROXY"):
        monkeypatch.delenv(name, raising=False)

    pc_home = tmp_path / "pc"
    agent_config = pc_home / ".config" / "mydock" / "agent.toml"
    # Le PC a déjà son jeton : c'est celui de l'agent qui tourne (créé comme le fait « setup »).
    config, _created = init_config(agent_config, allow=["127.0.0.1"])

    async with (
        running_agent(token=config.token) as agent,
        running_dock(tmp_path / "pi", portal=True) as dock,
    ):
        await eventually(lambda: dock.server.pairing.is_open())  # un dock neuf montre un code
        code = dock.server.pairing.info()["code"]
        assert not dock.config.agent_host

        lines: list[str] = []
        systemd = FakeSystemd()
        status = await asyncio.to_thread(
            lambda: run_setup(
                config_path=agent_config,
                pi=["127.0.0.1"],
                port=agent.port,
                code=code,
                portal_port=dock.server.portal.port,
                systemd=systemd,
                out=lines.append,
                is_root=False,
                wait=lambda _config: True,
                interactive=False,
            )
        )
        text = "\n".join(lines)
        assert status == 0, text
        assert "PC associé au dock" in text
        assert "À saisir à la main" not in text

        # Le dock s'est configuré tout seul, avec l'adresse et le jeton de ce PC.
        assert dock.config.agent_host == "127.0.0.1"
        assert dock.config.agent_port == agent.port
        assert dock.config.token == config.token
        saved = DockConfig.load(dock.config.path)
        assert (saved.agent_host, saved.agent_port, saved.token) == (
            "127.0.0.1",
            agent.port,
            config.token,
        )
        assert stat.S_IMODE(dock.config.path.stat().st_mode) == 0o600
        await eventually(lambda: dock.server.link.connected)
        assert dock.server.pairing.info()["last"]["result"] == "paired"

        # Et le jeton n'a pas été montré : il n'y avait rien à recopier.
        assert config.token not in text
        assert load_config(agent_config).token == config.token  # inchangé
