"""USB serial link to the Nano motor controller. See docs/protocol.md.

The Nano only moves motors. NanoLink numbers each request, matches the replies by that
number, notices when the Nano goes away (USB unplugged, no answer to pings) and
reconnects on its own.
"""

import asyncio
import glob
import logging
import threading
import time
from collections.abc import Callable
from typing import Protocol

log = logging.getLogger(__name__)

# Sequence number of the Q probe sent when a Nano does not say READY after the port opens.
PROBE_SEQ = 0
MAX_SEQ = 65535

AUTO_PATTERNS = (
    "/dev/cu.usbserial-*",
    "/dev/cu.wchusbserial*",
    "/dev/cu.usbmodem*",
    "/dev/ttyUSB*",
    "/dev/ttyACM*",
)

Tap = Callable[[str, str], None]  # direction ("out" = Pi to Nano, "in"), line
OnlineListener = Callable[[bool], None]


class NanoError(Exception):
    """The Nano answered ERR <code>, or the link is down ("offline")."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


class Line(Protocol):
    """A line-based byte stream to the Nano. Methods raise OSError when the device is gone."""

    def open(self) -> None: ...

    def readline(self) -> str | None:
        """One line without its ending, or None after a short wait without a full line."""
        ...

    def write(self, line: str) -> None: ...

    def close(self) -> None: ...


def find_port(spec: str) -> str:
    if spec != "auto":
        return spec
    for pattern in AUTO_PATTERNS:
        matches = sorted(glob.glob(pattern))
        if matches:
            return matches[0]
    raise OSError("no USB serial port found")


class SerialLine:
    def __init__(self, port: str = "auto", baud: int = 115200, read_timeout_s: float = 0.2):
        self._spec = port
        self._baud = baud
        self._read_timeout_s = read_timeout_s
        self._serial = None
        self._buf = b""
        self.port: str | None = None

    def open(self) -> None:
        import serial

        self.port = find_port(self._spec)
        # Opening the port resets the Nano (DTR); it answers with READY after its boot.
        self._serial = serial.Serial(self.port, self._baud, timeout=self._read_timeout_s)
        self._buf = b""

    def readline(self) -> str | None:
        chunk = self._serial.read_until(b"\n")
        self._buf += chunk
        if not self._buf.endswith(b"\n"):
            return None
        line, self._buf = self._buf, b""
        return line.decode(errors="replace").strip()

    def write(self, line: str) -> None:
        self._serial.write(f"{line}\n".encode())

    def close(self) -> None:
        if self._serial is not None:
            try:
                self._serial.close()
            except OSError:
                pass
            self._serial = None


class NanoLink:
    def __init__(
        self,
        line: Line,
        tap: Tap | None = None,
        ready_timeout_s: float = 4.0,
        ping_s: float = 5.0,
        ping_timeout_s: float = 3.0,
        retry_s: float = 1.0,
    ):
        self._line = line
        self._tap = tap or (lambda _direction, _line: None)
        self._ready_timeout_s = ready_timeout_s
        self._ping_s = ping_s
        self._ping_timeout_s = ping_timeout_s
        self._retry_s = retry_s

        self.online = False
        self.fw = ""
        self.bays = 0

        self._listeners: list[OnlineListener] = []
        self._pending: dict[int, asyncio.Future[list[str]]] = {}
        self._seq = 0
        self._write_lock = threading.Lock()
        self._shutdown = threading.Event()
        self._reconnect = threading.Event()
        self._thread: threading.Thread | None = None
        self._ping_task: asyncio.Task | None = None
        self._loop: asyncio.AbstractEventLoop | None = None

    def add_listener(self, listener: OnlineListener) -> None:
        """Called with True after each (re)connect, including a Nano reset, and False when lost."""
        self._listeners.append(listener)

    async def start(self) -> None:
        self._loop = asyncio.get_running_loop()
        self._thread = threading.Thread(target=self._run, name="nano-link", daemon=True)
        self._thread.start()
        self._ping_task = asyncio.create_task(self._ping_loop())

    def stop(self) -> None:
        self._shutdown.set()
        if self._ping_task:
            self._ping_task.cancel()
        if self._thread:
            self._thread.join(timeout=2)
        self._fail_pending("offline")

    async def request(self, text: str, timeout_s: float) -> list[str]:
        """Send "<seq> <text>" and return the reply words after the sequence number.

        Raises NanoError on ERR or when offline, TimeoutError when no reply came in time.
        """
        if not self.online:
            raise NanoError("offline")
        assert self._loop is not None, "NanoLink.start() was not called"
        seq = self._next_seq()
        future: asyncio.Future[list[str]] = self._loop.create_future()
        self._pending[seq] = future
        try:
            self._write(f"{seq} {text}")
            return await asyncio.wait_for(future, timeout_s)
        finally:
            self._pending.pop(seq, None)

    def _next_seq(self) -> int:
        self._seq = self._seq % MAX_SEQ + 1  # 1..65535, 0 is the probe
        return self._seq

    def _write(self, line: str) -> None:
        with self._write_lock:
            self._tap("out", line)
            try:
                self._line.write(line)
            except OSError:
                self._reconnect.set()
                raise NanoError("offline") from None

    # Reader thread

    def _run(self) -> None:
        reported = False
        while not self._shutdown.is_set():
            try:
                self._line.open()
            except OSError as e:
                if not reported:
                    log.warning("Nano not connected: %s", e)
                    reported = True
                self._shutdown.wait(self._retry_s)
                continue
            reported = False
            try:
                self._session()
            except OSError as e:
                log.warning("Nano link lost: %s", e)
            finally:
                self._line.close()
                self._to_loop(self._set_online, False, "", 0)
            self._shutdown.wait(self._retry_s)

    def _session(self) -> None:
        self._reconnect.clear()
        connected = False
        probe_at = time.monotonic() + self._ready_timeout_s
        while not self._shutdown.is_set() and not self._reconnect.is_set():
            line = self._line.readline()
            if line is None:
                if not connected and probe_at and time.monotonic() > probe_at:
                    # No READY: the Nano may not have reset on open. Ask it directly.
                    probe_at = 0
                    with self._write_lock:
                        self._tap("out", f"{PROBE_SEQ} Q")
                        self._line.write(f"{PROBE_SEQ} Q")
                continue
            if not line:
                continue
            self._tap("in", line)
            words = line.split()

            if words[0] == "READY" and len(words) >= 3 and words[2].isdigit():
                # Also after an unexpected reset: every position is back at 0.
                connected = True
                self._to_loop(self._set_online, True, words[1], int(words[2]))
                continue
            if not words[0].isdigit() or len(words) < 2:
                log.warning("Nano says: %s", line)
                continue

            seq = int(words[0])
            if seq == PROBE_SEQ and words[1] == "STATE" and not connected:
                connected = True
                self._to_loop(self._set_online, True, "unknown", len(words) - 3)
                continue
            self._to_loop(self._resolve, seq, words[1:])

        if self._reconnect.is_set():
            raise OSError("no answer, reconnecting")

    # Event loop side

    def _to_loop(self, fn: Callable, *args) -> None:
        loop = self._loop
        if loop is not None and not loop.is_closed():
            loop.call_soon_threadsafe(fn, *args)

    def _set_online(self, online: bool, fw: str, bays: int) -> None:
        was_online = self.online
        if was_online:
            # Lost, or the Nano reset: nothing that was in flight will be answered.
            self._fail_pending("offline")
        if not online and not was_online:
            return
        self.online = online
        if online:  # offline keeps the last known firmware and bay count
            self.fw = fw
            self.bays = bays
        if online:
            log.info("Nano online: firmware %s, %d bay(s)", fw, bays)
        else:
            log.warning("Nano offline")
        for listener in self._listeners:
            try:
                listener(online)
            except Exception:
                log.exception("Nano link listener failed")

    def _resolve(self, seq: int, words: list[str]) -> None:
        future = self._pending.get(seq)
        if future is None or future.done():
            return
        if words[0] == "ERR":
            future.set_exception(NanoError(words[1] if len(words) > 1 else "error"))
        else:
            future.set_result(words)

    def _fail_pending(self, code: str) -> None:
        for future in self._pending.values():
            if not future.done():
                future.set_exception(NanoError(code))

    async def _ping_loop(self) -> None:
        while True:
            await asyncio.sleep(self._ping_s)
            if not self.online:
                continue
            try:
                await self.request("Q", self._ping_timeout_s)
            except TimeoutError:
                log.warning("Nano did not answer a ping, reconnecting")
                self._reconnect.set()
            except NanoError:
                pass
