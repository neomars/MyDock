"""Mode démo : de fausses données pour développer l'interface sans le PC.

Le mode démo parle exactement le même protocole que le vrai agent. Toutes les valeurs sont
inventées (adresses MAC « locales » en 02:…, adresses de documentation RFC 5737).
"""

from __future__ import annotations

import time
from typing import Any

from mydock_agent.collectors.host import HostProvider

DEMO_TOKEN = "demo-token-pas-un-secret-0123456789"  # noqa: secret - jeton de démonstration public


def demo_interfaces() -> list[dict[str, Any]]:
    return [
        {
            "name": "enp3s0",
            "mac": "02:00:5e:10:00:01",
            "kind": "ethernet",
            "up": True,
            "ipv4": ["192.0.2.20"],
            "default": True,
        },
        {
            "name": "wlp2s0",
            "mac": "02:00:5e:10:00:02",
            "kind": "wifi",
            "up": False,
            "ipv4": [],
            "default": False,
        },
    ]


class DemoHostProvider(HostProvider):
    def __init__(self) -> None:
        super().__init__(collect_interfaces=demo_interfaces, demo=True)
        self._started = int(time.time()) - 3 * 3600 - 17 * 60

    def collect(self) -> dict[str, Any]:
        data = super().collect()
        data.update(
            hostname="pc-demo",
            os="Ubuntu 26.04 LTS (démo)",
            kernel="7.0.0-demo",
            arch="x86_64",
            python="3.14.0",
            boot_time=self._started,
            session={"type": "wayland", "desktop": "GNOME"},
        )
        return data
