"""The Pi side of the hub protocol: send commands, wait for acks, track hub state."""

import asyncio
import json
import logging
from collections.abc import Callable
from typing import Any

from pydantic import ValidationError

from .protocol import (
    HUB_ACK,
    HUB_ALL,
    HUB_ONLINE,
    HUB_PROGRESS,
    HUB_STATE,
    Ack,
    Command,
    HubState,
    Progress,
    RequestState,
    Stop,
)
from .transport import Transport

log = logging.getLogger(__name__)

Listener = Callable[[str, Any], None]


class HubError(Exception):
    def __init__(self, code: str, poured_ml: float | None = None):
        super().__init__(code)
        self.code = code
        self.poured_ml = poured_ml


class HubLink:
    def __init__(self, transport: Transport, default_timeout_s: float = 30.0):
        self._transport = transport
        self._default_timeout_s = default_timeout_s
        self._loop: asyncio.AbstractEventLoop | None = None
        self._pending: dict[str, asyncio.Future[Ack]] = {}
        self._lock = asyncio.Lock()
        self._listeners: list[Listener] = []
        self.online = False
        self.state: HubState | None = None

    def add_listener(self, listener: Listener) -> None:
        """Listener gets ("online", bool), ("state", HubState) and ("progress", Progress)."""
        self._listeners.append(listener)

    async def start(self) -> None:
        self._loop = asyncio.get_running_loop()
        self._transport.start(self._on_message_threadsafe, [HUB_ALL], on_connect=self._on_connect_threadsafe)

    def stop(self) -> None:
        self._transport.stop()

    async def send(self, cmd: Command, timeout_s: float | None = None) -> Ack:
        """Send a command and wait for its final ack. Raises HubError on error or timeout."""
        if not self.online:
            raise HubError("offline")
        async with self._lock:
            return await self._send(cmd, timeout_s or self._default_timeout_s)

    async def emergency_stop(self) -> None:
        """Send stop without waiting for the running command to finish."""
        await self._send(Stop(), self._default_timeout_s)

    async def _send(self, cmd: Command, timeout_s: float) -> Ack:
        assert self._loop is not None, "HubLink.start() was not called"
        future: asyncio.Future[Ack] = self._loop.create_future()
        self._pending[cmd.id] = future
        try:
            self._transport.publish(cmd.topic, cmd.payload(), qos=1)
            ack = await asyncio.wait_for(future, timeout_s)
        except TimeoutError:
            if not isinstance(cmd, Stop):
                self._transport.publish(Stop.topic, Stop().payload(), qos=1)
            raise HubError("timeout") from None
        finally:
            self._pending.pop(cmd.id, None)
        if ack.status == "error":
            raise HubError(ack.code or "error", ack.poured_ml)
        return ack

    def _on_connect_threadsafe(self) -> None:
        # Ask for fresh state; the retained one may be stale.
        self._transport.publish(RequestState.topic, RequestState().payload(), qos=1)

    def _on_message_threadsafe(self, topic: str, payload: bytes) -> None:
        if self._loop is None or self._loop.is_closed():
            return
        self._loop.call_soon_threadsafe(self._on_message, topic, payload)

    def _on_message(self, topic: str, payload: bytes) -> None:
        try:
            if topic == HUB_ONLINE:
                self.online = payload.strip() == b"1"
                if not self.online:
                    for future in self._pending.values():
                        if not future.done():
                            future.set_exception(HubError("offline"))
                self._emit("online", self.online)
            elif topic == HUB_STATE:
                self.state = HubState.model_validate_json(payload)
                self._emit("state", self.state)
            elif topic == HUB_PROGRESS:
                self._emit("progress", Progress.model_validate_json(payload))
            elif topic == HUB_ACK:
                ack = Ack.model_validate_json(payload)
                future = self._pending.get(ack.id)
                if future and not future.done() and ack.status != "accepted":
                    future.set_result(ack)
        except (ValidationError, json.JSONDecodeError, UnicodeDecodeError):
            log.warning("ignoring malformed message on %s: %r", topic, payload[:200])

    def _emit(self, kind: str, data: Any) -> None:
        for listener in self._listeners:
            try:
                listener(kind, data)
            except Exception:
                log.exception("hub listener failed")
