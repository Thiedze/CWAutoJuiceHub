"""MQTT topics and message models. See docs/protocol.md."""

from typing import ClassVar, Literal
from uuid import uuid4

from pydantic import BaseModel, Field, field_validator

BAY_COUNT = 12
MAX_GLASS_ML = 400.0
MAX_TILT_DEG = 30.0
MIN_G_PER_ML = 0.5
MAX_G_PER_ML = 2.0

CMD_GLASS_INIT = "cwjh/cmd/glass/init"
CMD_GLASS_RELEASE = "cwjh/cmd/glass/release"
CMD_POUR = "cwjh/cmd/pour"
CMD_MOVE = "cwjh/cmd/move"
CMD_STOP = "cwjh/cmd/stop"
CMD_STATE = "cwjh/cmd/state"
CMD_ALL = "cwjh/cmd/#"

HUB_ACK = "cwjh/hub/ack"
HUB_PROGRESS = "cwjh/hub/progress"
HUB_STATE = "cwjh/hub/state"
HUB_ONLINE = "cwjh/hub/online"
HUB_ALL = "cwjh/hub/#"

ErrorCode = Literal["invalid", "busy", "no_glass", "bay_empty", "limit", "timeout", "stopped"]


def new_id() -> str:
    return uuid4().hex[:12]


class Command(BaseModel):
    topic: ClassVar[str]
    id: str = Field(default_factory=new_id, min_length=1, max_length=36)

    def payload(self) -> bytes:
        return self.model_dump_json().encode()


class GlassInit(Command):
    topic: ClassVar[str] = CMD_GLASS_INIT


class GlassRelease(Command):
    topic: ClassVar[str] = CMD_GLASS_RELEASE


class Stop(Command):
    topic: ClassVar[str] = CMD_STOP


class RequestState(Command):
    topic: ClassVar[str] = CMD_STATE


class Pour(Command):
    topic: ClassVar[str] = CMD_POUR
    bay: int = Field(ge=1, le=BAY_COUNT)
    ml: float = Field(gt=0, le=MAX_GLASS_ML)
    g_per_ml: float = Field(default=1.0, ge=MIN_G_PER_ML, le=MAX_G_PER_ML)


class Move(Command):
    topic: ClassVar[str] = CMD_MOVE
    bay: int = Field(ge=1, le=BAY_COUNT)
    degrees: float

    @field_validator("degrees")
    @classmethod
    def _clamp(cls, value: float) -> float:
        # The wiki says angles above the limit are cut, not rejected.
        return min(max(value, 0.0), MAX_TILT_DEG)


COMMANDS: dict[str, type[Command]] = {
    cls.topic: cls for cls in (GlassInit, GlassRelease, Stop, RequestState, Pour, Move)
}


class Ack(BaseModel):
    id: str
    status: Literal["accepted", "done", "error"]
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
