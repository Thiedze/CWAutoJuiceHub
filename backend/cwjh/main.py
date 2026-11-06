"""App factory.

    uvicorn cwjh.main:app                          # Nano on the first USB serial port
    CWJH_SERIAL_PORT=sim uvicorn cwjh.main:app     # no hardware: in-process simulated Nano
"""

import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from .api import Broadcaster, router
from .config import Settings
from .db import init_db, make_engine
from .hub import Hub
from .nano_link import Line, NanoLink, SerialLine
from .seed import seed
from .serial_log import SerialLog
from .services.bays import BayService
from .services.cocktail import CocktailService
from .sim.nano_sim import NanoSim

log = logging.getLogger(__name__)
STATIC_DIR = Path(__file__).parent / "static"


def create_app(settings: Settings | None = None, line: Line | None = None) -> FastAPI:
    settings = settings or Settings.from_env()
    sim: NanoSim | None = None

    if line is None:
        if settings.serial_port == "sim":
            sim = NanoSim(bays=settings.sim_bays, steps_per_s=settings.step_rate)
            line = sim.line()
        else:
            line = SerialLine(settings.serial_port)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        engine = make_engine(settings.db_url)
        sessions = init_db(engine)
        if settings.seed:
            with sessions.begin() as session:
                seed(session)

        broadcaster = Broadcaster()
        serial_log = SerialLog(asyncio.get_running_loop(), lambda entry: broadcaster.publish("serial", entry))
        hub = Hub(NanoLink(line, tap=serial_log.record), settings)
        cocktail = CocktailService(sessions, hub, settings)
        hub.add_listener(broadcaster.publish)
        cocktail.add_listener(lambda status: broadcaster.publish("cocktail", status))

        app.state.settings = settings
        app.state.sessions = sessions
        app.state.hub = hub
        app.state.bays = BayService(sessions, hub)
        app.state.cocktail = cocktail
        app.state.broadcaster = broadcaster
        app.state.serial_log = serial_log
        app.state.sim = sim

        if sim:
            log.info("running with the in-process simulated Nano")
        await hub.start()
        try:
            yield
        finally:
            await cocktail.abort()
            hub.stop()
            engine.dispose()

    app = FastAPI(title="CWAutoJuiceHub", lifespan=lifespan)
    app.include_router(router)
    app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")
    return app


logging.basicConfig(level=logging.INFO)
app = create_app()
