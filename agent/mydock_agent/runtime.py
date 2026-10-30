"""Assemblage de l'agent : quels fournisseurs et quelles actions, en vrai ou en démonstration.

Un seul endroit pour dire ce que l'agent publie et ce qu'il accepte de faire : `__main__` s'en
sert pour démarrer, les tests pour vérifier que la liste blanche ne contient que ce qu'on veut.
"""

from __future__ import annotations

from mydock_agent.builtin_actions import register_builtin
from mydock_agent.collectors.host import HostProvider
from mydock_agent.collectors.music import MusicProvider, register_music_actions
from mydock_agent.collectors.procs import ProcsProvider, register_system_actions
from mydock_agent.collectors.system import SystemProvider
from mydock_agent.demo import DemoHostProvider
from mydock_agent.demo.music import DemoMusic
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
        providers: list[Provider] = [
            DemoHostProvider(),
            SystemProvider(DemoSensors()),
            procs,
            music,
        ]
        register_demo_system_actions(registry, procs, source)
        register_music_actions(registry, music)
    else:
        real_music = MusicProvider()
        real_procs = ProcsProvider()
        providers = [HostProvider(), SystemProvider(), real_procs, real_music]
        register_system_actions(registry, real_procs)
        register_music_actions(registry, real_music)
    return providers, registry
