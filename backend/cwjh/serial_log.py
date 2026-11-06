"""Records every line between the Pi and the Nano, for the UI's serial panel."""

import asyncio
import logging
import time
from collections import deque
from collections.abc import Callable
from typing import Any

log = logging.getLogger("cwjh.serial")


def is_status(line: str) -> bool:
    """Q requests and STATE replies: mostly the liveness pings."""
    words = line.split()
    return len(words) >= 2 and words[1] in ("Q", "STATE")


class SerialLog:
    def __init__(self, loop: asyncio.AbstractEventLoop, listener: Callable[[dict[str, Any]], None], size: int = 300):
        self._loop = loop
        self._listener = listener
        self.entries: deque[dict[str, Any]] = deque(maxlen=size)

    def record(self, direction: str, line: str) -> None:
        """Thread-safe: called from the serial reader thread as well as the event loop."""
        entry = {"t": time.time(), "dir": direction, "line": line}
        level = logging.DEBUG if is_status(line) else logging.INFO
        log.log(level, "%s %s", "Pi -> Nano" if direction == "out" else "Nano -> Pi", line)
        if not self._loop.is_closed():
            self._loop.call_soon_threadsafe(self._add, entry)

    def _add(self, entry: dict[str, Any]) -> None:
        self.entries.append(entry)
        self._listener(entry)
