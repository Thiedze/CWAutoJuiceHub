"""A fake Nano that speaks docs/protocol.md in-process.

Used with CWJH_SERIAL_PORT=sim and in the tests. Moves take |distance| / speed seconds,
scaled by time_scale, and can be stopped half way like on the real Nano.
"""

import queue
import threading
import time


class NanoSim:
    def __init__(self, bays: int = 12, steps_per_s: int = 300, time_scale: float = 1.0, fw: str = "sim"):
        self.bays = bays
        self.fw = fw
        self.steps_per_s = steps_per_s
        self.time_scale = time_scale
        self.positions = [0] * bays
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

    # Called through SimLine

    def _open(self) -> None:
        self._check_plugged()
        self._abort_move()
        with self._lock:
            self.positions = [0] * self.bays  # opening the port resets the Nano
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
            if self.busy:
                self._reply(f"{seq} ERR busy")
                return
            try:
                values = [int(a) for a in args]
            except ValueError:
                self._reply(f"{seq} ERR invalid")
                return

            if op == "V" and len(values) == 1 and 1 <= values[0] <= 5000:
                self.steps_per_s = values[0]
                self._reply(f"{seq} OK")
            elif op in ("G", "H") and len(values) == 2 and 1 <= values[0] <= 12:
                if values[0] > self.bays:
                    self._reply(f"{seq} ERR no_motor")
                elif op == "H":
                    self._reply(f"{seq} OK")
                else:
                    self.busy = True
                    self._stop.clear()
                    self._worker = threading.Thread(target=self._move, args=(seq, values[0], values[1]), daemon=True)
                    self._worker.start()
            else:
                self._reply(f"{seq} ERR invalid")

    def _move(self, seq: int, bay: int, target: int) -> None:
        index = bay - 1
        start = self.positions[index]
        duration = abs(target - start) / self.steps_per_s * self.time_scale
        began = time.monotonic()
        while True:
            elapsed = time.monotonic() - began
            done = duration <= 0 or elapsed >= duration
            fraction = 1.0 if done else elapsed / duration
            with self._lock:
                self.positions[index] = round(start + (target - start) * fraction)
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
            self._reply(f"{seq} OK {target}")

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
