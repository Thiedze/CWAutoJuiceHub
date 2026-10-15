import asyncio

import pytest

from cwjh.config import Settings
from cwjh.db import init_db, make_engine
from cwjh.mqtt_link import HubLink
from cwjh.protocol import HUB_ONLINE
from cwjh.seed import seed
from cwjh.services.bays import BayService
from cwjh.services.cocktail import CocktailService
from cwjh.sim.hub_sim import HubSim
from cwjh.transport import LocalBroker

# Simulated seconds run 100x faster in tests.
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
    def __init__(self, sessions):
        self.broker = LocalBroker()
        self.sim_transport = self.broker.client(will=(HUB_ONLINE, b"0"))
        self.sim = HubSim(self.sim_transport, flow_ml_s=20.0, time_scale=TIME_SCALE)
        self.settings = Settings(ack_timeout_s=2.0, min_flow_ml_s=50.0)
        self.hub = HubLink(self.broker.client(), default_timeout_s=self.settings.ack_timeout_s)
        self.sessions = sessions
        self.bays = BayService(sessions, self.hub)
        self.cocktail = CocktailService(sessions, self.hub, self.settings)
        self.events: list[dict] = []
        self.cocktail.add_listener(self.events.append)

    async def start(self):
        await self.hub.start()
        self.sim.start()
        await wait_for(lambda: self.hub.online)

    def stop(self):
        self.sim.stop()
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
