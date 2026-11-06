"""Planning and running a cocktail: the "default behavior" flow from the wiki."""

import asyncio
import enum
import logging
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload, sessionmaker

from ..config import Settings
from ..models import Bay, IngredientType, Recipe, RecipeIngredient
from ..hub import Hub, HubError
from ..protocol import MAX_GLASS_ML, GlassInit, GlassRelease, Pour

log = logging.getLogger(__name__)

MIN_STRENGTH = 0.5
MAX_STRENGTH = 1.5


class CocktailError(Exception):
    pass


@dataclass
class Step:
    ingredient: str
    quantity: float
    manual: bool
    bay: int | None = None
    g_per_ml: float = 1.0
    status: str = "pending"  # pending, active, done, error
    poured_ml: float = 0.0


@dataclass
class Plan:
    recipe_id: int
    recipe: str
    strength: float
    steps: list[Step]

    @property
    def total_ml(self) -> float:
        return sum(s.quantity for s in self.steps if not s.manual)


def scale_quantities(items: list[RecipeIngredient], strength: float) -> list[float]:
    """Scale alcoholic parts by strength, then shrink everything to fit the glass."""
    quantities = [
        ri.quantity * (strength if ri.ingredient.type is IngredientType.ALCOHOLIC else 1.0) for ri in items
    ]
    liquid = sum(q for q, ri in zip(quantities, items) if ri.ingredient.is_liquid)
    factor = min(1.0, MAX_GLASS_ML / liquid) if liquid else 1.0
    return [round(q * factor, 1) if ri.ingredient.is_liquid else q for q, ri in zip(quantities, items)]


def build_plan(recipe: Recipe, bays: list[Bay], strength: float = 1.0) -> Plan:
    """Map each liquid to a bay holding enough of it. Raises CocktailError if one is missing."""
    if not MIN_STRENGTH <= strength <= MAX_STRENGTH:
        raise CocktailError(f"strength must be {MIN_STRENGTH}..{MAX_STRENGTH}")
    remaining = {b.id: b.volume_ml for b in bays if b.filled}
    steps, missing = [], []
    for ri, qty in zip(recipe.ingredients, scale_quantities(recipe.ingredients, strength)):
        ing = ri.ingredient
        if not ing.is_liquid:
            steps.append(Step(ingredient=ing.name, quantity=qty, manual=True))
            continue
        bay = next((b for b in bays if b.ingredient_id == ing.id and remaining[b.id] >= qty), None)
        if bay is None:
            missing.append(ing.name)
            continue
        remaining[bay.id] -= qty
        steps.append(Step(ingredient=ing.name, quantity=qty, manual=False, bay=bay.id, g_per_ml=ing.g_per_ml))
    if missing:
        raise CocktailError("not enough in the bays: " + ", ".join(missing))
    return Plan(recipe_id=recipe.id, recipe=recipe.name, strength=strength, steps=steps)


class Phase(str, enum.Enum):
    IDLE = "idle"
    AWAIT_GLASS = "await_glass"
    INIT_GLASS = "init_glass"
    POURING = "pouring"
    AWAIT_MANUAL = "await_manual"
    RELEASING = "releasing"
    DONE = "done"
    ERROR = "error"


@dataclass
class Status:
    phase: Phase = Phase.IDLE
    plan: Plan | None = None
    step: int | None = None
    error: str | None = None
    progress: dict[str, float] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["phase"] = self.phase.value
        return data


Listener = Callable[[dict[str, Any]], None]


