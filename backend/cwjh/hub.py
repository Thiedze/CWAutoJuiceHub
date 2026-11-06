"""The machine logic. It turns the commands of the services into motor moves on the Nano.

Until a scale is fitted, a pour is timed: open the dispenser, wait ml / flow, close it.
"""

import asyncio
import logging
from collections.abc import Callable
from typing import Any

from .config import Settings
from .nano_link import NanoError, NanoLink
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

PROGRESS_INTERVAL_S = 0.2
STOP_TIMEOUT_S = 2.0


class HubError(Exception):
    def __init__(self, code: str, poured_ml: float | None = None):
        super().__init__(code)
        self.code = code
        self.poured_ml = poured_ml


class Hub:
    def __init__(self, link: NanoLink, settings: Settings):
        self._link = link
        self._settings = settings
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

    async def send(self, cmd: Command, timeout_s: float | None = None) -> Ack:
        """Run a command and return its final ack. Raises HubError on error or timeout."""
        if not self.online:
            raise HubError("offline")
        if isinstance(cmd, Stop):
            await self.emergency_stop()
            return Ack(id=cmd.id, status="done")
        async with self._lock:
            self._stop.clear()
            self._set_busy(True)
            try:
                return await asyncio.wait_for(self._run(cmd), timeout_s or self._settings.ack_timeout_s)
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
            await self._go(cmd.bay, round(cmd.degrees * self._settings.steps_per_degree))
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
        flow = self._settings.pour_flow_ml_s
        target_g = cmd.ml * cmd.g_per_ml
        await self._go(cmd.bay, self._settings.pour_open_steps)

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

    def _direction(self) -> int:
        return -1 if self._settings.invert_direction else 1

    async def _go(self, bay: int, position: int) -> None:
        """Move to a position in machine terms: positive tilts the bottle, negative pours."""
        if bay > self._link.bays:
            raise HubError("no_motor")
        await self._link.request(f"G {bay} {position * self._direction()}", self._settings.ack_timeout_s)

    async def _close(self, bay: int) -> None:
        try:
            await self._go(bay, 0)
        except (NanoError, HubError, TimeoutError) as e:
            log.error("could not close bay %d: %r", bay, e)

    async def _send_stop(self) -> None:
        try:
            await self._link.request("S", STOP_TIMEOUT_S)
        except (NanoError, TimeoutError) as e:
            log.error("stop not confirmed by the Nano: %s", e)

    # State

    def _on_link(self, online: bool) -> None:
        self._ready = False
        if self._sync_task:
            self._sync_task.cancel()
        if online:
            self._sync_task = asyncio.get_running_loop().create_task(self._sync())
        else:
            self._stop.set()  # ends a running pour wait
            self._emit("online", False)

    async def _sync(self) -> None:
        """After a (re)connect: set the speed and read where the motors are."""
        try:
            await self._link.request(f"V {self._settings.step_rate}", self._settings.ack_timeout_s)
            words = await self._link.request("Q", self._settings.ack_timeout_s)
        except (NanoError, TimeoutError) as e:
            log.error("Nano sync failed: %s", e)
            return
        positions = [int(w) for w in words[2:]]
        degrees = [p * self._direction() / self._settings.steps_per_degree for p in positions]
        degrees += [0.0] * (BAY_COUNT - len(degrees))
        self.state = HubState(bays=degrees[:BAY_COUNT], fw=self._link.fw)
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
