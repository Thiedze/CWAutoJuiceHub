"""Records every MQTT message between the Pi and the hub, for the UI's MQTT panel."""

import asyncio
import logging
import time
from collections import deque
from collections.abc import Callable
from typing import Any

from .protocol import HUB_PROGRESS
from .transport import OnConnect, OnMessage, Transport

log = logging.getLogger("cwjh.mqtt")

Tap = Callable[[str, str, bytes], None]


class TapTransport:
    """Wraps a transport and reports each message: direction "out" (Pi to hub) or "in"."""

    def __init__(self, inner: Transport, tap: Tap):
        self._inner = inner
        self._tap = tap

    def start(self, on_message: OnMessage, subscriptions: list[str], on_connect: OnConnect | None = None) -> None:
        def tapped(topic: str, payload: bytes) -> None:
            self._tap("in", topic, payload)
            on_message(topic, payload)

        self._inner.start(tapped, subscriptions, on_connect)

    def publish(self, topic: str, payload: bytes | str, qos: int = 1, retain: bool = False) -> None:
        self._tap("out", topic, payload.encode() if isinstance(payload, str) else payload)
        self._inner.publish(topic, payload, qos=qos, retain=retain)

    def stop(self) -> None:
        self._inner.stop()


class MqttLog:
    def __init__(self, loop: asyncio.AbstractEventLoop, listener: Callable[[dict[str, Any]], None], size: int = 300):
        self._loop = loop
        self._listener = listener
        self.entries: deque[dict[str, Any]] = deque(maxlen=size)

    def record(self, direction: str, topic: str, payload: bytes) -> None:
        """Thread-safe: called from the MQTT thread as well as the event loop."""
        entry = {
            "t": time.time(),
            "dir": direction,
            "topic": topic,
            "payload": payload.decode(errors="replace"),
        }
        level = logging.DEBUG if topic == HUB_PROGRESS else logging.INFO
        log.log(level, "%s %s %s", "Pi -> hub" if direction == "out" else "hub -> Pi", topic, entry["payload"])
        if not self._loop.is_closed():
            self._loop.call_soon_threadsafe(self._add, entry)

    def _add(self, entry: dict[str, Any]) -> None:
        self.entries.append(entry)
        self._listener(entry)
