"""App factory.

    uvicorn cwjh.main:app                         # real broker (CWJH_MQTT_HOST, default localhost)
    CWJH_MQTT_HOST=sim uvicorn cwjh.main:app      # no broker, no hardware: in-process simulator
"""

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from .api import Broadcaster, router
from .config import Settings
from .db import init_db, make_engine
from .mqtt_link import HubLink
from .seed import seed
from .services.bays import BayService
from .services.cocktail import CocktailService
from .sim.hub_sim import HubSim
from .transport import LocalBroker, PahoTransport, Transport

log = logging.getLogger(__name__)
STATIC_DIR = Path(__file__).parent / "static"


def create_app(settings: Settings | None = None, transport: Transport | None = None) -> FastAPI:
    settings = settings or Settings.from_env()
    sim: HubSim | None = None

    if transport is None:
        if settings.mqtt_host == "sim":
            broker = LocalBroker()
            sim = HubSim(broker.client())
            transport = broker.client()
        else:
            transport = PahoTransport(
                settings.mqtt_host,
                settings.mqtt_port,
                settings.mqtt_username,
                settings.mqtt_password,
                client_id="cwjh-backend",
            )

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        engine = make_engine(settings.db_url)
        sessions = init_db(engine)
        if settings.seed:
            with sessions.begin() as session:
                seed(session)

        hub = HubLink(transport, default_timeout_s=settings.ack_timeout_s)
        broadcaster = Broadcaster()
        cocktail = CocktailService(sessions, hub, settings)
        hub.add_listener(broadcaster.publish)
        cocktail.add_listener(lambda status: broadcaster.publish("cocktail", status))

        app.state.settings = settings
        app.state.sessions = sessions
        app.state.hub = hub
        app.state.bays = BayService(sessions, hub)
        app.state.cocktail = cocktail
        app.state.broadcaster = broadcaster
        app.state.sim = sim

        await hub.start()
        if sim:
            sim.start()
            log.info("running with the in-process hub simulator")
        try:
            yield
        finally:
            await cocktail.abort()
            if sim:
                sim.stop()
            hub.stop()
            engine.dispose()

    app = FastAPI(title="CWAutoJuiceHub", lifespan=lifespan)
    app.include_router(router)
    app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")
    return app


logging.basicConfig(level=logging.INFO)
app = create_app()
