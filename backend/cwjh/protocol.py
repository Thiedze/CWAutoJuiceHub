"""Commands the services give the hub, and what the hub reports back.

The hub (cwjh/hub.py) carries them out with the Nano; see docs/protocol.md for the wire format.
"""

from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, Field, field_validator

BAY_COUNT = 12
MAX_GLASS_ML = 400.0
MAX_TILT_DEG = 30.0
MIN_G_PER_ML = 0.5
MAX_G_PER_ML = 2.0

ErrorCode = Literal["invalid", "busy", "no_motor", "limit", "timeout", "stopped", "offline"]


def new_id() -> str:
    return uuid4().hex[:12]


class Command(BaseModel):
    id: str = Field(default_factory=new_id, min_length=1, max_length=36)


class GlassInit(Command):
    pass


class GlassRelease(Command):
    pass


class Stop(Command):
    pass


class Pour(Command):
    bay: int = Field(ge=1, le=BAY_COUNT)
    ml: float = Field(gt=0, le=MAX_GLASS_ML)
    g_per_ml: float = Field(default=1.0, ge=MIN_G_PER_ML, le=MAX_G_PER_ML)


class Move(Command):
    bay: int = Field(ge=1, le=BAY_COUNT)
    degrees: float

    @field_validator("degrees")
    @classmethod
    def _clamp(cls, value: float) -> float:
        # The wiki says angles above the limit are cut, not rejected.
        return min(max(value, 0.0), MAX_TILT_DEG)


class Ack(BaseModel):
    id: str
    status: Literal["done", "error"]
    code: ErrorCode | None = None
    poured_ml: float | None = None


class Progress(BaseModel):
    id: str
    grams: float
    target: float


class HubState(BaseModel):
    busy: bool = False
    scale_g: float = 0.0
    glass: bool = False
    lift: Literal["up", "down", "moving"] = "down"
    poured_ml: float = 0.0
    bays: list[float] = Field(default_factory=lambda: [0.0] * BAY_COUNT)
    fw: str = ""
