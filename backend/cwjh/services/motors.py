"""Per bay motor calibration and the motion profile of each move. See docs/hardware.md.

A profile is an S-curve: the motor starts at min_speed, speeds up to its top speed along
half a cosine that takes start_ms, and slows down again along one that takes end_ms. Each
bay has one set of values for an empty bottle and one for a full one; a move uses a blend
by the bottle's weight. The weight is estimated from the bay's volume and the liquid's
density until the load cell exists.
"""

import json
from dataclasses import dataclass

import logging

from pydantic import BaseModel, Field, ValidationError, model_validator
from sqlalchemy.orm import Session, sessionmaker

from ..config import Settings
from ..models import Bay, BayMotor

log = logging.getLogger(__name__)

MAX_SPEED = 5000  # steps/s, the Nano's limit; the SX1509 manages about 320
MAX_CURVE_MS = 10000


class MotorConfig(BaseModel):
    # Flip the motor's direction so that positive steps tilt the bottle for filling.
    invert: bool
    steps_per_degree: float = Field(gt=0, le=1000)
    # How far a pour turns the bay from rest (opposite to the tilt), and the timed flow.
    pour_open_steps: int = Field(ge=-100000, le=100000)
    pour_flow_ml_s: float = Field(gt=0, le=10000)
    # After homing, go this far onto the home switch and call that 0, so the switch is
    # firmly pressed at rest and not just at its edge.
    home_offset_steps: int = Field(default=5, ge=0, le=200)
    # Speed at the start and end of every move, in steps/s.
    min_speed: int = Field(ge=1, le=MAX_SPEED)
    # Top speed and S-curve lengths with an empty and with a full bottle.
    speed_empty: int = Field(ge=1, le=MAX_SPEED)
    speed_full: int = Field(ge=1, le=MAX_SPEED)
    start_ms_empty: int = Field(ge=0, le=MAX_CURVE_MS)
    start_ms_full: int = Field(ge=0, le=MAX_CURVE_MS)
    end_ms_empty: int = Field(ge=0, le=MAX_CURVE_MS)
    end_ms_full: int = Field(ge=0, le=MAX_CURVE_MS)
    # Bottle weight (liquid only) at which the "full" values apply.
    full_load_g: float = Field(gt=0, le=10000)

    @model_validator(mode="after")
    def _min_below_top(self) -> "MotorConfig":
        if self.min_speed > min(self.speed_empty, self.speed_full):
            raise ValueError("min_speed must not be above the top speeds")
        return self

    @classmethod
    def defaults(cls, settings: Settings) -> "MotorConfig":
        return cls(
            invert=settings.invert_direction,
            steps_per_degree=settings.steps_per_degree,
            pour_open_steps=settings.pour_open_steps,
            pour_flow_ml_s=settings.pour_flow_ml_s,
            min_speed=min(50, settings.step_rate),
            speed_empty=settings.step_rate,
            speed_full=max(min(50, settings.step_rate), settings.step_rate // 2),
            start_ms_empty=200,
            start_ms_full=600,
            end_ms_empty=200,
            end_ms_full=600,
            full_load_g=1000.0,
        )


@dataclass(frozen=True)
class Profile:
    max_speed: int
    min_speed: int
    start_ms: int
    end_ms: int

    def request(self, bay: int) -> str:
        """The Nano's P request for this profile."""
        return f"P {bay} {self.max_speed} {self.min_speed} {self.start_ms} {self.end_ms}"


def profile_for(config: MotorConfig, load_g: float) -> Profile:
    """Blend the empty and full values by the bottle weight."""
    f = min(max(load_g / config.full_load_g, 0.0), 1.0)

    def blend(empty: float, full: float) -> int:
        return round(empty + (full - empty) * f)

    return Profile(
        max_speed=max(blend(config.speed_empty, config.speed_full), config.min_speed),
        min_speed=config.min_speed,
        start_ms=blend(config.start_ms_empty, config.start_ms_full),
        end_ms=blend(config.end_ms_empty, config.end_ms_full),
    )


class DefaultMotors:
    """Every bay with the settings' defaults and an empty bottle; for tests and tools."""

    def __init__(self, settings: Settings):
        self._config = MotorConfig.defaults(settings)

    def config(self, bay: int) -> MotorConfig:
        return self._config

    def load_g(self, bay: int) -> float:
        return 0.0

    def profile(self, bay: int, load_g: float | None = None) -> Profile:
        return profile_for(self._config, self.load_g(bay) if load_g is None else load_g)


class MotorService(DefaultMotors):
    """Calibration stored per bay in the database."""

    def __init__(self, sessions: sessionmaker[Session], settings: Settings):
        super().__init__(settings)
        self._sessions = sessions
        self._defaults = MotorConfig.defaults(settings)

    def config(self, bay: int) -> MotorConfig:
        with self._sessions() as session:
            row = session.get(BayMotor, bay)
            if row is None:
                return self._defaults
            data = {**self._defaults.model_dump(), **_loads(row.config_json)}
        try:
            return MotorConfig.model_validate(data)
        except ValidationError as e:
            log.error("bay %d: stored motor config is invalid, using defaults: %s", bay, e)
            return self._defaults

    def save(self, bay: int, config: MotorConfig) -> MotorConfig:
        with self._sessions.begin() as session:
            row = session.get(BayMotor, bay)
            if row is None:
                session.add(BayMotor(bay_id=bay, config_json=config.model_dump_json()))
            else:
                row.config_json = config.model_dump_json()
        return config

    def load_g(self, bay: int) -> float:
        """Liquid in the bay's bottle in grams. Later this comes from the load cell."""
        with self._sessions() as session:
            row = session.get(Bay, bay)
            if row is None or row.ingredient is None:
                return 0.0
            return row.volume_ml * row.ingredient.g_per_ml


def _loads(text: str) -> dict:
    try:
        data = json.loads(text)
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}
