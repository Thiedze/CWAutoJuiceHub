"""Bay management: the "initialise a bay" flow from the wiki."""

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from ..models import Bay, Ingredient
from ..mqtt_link import HubLink
from ..protocol import BAY_COUNT, MAX_TILT_DEG, Move


class BayError(Exception):
    pass


class BayService:
    def __init__(self, sessions: sessionmaker[Session], hub: HubLink):
        self._sessions = sessions
        self._hub = hub

    def list(self) -> list[Bay]:
        with self._sessions() as session:
            bays = list(session.scalars(select(Bay).order_by(Bay.id)))
            for bay in bays:
                _ = bay.ingredient  # load before the session closes
            return bays

    async def start_fill(self, bay_id: int) -> None:
        """Tilt the bay so a bottle can be inserted."""
        _check_id(bay_id)
        await self._hub.send(Move(bay=bay_id, degrees=MAX_TILT_DEG))

    async def confirm_fill(self, bay_id: int, ingredient_id: int, volume_ml: float) -> Bay:
        """Bottle is in: tilt back and remember what the bay holds."""
        _check_id(bay_id)
        if volume_ml <= 0:
            raise BayError("volume must be positive")
        with self._sessions() as session:
            ingredient = session.get(Ingredient, ingredient_id)
            if ingredient is None:
                raise BayError("unknown ingredient")
            if not ingredient.is_liquid:
                raise BayError("solid ingredients cannot go into a bay")
        await self._hub.send(Move(bay=bay_id, degrees=0))
        with self._sessions.begin() as session:
            bay = session.get(Bay, bay_id)
            bay.ingredient_id = ingredient_id
            bay.volume_ml = volume_ml
        return self._get(bay_id)

    async def cancel_fill(self, bay_id: int) -> None:
        _check_id(bay_id)
        await self._hub.send(Move(bay=bay_id, degrees=0))

    def empty(self, bay_id: int) -> Bay:
        _check_id(bay_id)
        with self._sessions.begin() as session:
            bay = session.get(Bay, bay_id)
            bay.ingredient_id = None
            bay.volume_ml = 0.0
        return self._get(bay_id)

    def _get(self, bay_id: int) -> Bay:
        with self._sessions() as session:
            bay = session.get(Bay, bay_id)
            _ = bay.ingredient
            return bay


def _check_id(bay_id: int) -> None:
    if not 1 <= bay_id <= BAY_COUNT:
        raise BayError(f"bay must be 1..{BAY_COUNT}")
