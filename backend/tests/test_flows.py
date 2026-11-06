"""End to end: services -> Hub -> NanoLink -> simulated Nano, and back."""

import asyncio

import pytest
from sqlalchemy import select

from cwjh.hub import HubError
from cwjh.models import Bay, Ingredient, Recipe
from cwjh.protocol import GlassInit, Move, Pour
from cwjh.services.cocktail import CocktailError, Phase

from .conftest import Rig, wait_for

pytestmark = pytest.mark.asyncio

TILT_STEPS = round(30 * 8.889)  # MAX_TILT_DEG at the default steps per degree


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
    assert rig.nano.positions[bay_id - 1] == TILT_STEPS
    assert rig.hub.state.bays[bay_id - 1] == 30
    await rig.bays.confirm_fill(bay_id, ingredient_id(rig, name), volume)
    assert rig.nano.positions[bay_id - 1] == 0


def phase(rig):
    return rig.cocktail.status.phase


def sent(rig, op):
    """Lines the Nano received with this op letter."""
    return [line for line in rig.nano.received if line.split()[1:2] == [op]]


async def test_fill_and_empty_bay(rig):
    await fill(rig, 4, "Gin", 500)
    bay = rig.bays.list()[3]
    assert bay.ingredient.name == "Gin"
    assert bay.volume_ml == 500
    rig.bays.empty(4)
    assert not rig.bays.list()[3].filled


async def test_move_turns_degrees_into_steps(rig):
    await rig.hub.send(Move(bay=2, degrees=12.5))
    assert rig.nano.positions[1] == round(12.5 * 8.889)
    await rig.hub.send(Move(bay=2, degrees=45))  # cut to 30
    assert rig.nano.positions[1] == TILT_STEPS


async def test_inverted_direction_flips_every_move(rig):
    rig.configure(invert_direction=True)
    await rig.hub.send(Move(bay=1, degrees=30))
    assert rig.nano.positions[0] == -TILT_STEPS
    assert rig.hub.state.bays[0] == 30
    await rig.hub.send(GlassInit())
    await rig.hub.send(Move(bay=1, degrees=0))
    await rig.hub.send(Pour(bay=1, ml=10), timeout_s=5)
    assert [line.split()[2:] for line in sent(rig, "G")][-2:] == [["1", "400"], ["1", "0"]]


async def test_sync_sets_speed_and_state(rig):
    assert sent(rig, "V") and sent(rig, "V")[0].endswith(" V 300")
    assert rig.hub.state.fw == "sim"
    assert rig.hub.state.bays == [0.0] * 12


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
    assert rig.nano.positions[:2] == [0, 0], "dispensers must be closed again"
    assert any(e["progress"] for e in rig.events), "UI never got pour progress"
    # Each pour opens the dispenser and closes it again.
    assert [line.split()[2:] for line in sent(rig, "G")][-4:] == [["1", "-400"], ["1", "0"], ["2", "-400"], ["2", "0"]]

    rig.cocktail.next()  # drink taken
    assert phase(rig) is Phase.IDLE


async def test_abort_while_pouring_stops_and_closes(rig):
    rig.configure(pour_flow_ml_s=1.0)  # slow, so the pour is still running when we abort
    await fill(rig, 1, "Orange Juice")
    await fill(rig, 2, "Grenadine")

    rig.cocktail.start(recipe_id(rig, "Virgin Sunrise"))
    rig.cocktail.next()
    await wait_for(lambda: phase(rig) is Phase.AWAIT_MANUAL)
    rig.cocktail.next()
    await wait_for(lambda: phase(rig) is Phase.POURING and rig.nano.positions[0] == -400)

    await rig.cocktail.abort()
    assert phase(rig) is Phase.IDLE
    assert sent(rig, "S"), "the Nano never got a stop"
    await wait_for(lambda: not rig.hub.state.busy)
    assert rig.nano.positions[0] == 0, "the dispenser was left open"


async def test_stop_during_a_move(rig):
    rig.nano.time_scale = 1.0  # 267 steps take almost a second
    move = asyncio.create_task(rig.hub.send(Move(bay=1, degrees=30)))
    await wait_for(lambda: rig.nano.positions[0] > 20)
    await rig.hub.emergency_stop()
    with pytest.raises(HubError, match="stopped"):
        await move
    assert 0 < rig.nano.positions[0] < TILT_STEPS


async def test_nano_rejects_a_second_move_while_moving(rig):
    rig.nano.time_scale = 1.0
    first = asyncio.create_task(rig.link.request("G 1 300", 5))
    await wait_for(lambda: rig.nano.busy)
    # Bypass the hub's own lock to see the Nano's answer.
    with pytest.raises(Exception, match="busy"):
        await rig.link.request("G 2 10", 1)
    state = await rig.link.request("Q", 1)  # queries still work while moving
    assert state[:2] == ["STATE", "1"]
    await rig.link.request("S", 1)
    with pytest.raises(Exception, match="stopped"):
        await first


async def test_hub_enforces_glass_limit(rig):
    await rig.hub.send(GlassInit())
    await rig.hub.send(Pour(bay=1, ml=390), timeout_s=5)
    with pytest.raises(HubError, match="limit"):
        await rig.hub.send(Pour(bay=1, ml=20))


async def test_glass_init_resets_the_poured_total(rig):
    await rig.hub.send(GlassInit())
    await rig.hub.send(Pour(bay=1, ml=300), timeout_s=5)
    await rig.hub.send(GlassInit())
    await rig.hub.send(Pour(bay=1, ml=300), timeout_s=5)
    assert rig.hub.state.poured_ml == pytest.approx(300, abs=0.2)


async def test_bay_without_motor(sessions):
    rig = Rig(sessions, bays=1)
    await rig.start()
    try:
        await rig.hub.send(Move(bay=1, degrees=10))
        with pytest.raises(HubError, match="no_motor"):
            await rig.hub.send(Move(bay=2, degrees=10))
        with pytest.raises(HubError, match="no_motor"):
            await rig.bays.start_fill(2)
    finally:
        rig.stop()


async def test_unplug_and_reconnect(rig):
    await fill(rig, 1, "Vodka")
    rig.nano.unplug()
    await wait_for(lambda: not rig.hub.online)
    with pytest.raises(HubError, match="offline"):
        await rig.bays.start_fill(2)

    rig.nano.plug()
    await wait_for(lambda: rig.hub.online)
    await rig.bays.start_fill(2)
    assert rig.nano.positions[1] == TILT_STEPS


async def test_unplug_while_pouring_reports_offline(rig):
    rig.configure(pour_flow_ml_s=1.0)
    await rig.hub.send(GlassInit())
    pour = asyncio.create_task(rig.hub.send(Pour(bay=1, ml=100), timeout_s=10))
    await wait_for(lambda: rig.nano.positions[0] == -400)
    rig.nano.unplug()
    with pytest.raises(HubError, match="offline"):
        await pour


async def test_silent_nano_goes_offline(rig):
    rig.nano.mute = True  # USB still there, firmware hung
    await wait_for(lambda: not rig.hub.online)


async def test_timeout_sends_stop(rig):
    rig.nano.time_scale = 1.0
    rig.configure(ack_timeout_s=0.1)
    with pytest.raises(HubError, match="timeout"):
        await rig.hub.send(Move(bay=1, degrees=30))
    assert sent(rig, "S")
    await wait_for(lambda: not rig.nano.busy)


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
