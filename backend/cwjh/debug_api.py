"""REST routes for the motor debug page (static/debug.html): test and calibrate each bay."""

import time
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field, model_validator

from .hub import HubError
from .protocol import BAY_COUNT
from .services.cocktail import Phase
from .services.motors import MAX_SPEED, MotorConfig, profile_for

router = APIRouter(prefix="/api/debug")

MAX_STEPS = 100000
MAX_SNAPSHOT_BYTES = 5_000_000
KEEP_SNAPSHOTS = 30


class MoveRequest(BaseModel):
    """Exactly one of: steps (relative), position (absolute steps), degrees (absolute)."""

    steps: int | None = Field(default=None, ge=-MAX_STEPS, le=MAX_STEPS)
    position: int | None = Field(default=None, ge=-MAX_STEPS, le=MAX_STEPS)
    degrees: float | None = Field(default=None, ge=-360, le=360)
    # Test with this bottle weight instead of the bay's real one.
    load_g: float | None = Field(default=None, ge=0, le=10000)

    @model_validator(mode="after")
    def _one_target(self) -> "MoveRequest":
        if sum(v is not None for v in (self.steps, self.position, self.degrees)) != 1:
            raise ValueError("give exactly one of steps, position, degrees")
        return self


class TestRequest(BaseModel):
    steps: int = Field(ge=1, le=MAX_STEPS)
    repeat: int = Field(default=1, ge=1, le=20)
    load_g: float | None = Field(default=None, ge=0, le=10000)


class HoldRequest(BaseModel):
    on: bool


class RawRequest(BaseModel):
    line: str = Field(min_length=1, max_length=60)


def _state(request: Request):
    return request.app.state


def _check_bay(bay: int) -> None:
    if not 1 <= bay <= BAY_COUNT:
        raise HTTPException(400, f"bay must be 1..{BAY_COUNT}")


def _require_idle(request: Request) -> None:
    if _state(request).cocktail.status.phase is not Phase.IDLE:
        raise HTTPException(409, "a cocktail is running")


def _ask_snapshot(request: Request, label: str, reason: str = "move") -> None:
    """Ask every open debug page with a running camera for a snapshot (WS event "camera")."""
    _state(request).broadcaster.publish("camera", {"label": label, "reason": reason})


async def _call(coro):
    try:
        return await coro
    except HubError as e:
        raise HTTPException(503 if e.code == "offline" else 409, f"hub: {e.code}") from None


def _bay_item(request: Request, bay, live: dict[str, Any] | None) -> dict[str, Any]:
    s = _state(request)
    config = s.motors.config(bay.id)
    load_g = s.motors.load_g(bay.id)
    i = bay.id - 1
    wired = live is not None and i < len(live["positions"])
    position = live["positions"][i] if wired else None
    homed = s.hub.state.homed[i] if s.hub.state else None
    return {
        "bay": bay.id,
        "wired": wired,
        "position": position,
        "degrees": None if position is None else round(position / config.steps_per_degree, 2),
        "switch": live["switches"][i] if wired and i < len(live["switches"]) else None,
        "homed": homed,
        "ingredient": bay.ingredient.name if bay.ingredient else None,
        "volume_ml": bay.volume_ml,
        "load_g": round(load_g, 1),
        "config": config.model_dump(),
        "profile": profile_for(config, load_g).__dict__,
    }


@router.get("/bays")
async def bays(request: Request) -> dict[str, Any]:
    s = _state(request)
    live = None
    if s.hub.online:
        try:
            live = await s.hub.snapshot()
        except HubError:
            live = None
    return {
        "online": s.hub.online,
        "busy": bool(live and live["busy"]),
        "fw": s.hub.state.fw if s.hub.state else None,
        "max_speed": MAX_SPEED,
        "bays": [_bay_item(request, bay, live) for bay in s.bays.list()],
    }


@router.put("/bays/{bay}/config")
async def save_config(request: Request, bay: int, config: MotorConfig) -> dict[str, Any]:
    _check_bay(bay)
    s = _state(request)
    s.motors.save(bay, config)
    s.hub.config_changed(bay)
    return config.model_dump()