class CocktailService:
    def __init__(self, sessions: sessionmaker[Session], hub: Hub, settings: Settings):
        self._sessions = sessions
        self._hub = hub
        self._settings = settings
        self._listeners: list[Listener] = []
        self._task: asyncio.Task | None = None
        self._next = asyncio.Event()
        self._pour_id: str | None = None
        self.status = Status()
        hub.add_listener(self._on_hub)

    def add_listener(self, listener: Listener) -> None:
        self._listeners.append(listener)

    def recipes(self) -> list[dict[str, Any]]:
        """All recipes, each marked whether the bays can make it right now."""
        with self._sessions() as session:
            bays = list(session.scalars(select(Bay)))
            recipes = session.scalars(
                select(Recipe).options(selectinload(Recipe.ingredients).selectinload(RecipeIngredient.ingredient))
            ).all()
            result = []
            for r in sorted(recipes, key=lambda r: r.name):
                try:
                    build_plan(r, bays)
                    available, missing = True, None
                except CocktailError as e:
                    available, missing = False, str(e)
                result.append(
                    {
                        "id": r.id,
                        "name": r.name,
                        "image": r.base64_image,
                        "alcoholic": r.alcoholic,
                        "available": available,
                        "missing": missing,
                        "ingredients": [
                            {"name": ri.ingredient.name, "quantity": ri.quantity, "type": ri.ingredient.type.value}
                            for ri in r.ingredients
                        ],
                    }
                )
            return result

    def plan(self, recipe_id: int, strength: float) -> Plan:
        with self._sessions() as session:
            recipe = session.get(
                Recipe,
                recipe_id,
                options=[selectinload(Recipe.ingredients).selectinload(RecipeIngredient.ingredient)],
            )
            if recipe is None:
                raise CocktailError("unknown recipe")
            return build_plan(recipe, list(session.scalars(select(Bay))), strength)

    @property
    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    def start(self, recipe_id: int, strength: float = 1.0) -> Status:
        if self.running:
            raise CocktailError("a cocktail is already being made")
        plan = self.plan(recipe_id, strength)
        self.status = Status(phase=Phase.AWAIT_GLASS, plan=plan)
        self._next.clear()
        self._task = asyncio.create_task(self._run(plan))
        self._emit()
        return self.status

    def next(self) -> None:
        """User confirmation: glass is placed, a manual step is done, or the drink was taken."""
        if self.status.phase in (Phase.DONE, Phase.ERROR):
            self.status = Status()
            self._emit()
            return
        if self.status.phase not in (Phase.AWAIT_GLASS, Phase.AWAIT_MANUAL):
            raise CocktailError(f"nothing to confirm while {self.status.phase.value}")
        self._next.set()

    async def abort(self) -> None:
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        self.status = Status()
        self._emit()

    async def _run(self, plan: Plan) -> None:
        try:
            await self._wait_next()
            self._set(phase=Phase.INIT_GLASS)
            await self._hub.send(GlassInit())

            for i, step in enumerate(plan.steps):
                step.status = "active"
                if step.manual:
                    self._set(phase=Phase.AWAIT_MANUAL, step=i)
                    await self._wait_next()
                else:
                    self._set(phase=Phase.POURING, step=i, progress={})
                    await self._pour(step)
                step.status = "done"
                self._emit()

            self._set(phase=Phase.RELEASING, step=None)
            await self._hub.send(GlassRelease())
            self._set(phase=Phase.DONE)
        except asyncio.CancelledError:
            await self._stop_hub()
            raise
        except HubError as e:
            self._fail(plan, f"hub: {e.code}")
        except Exception as e:
            log.exception("cocktail failed")
            self._fail(plan, str(e))

    async def _pour(self, step: Step) -> None:
        cmd = Pour(bay=step.bay, ml=step.quantity, g_per_ml=step.g_per_ml)
        self._pour_id = cmd.id
        try:
            ack = await self._hub.send(cmd, timeout_s=self._settings.pour_timeout_s(step.quantity))
            step.poured_ml = ack.poured_ml or 0.0
        except HubError as e:
            step.poured_ml = e.poured_ml or 0.0
            raise
        finally:
            self._pour_id = None
            self._book(step)

    def _book(self, step: Step) -> None:
        if not step.poured_ml:
            return
        with self._sessions.begin() as session:
            bay = session.get(Bay, step.bay)
            bay.volume_ml = max(0.0, round(bay.volume_ml - step.poured_ml, 1))

    async def _wait_next(self) -> None:
        # Not cleared before waiting: a tap that arrived before we got here must count.
        # next() only sets it while an AWAIT_* phase is shown, so it cannot be stale.
        await self._next.wait()
        self._next.clear()

    async def _stop_hub(self) -> None:
        try:
            await self._hub.emergency_stop()
        except HubError:
            pass

    def _fail(self, plan: Plan, message: str) -> None:
        if self.status.step is not None:
            plan.steps[self.status.step].status = "error"
        self._set(phase=Phase.ERROR, error=message)

    def _on_hub(self, kind: str, data: Any) -> None:
        if kind == "progress" and data.id == self._pour_id:
            self.status.progress = {"grams": data.grams, "target": data.target}
            self._emit()

    def _set(self, **changes: Any) -> None:
        for key, value in changes.items():
            setattr(self.status, key, value)
        self._emit()

    def _emit(self) -> None:
        data = self.status.to_dict()
        for listener in self._listeners:
            try:
                listener(data)
            except Exception:
                log.exception("cocktail listener failed")
