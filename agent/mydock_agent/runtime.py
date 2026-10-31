"""Assemblage de l'agent : quels fournisseurs et quelles actions, en vrai ou en démonstration.

Un seul endroit pour dire ce que l'agent publie et ce qu'il accepte de faire : `__main__` s'en
sert pour démarrer, les tests pour vérifier que la liste blanche ne contient que ce qu'on veut.
"""

from __future__ import annotations

from mydock_agent.builtin_actions import register_builtin
from mydock_agent.collectors.audio import AudioProvider, register_audio_actions
from mydock_agent.collectors.desktop import AppsProvider, register_apps_actions
from mydock_agent.collectors.host import HostProvider
from mydock_agent.collectors.music import MusicProvider, register_music_actions
from mydock_agent.collectors.office import OfficeProvider, register_office_actions
from mydock_agent.collectors.power import PowerProvider, register_power_actions
from mydock_agent.collectors.procs import ProcsProvider, register_system_actions
from mydock_agent.collectors.steam import SteamProvider, register_steam_actions
from mydock_agent.collectors.system import SystemProvider
from mydock_agent.demo import DemoHostProvider
from mydock_agent.demo.apps import DemoWorld
from mydock_agent.demo.audio import DemoPactl
from mydock_agent.demo.music import DemoMusic
from mydock_agent.demo.power import HOSTNAME as DEMO_HOSTNAME
from mydock_agent.demo.power import DemoPower
from mydock_agent.demo.system import (
    DemoProcessSource,
    DemoSensors,
    register_demo_system_actions,
)
from mydock_agent.hub import Provider
from mydock_common.actions import ActionRegistry


def build_runtime(*, demo: bool) -> tuple[list[Provider], ActionRegistry]:
    registry = ActionRegistry()
    register_builtin(registry, demo=demo)
    if demo:
        music = DemoMusic()
        source = DemoProcessSource()
        procs = ProcsProvider(source)
        audio = AudioProvider(DemoPactl())
        world = (
            DemoWorld()
        )  # son dossier temporaire vit autant que les fournisseurs (ils en sont le lanceur)
        apps, steam, office = world.apps(), world.steam(), world.office()
        power = PowerProvider(DemoPower())
        providers: list[Provider] = [
            DemoHostProvider(),
            SystemProvider(DemoSensors()),
            procs,
            music,
            audio,
            apps,
            steam,
            office,
            power,
        ]
        register_demo_system_actions(registry, procs, source)
        register_music_actions(registry, music)
        register_audio_actions(registry, audio)
        register_apps_actions(registry, apps)
        register_steam_actions(registry, steam)
        register_office_actions(registry, office)
        register_power_actions(registry, power.backend, hostname=lambda: DEMO_HOSTNAME, demo=True)
    else:
        real_music = MusicProvider()
        real_procs = ProcsProvider()
        real_audio = AudioProvider()
        real_apps, real_steam, real_office = AppsProvider(), SteamProvider(), OfficeProvider()
        real_power = PowerProvider()
        providers = [
            HostProvider(),
            SystemProvider(),
            real_procs,
            real_music,
            real_audio,
            real_apps,
            real_steam,
            real_office,
            real_power,
        ]
        register_system_actions(registry, real_procs)
        register_music_actions(registry, real_music)
        register_audio_actions(registry, real_audio)
        register_apps_actions(registry, real_apps)
        register_steam_actions(registry, real_steam)
        register_office_actions(registry, real_office)
        register_power_actions(registry, real_power.backend)
    return providers, registry
