"""REST routes and the live WebSocket."""

import asyncio
import json
import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Request, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, Field
from sqlalchemy import select

from .models import Bay, Ingredient
from .mqtt_link import HubError
from .services.bays import BayError
from .services.cocktail import MAX_STRENGTH, MIN_STRENGTH, CocktailError, Plan

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api")


class FillRequest(BaseModel):
    ingredient_id: int
    volume_ml: float = Field(gt=0, le=5000)


class StartRequest(BaseModel):
    recipe_id: int
    strength: float = Field(default=1.0, ge=MIN_STRENGTH, le=MAX_STRENGTH)


def _services(request: Request):
    return request.app.state


def _bay_dict(bay: Bay) -> dict[str, Any]:
    return {
        "id": bay.id,
        "filled": bay.filled,
        "volume_ml": bay.volume_ml,
        "ingredient": {"id": bay.ingredient.id, "name": bay.ingredient.name} if bay.ingredient else None,
    }


def _plan_dict(plan: Plan) -> dict[str, Any]:
    return {
        "recipe": plan.recipe,
        "strength": plan.strength,
        "total_ml": plan.total_ml,
        "steps": [s.__dict__ for s in plan.steps],
    }


async def _hub_call(coro):
    try:
        return await coro
    except HubError as e:
        raise HTTPException(503 if e.code == "offline" else 409, f"hub: {e.code}") from None
    except BayError as e:
        raise HTTPException(400, str(e)) from None


@router.get("/status")
def status(request: Request) -> dict[str, Any]:
    s = _services(request)
    return {
        "hub_online": s.hub.online,
        "hub_state": s.hub.state.model_dump() if s.hub.state else None,
        "cocktail": s.cocktail.status.to_dict(),
    }


@router.get("/mqtt")
def mqtt_messages(request: Request) -> list[dict[str, Any]]:
    """The last MQTT messages between the Pi and the hub, oldest first."""
    return list(_services(request).mqtt_log.entries)


@router.get("/ingredients")
def ingredients(request: Request) -> list[dict[str, Any]]:
    with _services(request).sessions() as session:
        rows = session.scalars(select(Ingredient).order_by(Ingredient.name))
        return [{"id": i.id, "name": i.name, "type": i.type.value, "g_per_ml": i.g_per_ml} for i in rows]


@router.get("/recipes")
def recipes(request: Request, alcoholic: bool | None = None) -> list[dict[str, Any]]:
    items = _services(request).cocktail.recipes()
    return [r for r in items if alcoholic is None or r["alcoholic"] == alcoholic]


@router.get("/recipes/{recipe_id}/plan")
def plan(request: Request, recipe_id: int, strength: float = 1.0) -> dict[str, Any]:
    try:
        return _plan_dict(_services(request).cocktail.plan(recipe_id, strength))
    except CocktailError as e:
        raise HTTPException(400, str(e)) from None


@router.get("/bays")
def bays(request: Request) -> list[dict[str, Any]]:
    return [_bay_dict(b) for b in _services(request).bays.list()]


@router.post("/bays/{bay_id}/fill/start")
async def fill_start(request: Request, bay_id: int) -> dict[str, str]:
    await _hub_call(_services(request).bays.start_fill(bay_id))
    return {"status": "tilted"}


@router.post("/bays/{bay_id}/fill/confirm")
async def fill_confirm(request: Request, bay_id: int, body: FillRequest) -> dict[str, Any]:
    bay = await _hub_call(_services(request).bays.confirm_fill(bay_id, body.ingredient_id, body.volume_ml))
    return _bay_dict(bay)


@router.post("/bays/{bay_id}/fill/cancel")
async def fill_cancel(request: Request, bay_id: int) -> dict[str, str]:
    await _hub_call(_services(request).bays.cancel_fill(bay_id))
    return {"status": "cancelled"}


@router.post("/bays/{bay_id}/empty")
def empty(request: Request, bay_id: int) -> dict[str, Any]:
    try:
        return _bay_dict(_services(request).bays.empty(bay_id))
    except BayError as e:
        raise HTTPException(400, str(e)) from None


@router.post("/cocktail/start")
async def cocktail_start(request: Request, body: StartRequest) -> dict[str, Any]:
    s = _services(request)
    if not s.hub.online:
        raise HTTPException(503, "hub is offline")
    try:
        return s.cocktail.start(body.recipe_id, body.strength).to_dict()
    except CocktailError as e:
        raise HTTPException(400, str(e)) from None


@router.post("/cocktail/next")
async def cocktail_next(request: Request) -> dict[str, Any]:
    s = _services(request)
    try:
        s.cocktail.next()
    except CocktailError as e:
        raise HTTPException(409, str(e)) from None
    return s.cocktail.status.to_dict()


@router.post("/cocktail/abort")
async def cocktail_abort(request: Request) -> dict[str, Any]:
    s = _services(request)
    await s.cocktail.abort()
    return s.cocktail.status.to_dict()


class Broadcaster:
    """Fans out events to every connected WebSocket."""

    def __init__(self):
        self._queues: set[asyncio.Queue] = set()

    def publish(self, kind: str, data: Any) -> None:
        message = json.dumps({"type": kind, "data": data}, default=lambda o: o.model_dump())
        for queue in list(self._queues):
            if queue.qsize() < 100:
                queue.put_nowait(message)

    async def serve(self, ws: WebSocket, initial: list[tuple[str, Any]]) -> None:
        await ws.accept()
        queue: asyncio.Queue = asyncio.Queue()
        self._queues.add(queue)
        try:
            for kind, data in initial:
                await ws.send_text(json.dumps({"type": kind, "data": data}, default=lambda o: o.model_dump()))
            while True:
                await ws.send_text(await queue.get())
        except (WebSocketDisconnect, RuntimeError):
            pass
        finally:
            self._queues.discard(queue)


@router.websocket("/ws")
async def ws(websocket: WebSocket) -> None:
    s = websocket.app.state
    initial = [("online", s.hub.online), ("cocktail", s.cocktail.status.to_dict())]
    if s.hub.state:
        initial.append(("state", s.hub.state))
    await s.broadcaster.serve(websocket, initial)
