"""A fake Nano that speaks docs/protocol.md in-process.

Used with CWJH_SERIAL_PORT=sim and in the tests. Moves take |distance| / speed seconds,
scaled by time_scale, and can be stopped half way like on the real Nano.

The motors have a physical position that survives a reset of the Nano (opening the port),
while the Nano's own positions start at 0 again. A home switch is pressed while a bay's
physical position is within `home_width / 2` of `home_at`.
"""

import queue
import threading
import time


class NanoSim:
    def __init__(
        self,
        bays: int = 12,
        steps_per_s: int = 300,
        time_scale: float = 1.0,
        fw: str = "sim",
        home_at: dict[int, int] | None = None,
        home_width: int = 20,
    ):
        self.bays = bays
        self.fw = fw
        self.steps_per_s = steps_per_s  # default top speed of every bay
        self.profiles: dict[int, tuple[int, int, int, int]] = {}  # bay -> last P (max, min, start, end)
        self.time_scale = time_scale
        self.positions = [0] * bays  # what the Nano believes
        self.physical = [0] * bays  # where the motors really are
        self.home_at = dict(home_at or {})  # bay -> physical position of its home switch
        self.home_width = home_width
        self.busy = False
        self.received: list[str] = []  # every line from the Pi, for tests
        self.plugged = True  # False: the USB cable is pulled
        self.mute = False  # True: alive on USB but never answers (hung firmware)
        self.send_ready = True  # False: no READY after open, like a Nano that did not reset

        self._lock = threading.Lock()
        self._out: queue.Queue[str] = queue.Queue()
        self._stop = threading.Event()
        self._stop_seq: int | None = None
        self._worker: threading.Thread | None = None

    def line(self) -> "SimLine":
        return SimLine(self)

    def unplug(self) -> None:
        self.plugged = False
        self._abort_move()

    def plug(self) -> None:
        self.plugged = True

    def switch(self, bay: int) -> int | None:
        """1 pressed, 0 released, None if the bay has no home switch."""
        if bay not in self.home_at:
            return None
        return int(abs(self.physical[bay - 1] - self.home_at[bay]) <= self.home_width / 2)

    # Called through SimLine

    def _open(self) -> None:
        self._check_plugged()
        self._abort_move()
        with self._lock:
            self.positions = [0] * self.bays  # opening the port resets the Nano
            self.profiles = {}
            self.busy = False
            self._out = queue.Queue()
        if self.send_ready:
            self._reply(f"READY {self.fw} {self.bays}")

    def _read(self, timeout_s: float) -> str | None:
        self._check_plugged()
        try:
            return self._out.get(timeout=timeout_s)
        except queue.Empty:
            return None

    def _write(self, line: str) -> None:
        self._check_plugged()
        self.received.append(line)
        if not self.mute:
            self._handle(line)

    def _check_plugged(self) -> None:
        if not self.plugged:
            raise OSError("simulated Nano unplugged")

    # Protocol

    def _reply(self, line: str) -> None:
        self._out.put(line)

    def _handle(self, line: str) -> None:
        words = line.split()
        if not words or not words[0].isdigit():
            return
        seq = int(words[0])
        op, args = (words[1], words[2:]) if len(words) > 1 else ("", [])

        with self._lock:
            if op == "S":
                if self.busy:
                    self._stop_seq = seq
                    self._stop.set()
                else:
                    self._reply(f"{seq} OK")
                return
            if op == "Q":
                self._reply(f"{seq} STATE {int(self.busy)} {' '.join(map(str, self.positions))}".rstrip())
                return
            if op == "W":
                states = [self.switch(bay) for bay in range(1, self.bays + 1)]
                self._reply(f"{seq} SW " + " ".join("-" if s is None else str(s) for s in states))
                return
            if self.busy:
                self._reply(f"{seq} ERR busy")
                return
            try:
                values = [int(a) for a in args]
            except ValueError:
                self._reply(f"{seq} ERR invalid")
                return

            bay = values[0] if values else 0
            if op == "P" and len(values) == 5 and 1 <= bay <= 12:
                vmax, vmin, start, end = values[1:]
                if bay > self.bays:
                    self._reply(f"{seq} ERR no_motor")
                elif not (1 <= vmax <= 5000 and 1 <= vmin <= vmax and 0 <= start <= 10000 and 0 <= end <= 10000):
                    self._reply(f"{seq} ERR invalid")
                else:
                    self.profiles[bay] = (vmax, vmin, start, end)
                    self._reply(f"{seq} OK")
            elif op in ("G", "H", "F", "Z") and len(values) == {"G": 2, "H": 2, "F": 3, "Z": 1}[op] and 1 <= bay <= 12:
                if bay > self.bays:
                    self._reply(f"{seq} ERR no_motor")
                elif op == "H":
                    self._reply(f"{seq} OK")
                elif op == "Z":
                    self.positions[bay - 1] = 0
                    self._reply(f"{seq} OK")
                elif op == "F" and bay not in self.home_at:
                    self._reply(f"{seq} ERR no_switch")
                else:
                    level = values[2] if op == "F" else None
                    self.busy = True
                    self._stop.clear()
                    self._worker = threading.Thread(
                        target=self._move, args=(seq, bay, values[1], level), daemon=True
                    )
                    self._worker.start()
            else:
                self._reply(f"{seq} ERR invalid")

    def _switch_stop(self, bay: int, target: int, level: int) -> int | None:
        """First Nano position on the way to target where the switch reads level, if any."""
        index = bay - 1
        offset = self.physical[index] - self.positions[index]
        step = 1 if target >= self.positions[index] else -1
        pos = self.positions[index]
        while True:
            if int(abs(pos + offset - self.home_at[bay]) <= self.home_width / 2) == level:
                return pos
            if pos == target:
                return None
            pos += step

    def _move(self, seq: int, bay: int, target: int, level: int | None) -> None:
        index = bay - 1
        found = False
        if level is not None:
            stop_at = self._switch_stop(bay, target, level)
            if stop_at is not None:
                target, found = stop_at, True

        start = self.positions[index]
        offset = self.physical[index] - start
        # The curves are not simulated: the whole move runs at the top speed.
        speed = self.profiles.get(bay, (self.steps_per_s,))[0]
        duration = abs(target - start) / speed * self.time_scale
        began = time.monotonic()
        while True:
            elapsed = time.monotonic() - began
            done = duration <= 0 or elapsed >= duration
            fraction = 1.0 if done else elapsed / duration
            with self._lock:
                self.positions[index] = round(start + (target - start) * fraction)
                self.physical[index] = self.positions[index] + offset
            if done:
                break
            if self._stop.wait(min(0.01, duration - elapsed)):
                with self._lock:
                    self.busy = False
                    if self.plugged:
                        self._reply(f"{seq} ERR stopped")
                        if self._stop_seq is not None:
                            self._reply(f"{self._stop_seq} OK")
                    self._stop_seq = None
                return
        with self._lock:
            self.busy = False
            self._reply(f"{seq} OK {target}" + ("" if level is None else f" {int(found)}"))

    def _abort_move(self) -> None:
        worker = self._worker
        if worker and worker.is_alive():
            self._stop_seq = None
            self._stop.set()
            worker.join(timeout=1)


class SimLine:
    """The NanoLink side of a NanoSim, in place of a SerialLine."""

    def __init__(self, sim: NanoSim, read_timeout_s: float = 0.05):
        self.sim = sim
        self._read_timeout_s = read_timeout_s

    def open(self) -> None:
        self.sim._open()

    def readline(self) -> str | None:
        return self.sim._read(self._read_timeout_s)

    def write(self, line: str) -> None:
        self.sim._write(line)

    def close(self) -> None:
        pass
