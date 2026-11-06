import asyncio
from dataclasses import replace

import pytest

from cwjh.config import Settings
from cwjh.db import init_db, make_engine
from cwjh.hub import Hub
from cwjh.nano_link import NanoLink
from cwjh.seed import seed
from cwjh.services.bays import BayService
from cwjh.services.cocktail import CocktailService
from cwjh.sim.nano_sim import NanoSim

# Simulated motor time runs 100x faster in tests.
TIME_SCALE = 0.01


@pytest.fixture
def sessions():
    engine = make_engine("sqlite://")
    sessions = init_db(engine)
    with sessions.begin() as session:
        seed(session)
    yield sessions
    engine.dispose()


class Rig:
    def __init__(self, sessions, bays: int = 12):
        self.nano = NanoSim(bays=bays, time_scale=TIME_SCALE)
        # Timed pours at 2 l/s, so a 150 ml step takes 75 ms.
        self.settings = Settings(ack_timeout_s=2.0, pour_flow_ml_s=2000.0, invert_direction=False)
        self.link = NanoLink(self.nano.line(), ready_timeout_s=0.3, ping_s=0.1, ping_timeout_s=0.3, retry_s=0.05)
        self.hub = Hub(self.link, self.settings)
        self.sessions = sessions
        self.bays = BayService(sessions, self.hub)
        self.cocktail = CocktailService(sessions, self.hub, self.settings)
        self.events: list[dict] = []
        self.cocktail.add_listener(self.events.append)

    def configure(self, **changes):
        """Change hub settings on the fly, e.g. a slower pour."""
        self.hub._settings = replace(self.hub._settings, **changes)

    async def start(self):
        await self.hub.start()
        await wait_for(lambda: self.hub.online)

    def stop(self):
        self.hub.stop()


@pytest.fixture
async def rig(sessions):
    rig = Rig(sessions)
    await rig.start()
    yield rig
    await rig.cocktail.abort()
    rig.stop()


async def wait_for(predicate, timeout=3.0):
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while not predicate():
        if loop.time() > deadline:
            raise AssertionError("condition not met in time")
        await asyncio.sleep(0.005)
