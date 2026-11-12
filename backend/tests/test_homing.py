"""Homing bay 1 on a switch that is pressed at rest, from different starting positions."""

import pytest

from cwjh.protocol import Move
from cwjh.sim.nano_sim import NanoSim

from .conftest import Rig, wait_for

WIDTH = 20  # the simulated switch is pressed within +-10 steps of its position
OFFSET = 5  # default home_offset_steps: rest is this far onto the switch
REST = WIDTH // 2 - OFFSET  # physical rest position of a bay homed from the tilt side


async def homed_rig(sessions, start: int, invert: bool = False, home_at: int = 0) -> Rig:
    """A rig whose bay 1 has a home switch at physical position home_at and starts at start."""
    rig = Rig(sessions, bays=2)
    rig.nano.home_at = {1: home_at}
    rig.nano.home_width = WIDTH
    rig.nano.physical[0] = start
    rig.configure(invert_direction=invert)
    await rig.start()
    return rig


@pytest.mark.parametrize(
    "start",
    [
        pytest.param(0, id="at rest"),
        pytest.param(150, id="tilted"),
        pytest.param(-300, id="pouring"),
        pytest.param(500, id="far tilted"),
    ],
)
@pytest.mark.asyncio
async def test_homes_from_anywhere_to_the_edge_on_the_tilt_side(sessions, start):
    rig = await homed_rig(sessions, start)
    try:
        assert rig.hub.state.homed[:2] == [True, None]
        assert rig.nano.positions[0] == 0
        # Position 0 is a little onto the switch, coming back from the tilt side.
        assert rig.nano.physical[0] == REST
        assert rig.nano.switch(1) == 1
        assert rig.hub.state.bays[0] == 0
    finally:
        rig.stop()


@pytest.mark.asyncio
async def test_homing_respects_the_inverted_direction(sessions):
    rig = await homed_rig(sessions, start=0, invert=True)
    try:
        assert rig.hub.state.homed[0] is True
        # Machine "tilt side" is the Nano's negative side now.
        assert rig.nano.physical[0] == -REST
        await rig.hub.send(Move(bay=1, degrees=30))
        assert rig.nano.positions[0] == -round(30 * 8.889)
    finally:
        rig.stop()


@pytest.mark.asyncio
async def test_switch_out_of_reach_leaves_the_bay_where_it_was(sessions):
    rig = await homed_rig(sessions, start=0, home_at=5000)
    try:
        assert rig.hub.online, "a failed homing must not keep the hub offline"
        assert rig.hub.state.homed[0] is False
        assert rig.nano.physical[0] == 0
        assert rig.nano.positions[0] == 0
    finally:
        rig.stop()


@pytest.mark.asyncio
async def test_homing_runs_slowly_then_moves_use_the_bay_profile(sessions):
    rig = await homed_rig(sessions, start=150)
    try:
        profiles = [line.split()[1:] for line in rig.nano.received if line.split()[1] == "P"]
        assert profiles == [["P", "1", "100", "100", "0", "0"]]  # constant home speed, no curves
        await rig.hub.send(Move(bay=1, degrees=10))
        assert rig.nano.profiles[1] == (300, 50, 200, 200)
    finally:
        rig.stop()


@pytest.mark.asyncio
async def test_rehomes_after_a_reconnect(sessions):
    rig = await homed_rig(sessions, start=0)
    try:
        await rig.hub.send(Move(bay=1, degrees=10))
        rig.nano.unplug()
        await wait_for(lambda: not rig.hub.online)
        rig.nano.plug()
        await wait_for(lambda: rig.hub.online)
        # After the reset the Nano counted from 0 at the tilted spot; homing found rest again.
        assert rig.nano.physical[0] == REST
        assert rig.nano.positions[0] == 0
    finally:
        rig.stop()


@pytest.mark.asyncio
async def test_returning_short_of_the_switch_re_seats_the_bay(sessions):
    rig = await homed_rig(sessions, start=0)
    try:
        await rig.hub.send(Move(bay=1, degrees=30))
        rig.nano.physical[0] += 12  # 12 steps lost on the way: rest is now off the switch
        await rig.hub.send(Move(bay=1, degrees=0))
        assert rig.nano.switch(1) == 1, "back at rest, the switch must be pressed"
        assert rig.nano.physical[0] == REST
        assert rig.nano.positions[0] == 0
        assert rig.hub.state.homed[0] is True
    finally:
        rig.stop()


@pytest.mark.asyncio
async def test_no_extra_moves_when_the_switch_is_pressed_at_rest(sessions):
    rig = await homed_rig(sessions, start=0)
    try:
        await rig.hub.send(Move(bay=1, degrees=30))
        before = len([l for l in rig.nano.received if l.split()[1] in ("F", "Z")])
        await rig.hub.send(Move(bay=1, degrees=0))
        after = len([l for l in rig.nano.received if l.split()[1] in ("F", "Z")])
        assert after == before
    finally:
        rig.stop()


def test_sim_switch_reports_pressed_and_released():
    sim = NanoSim(bays=2, home_at={1: 0}, home_width=WIDTH)
    assert sim.switch(1) == 1
    sim.physical[0] = 11
    assert sim.switch(1) == 0
    assert sim.switch(2) is None
