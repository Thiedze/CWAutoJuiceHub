"""The machine logic. It turns the commands of the services into motor moves on the Nano.

Until a scale is fitted, a pour is timed: open the dispenser, wait ml / flow, close it.
Every move runs with the bay's motion profile (services/motors.py), blended by the weight
of its bottle. Positions here are machine steps from rest: positive tilts the bottle, toward filling and
further to pouring.
"""

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import Any, TypeVar

from .config import Settings
from .nano_link import NanoError, NanoLink
from .services.motors import DefaultMotors, Motors, Profile
from .protocol import (
    BAY_COUNT,
    MAX_GLASS_ML,
    Ack,
    Command,
    GlassInit,
    GlassRelease,
    HubState,
    Move,
    Pour,
    Progress,
    Stop,
)

log = logging.getLogger(__name__)

Listener = Callable[[str, Any], None]
T = TypeVar("T")

PROGRESS_INTERVAL_S = 0.2
STOP_TIMEOUT_S = 2.0


class HubError(Exception):
    def __init__(self, code: str, poured_ml: float | None = None):
        super().__init__(code)
        self.code = code
        self.poured_ml = poured_ml


class Hub:
    def __init__(self, link: NanoLink, settings: Settings, motors: Motors | None = None):
        self._link = link
        self._settings = settings
        self._motors: Motors = motors or DefaultMotors(settings)
        self._profiles: dict[int, Profile] = {}  # last profile sent per bay
        self._positions: dict[int, int] = {}  # last known machine position per bay
        self._switch_bays: set[int] = set()  # bays with a home switch
        self._listeners: list[Listener] = []
        self._lock = asyncio.Lock()
        self._stop = asyncio.Event()
        self._ready = False
        self._sync_task: asyncio.Task | None = None
        self.state: HubState | None = None
        link.add_listener(self._on_link)

    @property
    def online(self) -> bool:
        return self._link.online and self._ready

    def add_listener(self, listener: Listener) -> None:
        """Listener gets ("online", bool), ("state", HubState) and ("progress", Progress)."""
        self._listeners.append(listener)

    async def start(self) -> None:
        await self._link.start()

    def stop(self) -> None:
        self._link.stop()

    async def park(self, timeout_s: float = 30) -> None:
        """Move every bay that is away from rest back to 0, e.g. before shutting down, so the
        next start does not have to home from a tilted position."""
        if not self.online:
            return
        try:
            positions = (await self.snapshot())["positions"]
        except HubError:
            return
        for bay, position in enumerate(positions, start=1):
            if position != 0:
                try:
                    await self.move_to(bay, 0, timeout_s=timeout_s)
                    log.info("bay %d parked at rest", bay)
                except HubError as e:
                    log.error("bay %d could not be parked: %s", bay, e.code)

    async def send(self, cmd: Command, timeout_s: float | None = None) -> Ack:
        """Run a command and return its final ack. Raises HubError on error or timeout."""
        if not self.online:
            raise HubError("offline")
        if isinstance(cmd, Stop):
            await self.emergency_stop()
            return Ack(id=cmd.id, status="done")
        return await self._exclusive(lambda: self._run(cmd), timeout_s)

    async def _exclusive(self, action: Callable[[], Awaitable[T]], timeout_s: float | None = None) -> T:
        """Run one action at a time, with a time limit; a timeout stops the motors."""
        if not self.online:
            raise HubError("offline")
        async with self._lock:
            self._stop.clear()
            self._set_busy(True)
            try:
                return await asyncio.wait_for(action(), timeout_s or self._settings.ack_timeout_s)
            except TimeoutError:
                await self._send_stop()
                raise HubError("timeout") from None
            except NanoError as e:
                raise HubError(e.code) from None
            finally:
                self._set_busy(False)

    async def emergency_stop(self) -> None:
        """Stop every motor now, also in the middle of a running command."""
        self._stop.set()
        await self._send_stop()

    # Commands

    async def _run(self, cmd: Command) -> Ack:
        state = self._current()
        if isinstance(cmd, Move):
            await self._go(cmd.bay, round(cmd.degrees * self._motors.config(cmd.bay).steps_per_degree))
            state.bays[cmd.bay - 1] = cmd.degrees
        elif isinstance(cmd, Pour):
            poured_ml = await self._pour(cmd)
            return Ack(id=cmd.id, status="done", poured_ml=round(poured_ml, 1))
        elif isinstance(cmd, GlassInit):
            # No lift or scale yet: the user places the glass by hand.
            state.glass = True
            state.poured_ml = 0.0
        elif isinstance(cmd, GlassRelease):
            state.glass = False
        else:
            raise HubError("invalid")
        self._changed()
        return Ack(id=cmd.id, status="done")

    async def _pour(self, cmd: Pour) -> float:
        state = self._current()
        if state.poured_ml + cmd.ml > MAX_GLASS_ML + 1e-6:
            raise HubError("limit")
        if cmd.bay > self._link.bays:
            raise HubError("no_motor")

        try:
            poured_ml, stopped = await self._open_and_wait(cmd)
        except asyncio.CancelledError:
            # Aborted or timed out: stop any motion, then close the dispenser.
            await self._send_stop()
            await self._close(cmd.bay)
            raise
        except NanoError as e:
            await self._close(cmd.bay)
            raise HubError(e.code, 0.0) from None

        # Close the dispenser, also after a stop, so nothing keeps running out.
        await self._close(cmd.bay)
        state.poured_ml += poured_ml
        self._changed()
        if stopped:
            raise HubError("stopped" if self._link.online else "offline", poured_ml)
        self._progress(cmd, poured_ml * cmd.g_per_ml, cmd.ml * cmd.g_per_ml)
        return poured_ml

    async def _open_and_wait(self, cmd: Pour) -> tuple[float, bool]:
        """Timed pour: open, wait ml / flow. Returns the poured ml and whether it was stopped."""
        config = self._motors.config(cmd.bay)
        flow = config.pour_flow_ml_s
        target_g = cmd.ml * cmd.g_per_ml
        await self._go(cmd.bay, config.pour_open_steps)

        loop = asyncio.get_running_loop()
        duration = cmd.ml / flow
        started = loop.time()
        while (elapsed := loop.time() - started) < duration:
            self._progress(cmd, elapsed * flow * cmd.g_per_ml, target_g)
            try:
                await asyncio.wait_for(self._stop.wait(), min(PROGRESS_INTERVAL_S, duration - elapsed))
                return elapsed * flow, True
            except TimeoutError:
                pass
        return duration * flow, False

    def _direction(self, bay: int) -> int:
        return -1 if self._motors.config(bay).invert else 1

    async def _use_profile(self, bay: int, profile: Profile) -> None:
        if self._profiles.get(bay) != profile:
            await self._link.request(profile.request(bay), self._settings.ack_timeout_s)
            self._profiles[bay] = profile

    def _move_timeout(self, bay: int, target: int, profile: Profile) -> float:
        """Generous: the whole way at the start speed, from the last known position."""
        distance = abs(target - self._positions.get(bay, 0))
        return self._settings.ack_timeout_s + distance / profile.min_speed

    async def _go(self, bay: int, position: int, load_g: float | None = None, seat: bool = True) -> None:
        """Move to a machine position, with the profile for the bottle's weight (or load_g).
        Back at rest, a bay with a home switch re-seats itself on it (see _seat)."""
        if bay > self._link.bays:
            raise HubError("no_motor")
        profile = self._motors.profile(bay, load_g)
        await self._use_profile(bay, profile)
        words = await self._link.request(
            f"G {bay} {position * self._direction(bay)}", self._move_timeout(bay, position, profile)
        )
        self._positions[bay] = int(words[1]) * self._direction(bay)
        if seat and position == 0 and bay in self._switch_bays:
            await self._seat(bay)

    async def _close(self, bay: int) -> None:
        try:
            await self._go(bay, 0)
        except (NanoError, HubError, TimeoutError) as e:
            log.error("could not close bay %d: %r", bay, e)

    async def _find(self, bay: int, target: int, pressed: bool) -> tuple[int, bool]:
        """Move toward target (machine terms) until the home switch is pressed (or released).

        Returns the position where the move ended and whether the switch level was reached.
        """
        d = self._direction(bay)
        rate = self._settings.home_step_rate
        profile = Profile(max_speed=rate, min_speed=rate, start_ms=0, end_ms=0)
        await self._use_profile(bay, profile)
        words = await self._link.request(
            f"F {bay} {target * d} {int(pressed)}", self._move_timeout(bay, target, profile)
        )
        self._positions[bay] = int(words[1]) * d
        return self._positions[bay], words[2] == "1"

    async def _home(self, bay: int, pressed: bool) -> None:
        """Find the home switch and make its edge, seen from the tilt side, position 0.

        Raises HubError("home_failed") when the switch is not found within the search range.
        """
        s = self._settings
        start = await self._read_position(bay)
        pos = start

        if not pressed:
            # Search both sides in growing steps, so a wrong first guess costs little.
            radius = s.home_first_steps
            while not pressed:
                radius = min(radius, s.home_search_steps)
                for target in (start - radius, start + radius):
                    pos, pressed = await self._find(bay, target, True)
                    if pressed:
                        break
                if pressed or radius == s.home_search_steps:
                    break
                radius *= 2
            if not pressed:
                await self._go(bay, start, seat=False)
                raise HubError("home_failed")

        # On the switch: leave it toward the tilt side, then come back to where it presses.
        pos, released = await self._find(bay, pos + s.home_release_steps, False)
        if not released:
            raise HubError("home_failed")  # stuck switch
        pos, pressed = await self._find(bay, pos - s.home_release_steps, True)
        if not pressed:
            raise HubError("home_failed")
        await self._zero_on_switch(bay, pos)

    async def _zero_on_switch(self, bay: int, edge: int) -> None:
        """From the switch's edge, go home_offset_steps further onto it and call that 0."""
        offset = self._motors.config(bay).home_offset_steps
        if offset:
            await self._find(bay, edge - offset, False)  # stops early should the switch release
        await self._link.request(f"Z {bay}", self._settings.ack_timeout_s)
        self._positions[bay] = 0

    async def _seat(self, bay: int) -> None:
        """Back at rest, the switch must be pressed. If it is not (slack, lost steps), creep on
        until it presses and set 0 again, so errors never add up."""
        words = await self._link.request("W", self._settings.ack_timeout_s)
        if words[bay] != "0":
            return
        pos, pressed = await self._find(bay, -self._settings.home_release_steps, True)
        if not pressed:
            log.error("bay %d: home switch not found at rest, its position is a guess", bay)
            self._set_homed(bay, False)
            return
        await self._zero_on_switch(bay, pos)
        log.warning("bay %d was %d steps short of its home switch, re-seated", bay, -pos)

    def _set_homed(self, bay: int, homed: bool) -> None:
        if self.state is not None and bay <= len(self.state.homed):
            self.state.homed[bay - 1] = homed
            self._changed()

    async def _home_all(self) -> list[bool | None]:
        """Home every bay that has a switch. Per bay: True homed, False failed, None no switch."""
        try:
            words = await self._link.request("W", self._settings.ack_timeout_s)
        except NanoError:
            return []  # firmware without home switches
        switches = words[1:]
        if all(w == "-" for w in switches):
            return [None] * len(switches)

        homed: list[bool | None] = []
        for bay, switch in enumerate(switches, start=1):
            if switch == "-":
                homed.append(None)
                continue
            try:
                await self._home(bay, switch == "1")
                log.info("bay %d homed", bay)
                self._switch_bays.add(bay)
                homed.append(True)
            except HubError:
                log.error("bay %d: home switch not found, its position is a guess", bay)
                homed.append(False)
        return homed

    async def _read_position(self, bay: int) -> int:
        words = await self._link.request("Q", self._settings.ack_timeout_s)
        self._positions[bay] = int(words[1 + bay]) * self._direction(bay)
        return self._positions[bay]

    # Debug and calibration (the debug page). Each runs exclusively, like a command.

    async def snapshot(self) -> dict[str, Any]:
        """Machine positions and home switches of the wired bays; works while busy."""
        if not self.online:
            raise HubError("offline")
        try:
            q = await self._link.request("Q", self._settings.ack_timeout_s)
            w = await self._link.request("W", self._settings.ack_timeout_s)
        except NanoError as e:
            raise HubError(e.code) from None
        positions = [int(p) * self._direction(bay) for bay, p in enumerate(q[2:], start=1)]
        switches = [None if s == "-" else s == "1" for s in w[1:]]
        return {"busy": q[1] == "1", "positions": positions, "switches": switches}

    async def move_to(self, bay: int, position: int, load_g: float | None = None, timeout_s: float = 300) -> int:
        """Move a bay to a machine position in steps; returns where it ended."""

        async def action() -> int:
            await self._go(bay, position, load_g)
            self._note_position(bay)
            return self._positions[bay]

        return await self._exclusive(action, timeout_s)

    async def move_by(self, bay: int, steps: int, load_g: float | None = None, timeout_s: float = 300) -> int:
        async def action() -> int:
            if bay > self._link.bays:
                raise HubError("no_motor")
            await self._go(bay, await self._read_position(bay) + steps, load_g)
            self._note_position(bay)
            return self._positions[bay]

        return await self._exclusive(action, timeout_s)

    async def home(self, bay: int) -> bool:
        """Home one bay on its switch. False when it has none or the switch was not found."""

        async def action() -> bool:
            if bay > self._link.bays:
                raise HubError("no_motor")
            switch = (await self._link.request("W", self._settings.ack_timeout_s))[bay]
            if switch == "-":
                raise HubError("no_switch")
            try:
                await self._home(bay, switch == "1")
                ok = True
                self._switch_bays.add(bay)
            except HubError:
                ok = False
                self._switch_bays.discard(bay)
            self._current().homed[bay - 1] = ok
            self._note_position(bay)
            return ok

        return await self._exclusive(action, 300)

    async def dwell(self, seconds: float) -> None:
        """Wait between test moves; an emergency stop ends it with HubError("stopped")."""
        self._stop.clear()
        try:
            await asyncio.wait_for(self._stop.wait(), seconds)
        except TimeoutError:
            return
        raise HubError("stopped")

    async def zero(self, bay: int) -> None:
        async def action() -> None:
            if bay > self._link.bays:
                raise HubError("no_motor")
            await self._link.request(f"Z {bay}", self._settings.ack_timeout_s)
            self._positions[bay] = 0
            self._note_position(bay)

        await self._exclusive(action)

    async def hold(self, bay: int, on: bool) -> None:
        async def action() -> None:
            if bay > self._link.bays:
                raise HubError("no_motor")
            await self._link.request(f"H {bay} {int(on)}", self._settings.ack_timeout_s)

        await self._exclusive(action)

    async def raw(self, line: str, timeout_s: float = 60) -> list[str]:
        """Send one request as typed (without the sequence number) and return the reply.

        Not exclusive, so S and Q also work while something runs. The Nano answers busy
        to moves that would collide. Profiles are resent afterwards, in case it was a P.
        """
        if not self.online:
            raise HubError("offline")
        self._profiles.clear()
        try:
            return await self._link.request(line.strip(), timeout_s)
        except NanoError as e:
            raise HubError(e.code) from None

    def config_changed(self, bay: int) -> None:
        """A bay's calibration changed: send its profile again, re-read its position."""
        self._profiles.pop(bay, None)
        self._positions.pop(bay, None)

    def _note_position(self, bay: int) -> None:
        """Show a debug move in the hub state, in degrees."""
        state = self._current()
        if bay <= len(state.bays) and bay in self._positions:
            state.bays[bay - 1] = self._positions[bay] / self._motors.config(bay).steps_per_degree
            self._changed()

    async def _send_stop(self) -> None:
        try:
            await self._link.request("S", STOP_TIMEOUT_S)
        except (NanoError, TimeoutError) as e:
            log.error("stop not confirmed by the Nano: %s", e)

    # State

    def _on_link(self, online: bool) -> None:
        self._ready = False
        self._profiles.clear()  # a reset Nano is back at its default profile
        self._positions.clear()
        self._switch_bays.clear()
        if self._sync_task:
            self._sync_task.cancel()
        if online:
            self._sync_task = asyncio.get_running_loop().create_task(self._sync())
        else:
            self._stop.set()  # ends a running pour wait
            self._emit("online", False)

    async def _sync(self) -> None:
        """After a (re)connect: home the bays, read where the motors are."""
        try:
            homed = await self._home_all()
            words = await self._link.request("Q", self._settings.ack_timeout_s)
        except (NanoError, TimeoutError) as e:
            log.error("Nano sync failed: %r", e)
            return
        positions = [int(w) * self._direction(bay) for bay, w in enumerate(words[2:], start=1)]
        self._positions = dict(enumerate(positions, start=1))
        degrees = [p / self._motors.config(bay).steps_per_degree for bay, p in enumerate(positions, start=1)]
        degrees += [0.0] * (BAY_COUNT - len(degrees))
        homed += [None] * (BAY_COUNT - len(homed))
        self.state = HubState(bays=degrees[:BAY_COUNT], homed=homed[:BAY_COUNT], fw=self._link.fw)
        self._ready = True
        self._emit("online", True)
        self._emit("state", self.state)

    def _current(self) -> HubState:
        assert self.state is not None, "no state before the Nano is synced"
        return self.state

    def _set_busy(self, busy: bool) -> None:
        if self.state is not None:
            self.state.busy = busy
            self._changed()

    def _changed(self) -> None:
        if self.state is not None:
            self._emit("state", self.state.model_copy(deep=True))

    def _progress(self, cmd: Pour, grams: float, target: float) -> None:
        self._emit("progress", Progress(id=cmd.id, grams=round(grams, 1), target=round(target, 1)))

    def _emit(self, kind: str, data: Any) -> None:
        for listener in self._listeners:
            try:
                listener(kind, data)
            except Exception:
                log.exception("hub listener failed")
