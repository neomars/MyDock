from __future__ import annotations

from collections.abc import AsyncIterator

import pytest

from mydock_agent.testing import RunningAgent, running_agent


@pytest.fixture
async def agent() -> AsyncIterator[RunningAgent]:
    async with running_agent() as running:
        yield running
