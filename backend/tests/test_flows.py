"""End to end: services -> HubLink -> broker -> simulated hub, and back."""

import math

import pytest
from sqlalchemy import select

from cwjh.models import Bay, Ingredient, Recipe
from cwjh.mqtt_link import HubError
from cwjh.protocol import Move, Pour
from cwjh.services.cocktail import CocktailError, Phase

from .conftest import wait_for

pytestmark = pytest.mark.asyncio


def ingredient_id(rig, name):
    with rig.sessions() as s:
        return s.scalar(select(Ingredient.id).where(Ingredient.name == name))


def recipe_id(rig, name):
    with rig.sessions() as s:
        return s.scalar(select(Recipe.id).where(Recipe.name == name))


def bay_volume(rig, bay_id):
    with rig.sessions() as s:
        return s.get(Bay, bay_id).volume_ml


async def fill(rig, bay_id, name, volume=700.0):
    await rig.bays.start_fill(bay_id)
    assert rig.sim.state.bays[bay_id - 1] == 30
    await rig.bays.confirm_fill(bay_id, ingredient_id(rig, name), volume)
    assert rig.sim.state.bays[bay_id - 1] == 0


def phase(rig):
    return rig.cocktail.status.phase


async def test_fill_and_empty_bay(rig):
    await fill(rig, 4, "Gin", 500)
    bay = rig.bays.list()[3]
    assert bay.ingredient.name == "Gin"
    assert bay.volume_ml == 500
    rig.bays.empty(4)
    assert not rig.bays.list()[3].filled


async def test_move_is_clamped_by_the_hub_too(rig):
    await rig.hub.send(Move(bay=1, degrees=30))
    assert rig.sim.state.bays[0] == 30


async def test_full_cocktail(rig):
    await fill(rig, 1, "Vodka")
    await fill(rig, 2, "Orange Juice")

    rig.cocktail.start(recipe_id(rig, "Screwdriver"))
    assert phase(rig) is Phase.AWAIT_GLASS
    rig.cocktail.next()  # glass placed

    await wait_for(lambda: phase(rig) is Phase.AWAIT_MANUAL)  # ice cubes come first
    rig.cocktail.next()

    await wait_for(lambda: phase(rig) is Phase.DONE, timeout=5)
    steps = rig.cocktail.status.plan.steps
    assert [s.status for s in steps] == ["done", "done", "done"]
    assert steps[1].poured_ml == pytest.approx(50, abs=0.2)
    assert steps[2].poured_ml == pytest.approx(150, abs=0.2)
    assert bay_volume(rig, 1) == pytest.approx(650, abs=0.2)
    assert bay_volume(rig, 2) == pytest.approx(550, abs=0.2)
    assert rig.sim.state.lift == "down"
    assert any(e["progress"] for e in rig.events), "UI never got pour progress"

    rig.cocktail.next()  # drink taken
    assert phase(rig) is Phase.IDLE


async def test_empty_bottle_stops_with_bay_empty(rig):
    await fill(rig, 1, "Orange Juice")
    await fill(rig, 2, "Grenadine")
    rig.sim.bay_liquid_ml[1] = 40.0  # bottle has much less than the UI thinks

    rig.cocktail.start(recipe_id(rig, "Virgin Sunrise"))
    rig.cocktail.next()
    await wait_for(lambda: phase(rig) is Phase.AWAIT_MANUAL)
    rig.cocktail.next()

    await wait_for(lambda: phase(rig) is Phase.ERROR, timeout=5)
    assert rig.cocktail.status.error == "hub: bay_empty"
    juice = rig.cocktail.status.plan.steps[1]
    assert juice.status == "error"
    assert juice.poured_ml == pytest.approx(40, abs=0.2)
    assert bay_volume(rig, 1) == pytest.approx(660, abs=0.2)


async def test_no_glass(rig):
    await fill(rig, 1, "Orange Juice")
    await fill(rig, 2, "Grenadine")
    rig.sim.glass_present = False

    rig.cocktail.start(recipe_id(rig, "Virgin Sunrise"))
    rig.cocktail.next()
    await wait_for(lambda: phase(rig) is Phase.ERROR)
    assert rig.cocktail.status.error == "hub: no_glass"


async def test_abort_while_pouring_stops_hub(rig):
    rig.sim.flow_ml_s = 1.0  # slow, so the pour is still running when we abort
    await fill(rig, 1, "Orange Juice")
    await fill(rig, 2, "Grenadine")

    rig.cocktail.start(recipe_id(rig, "Virgin Sunrise"))
    rig.cocktail.next()
    await wait_for(lambda: phase(rig) is Phase.AWAIT_MANUAL)
    rig.cocktail.next()
    await wait_for(lambda: phase(rig) is Phase.POURING and rig.sim.state.busy)

    await rig.cocktail.abort()
    assert phase(rig) is Phase.IDLE
    await wait_for(lambda: not rig.sim.state.busy)


async def test_hub_rejects_second_command_while_busy(rig):
    rig.sim.flow_ml_s = 1.0
    await rig.hub.send(rig_glass_init())
    import asyncio

    pour = asyncio.create_task(rig.hub.send(Pour(bay=1, ml=100), timeout_s=5))
    await wait_for(lambda: rig.sim.state.busy)
    # Bypass the link's own lock to see the hub's answer.
    with pytest.raises(HubError, match="busy"):
        await rig.hub._send(Move(bay=2, degrees=10), 1.0)
    await rig.hub.emergency_stop()
    with pytest.raises(HubError, match="stopped"):
        await pour


def rig_glass_init():
    from cwjh.protocol import GlassInit

    return GlassInit()


async def test_hub_enforces_glass_limit(rig):
    rig.sim.bay_liquid_ml[1] = math.inf
    await rig.hub.send(rig_glass_init())
    await rig.hub.send(Pour(bay=1, ml=390), timeout_s=5)
    with pytest.raises(HubError, match="limit"):
        await rig.hub.send(Pour(bay=1, ml=20))


async def test_hub_going_offline(rig):
    await fill(rig, 1, "Vodka")
    rig.sim_transport.drop()
    await wait_for(lambda: not rig.hub.online)
    with pytest.raises(HubError, match="offline"):
        await rig.bays.start_fill(2)


async def test_ack_timeout_sends_stop(rig):
    rig.sim.time_scale = 1.0
    rig.hub._default_timeout_s = 0.1
    with pytest.raises(HubError, match="timeout"):
        await rig.hub.send(rig_glass_init())
    await wait_for(lambda: not rig.sim.state.busy)


async def test_cannot_start_unavailable_recipe(rig):
    with pytest.raises(CocktailError, match="not enough"):
        rig.cocktail.start(recipe_id(rig, "Mojito"))


async def test_recipe_availability_listing(rig):
    await fill(rig, 1, "Orange Juice")
    await fill(rig, 2, "Grenadine")
    by_name = {r["name"]: r for r in rig.cocktail.recipes()}
    assert by_name["Virgin Sunrise"]["available"]
    assert not by_name["Virgin Sunrise"]["alcoholic"]
    assert not by_name["Screwdriver"]["available"]
    assert by_name["Screwdriver"]["alcoholic"]