@router.post("/bays/{bay}/move")
async def move(request: Request, bay: int, body: MoveRequest) -> dict[str, Any]:
    _check_bay(bay)
    _require_idle(request)
    hub = _state(request).hub
    if body.steps is not None:
        position = await _call(hub.move_by(bay, body.steps, body.load_g))
    else:
        target = body.position
        if body.degrees is not None:
            target = round(body.degrees * _state(request).motors.config(bay).steps_per_degree)
        position = await _call(hub.move_to(bay, target, body.load_g))
    _ask_snapshot(request, f"bay{bay}-at{position}")
    return {"position": position}


@router.post("/bays/{bay}/test")
async def test(request: Request, bay: int, body: TestRequest) -> dict[str, Any]:
    """Out by steps and back, repeat times."""
    _check_bay(bay)
    _require_idle(request)
    hub = _state(request).hub
    position = None
    for _ in range(body.repeat):
        await _call(hub.move_by(bay, body.steps, body.load_g))
        position = await _call(hub.move_by(bay, -body.steps, body.load_g))
    _ask_snapshot(request, f"bay{bay}-test")
    return {"position": position}


@router.post("/bays/{bay}/home")
async def home(request: Request, bay: int) -> dict[str, Any]:
    _check_bay(bay)
    _require_idle(request)
    homed = await _call(_state(request).hub.home(bay))
    _ask_snapshot(request, f"bay{bay}-home")
    return {"homed": homed}


@router.post("/bays/{bay}/zero")
async def zero(request: Request, bay: int) -> dict[str, Any]:
    _check_bay(bay)
    _require_idle(request)
    await _call(_state(request).hub.zero(bay))
    return {"position": 0}


@router.post("/bays/{bay}/hold")
async def hold(request: Request, bay: int, body: HoldRequest) -> dict[str, Any]:
    _check_bay(bay)
    _require_idle(request)
    await _call(_state(request).hub.hold(bay, body.on))
    return {"hold": body.on}


@router.post("/stop")
async def stop(request: Request) -> dict[str, Any]:
    await _call(_state(request).hub.emergency_stop())
    return {"stopped": True}


@router.post("/camera/request")
async def camera_request(request: Request, label: str = "request") -> dict[str, Any]:
    """Ask the open debug page(s) for a snapshot now; it arrives via POST /camera."""
    _ask_snapshot(request, "".join(c for c in label if c.isalnum() or c in "-_")[:40] or "request", "request")
    return {"asked": True}


@router.post("/camera")
async def camera_snapshot(request: Request) -> dict[str, Any]:
    """A JPEG frame from the debug page's camera panel; kept for debugging on this machine."""
    data = await request.body()
    if not data.startswith(b"\xff\xd8") or len(data) > MAX_SNAPSHOT_BYTES:
        raise HTTPException(400, "expected a JPEG of at most 5 MB")
    folder = Path(_state(request).settings.camera_dir)
    folder.mkdir(parents=True, exist_ok=True)
    label = "".join(c for c in request.query_params.get("label", "") if c.isalnum() or c in "-_")[:40]
    name = time.strftime("%Y%m%d-%H%M%S") + f"-{int(time.time() * 1000) % 1000:03d}" + (f"-{label}" if label else "")
    (folder / f"{name}.jpg").write_bytes(data)
    (folder / "latest.jpg").write_bytes(data)
    for old in sorted(folder.glob("2*.jpg"))[:-KEEP_SNAPSHOTS]:
        old.unlink(missing_ok=True)
    return {"saved": f"{name}.jpg"}


@router.post("/raw")
async def raw(request: Request, body: RawRequest) -> dict[str, Any]:
    """One request to the Nano as typed, without the sequence number, e.g. "Q" or "G 1 50"."""
    op = body.line.split()[0].upper()
    if op not in ("Q", "W", "S"):
        _require_idle(request)
    try:
        words = await _state(request).hub.raw(body.line)
        return {"reply": " ".join(words)}
    except HubError as e:
        if e.code == "offline":
            raise HTTPException(503, "hub: offline") from None
        return {"reply": f"ERR {e.code}"}
