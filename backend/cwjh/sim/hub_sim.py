"""A fake ESP32 hub that speaks docs/protocol.md.

Run against a real broker:   python -m cwjh.sim.hub_sim --host localhost
The backend can also run it in-process with CWJH_MQTT_HOST=sim.
"""

import argparse
import json
import logging
import math
import threading
import time

from pydantic import ValidationError

from ..protocol import (
    BAY_COUNT,
    CMD_ALL,
    CMD_STATE,
    CMD_STOP,
    COMMANDS,
    HUB_ACK,
    HUB_ONLINE,
    HUB_PROGRESS,
    HUB_STATE,
    MAX_GLASS_ML,
    Ack,
    GlassInit,
    GlassRelease,
    HubState,
    Move,
    Pour,
    Progress,
)
from ..transport import Transport

log = logging.getLogger(__name__)

TICK_S = 0.05
PROGRESS_EVERY_TICKS = 4
STALL_S = 2.0
STALL_G = 2.0
GLASS_MIN_G = 50.0
GLASS_WEIGHT_G = 250.0


class _Stopped(Exception):
    pass


class HubSim:
    def __init__(self, transport: Transport, flow_ml_s: float = 20.0, time_scale: float = 1.0):
        self._t = transport
        self.flow_ml_s = flow_ml_s
        self.time_scale = time_scale
        # Liquid left in each bay's bottle; math.inf means never runs dry.
        self.bay_liquid_ml: dict[int, float] = {i: math.inf for i in range(1, BAY_COUNT + 1)}
        # Whether a glass is put on the scale when glass/init runs.
        self.glass_present = True
        self.state = HubState(fw="sim")
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._worker: threading.Thread | None = None
        self._gross_g = 0.0
        self._tare_g = 0.0

    def start(self) -> None:
        self._t.start(self._on_message, [CMD_ALL], on_connect=self._on_connect)

    def stop(self) -> None:
        self._stop.set()
        if self._worker:
            self._worker.join(timeout=2)
        self._t.stop()

    def _on_connect(self) -> None:
        self._t.publish(HUB_ONLINE, b"1", retain=True)
        self._publish_state()

    def _on_message(self, topic: str, payload: bytes) -> None:
        cls = COMMANDS.get(topic)
        if cls is None:
            return
        try:
            cmd = cls.model_validate_json(payload)
        except ValidationError:
            try:
                cmd_id = str(json.loads(payload).get("id", ""))
            except (json.JSONDecodeError, AttributeError):
                cmd_id = ""
            self._ack(cmd_id, "error", code="invalid")
            return

        if topic == CMD_STOP:
            self._stop.set()
            worker = self._worker
            if worker and worker is not threading.current_thread():
                worker.join(timeout=2)
            self._ack(cmd.id, "done")
            return
        if topic == CMD_STATE:
            self._publish_state()
            self._ack(cmd.id, "done")
            return

        with self._lock:
            if self.state.busy:
                self._ack(cmd.id, "error", code="busy")
                return
            self.state.busy = True
            self._stop.clear()
            self._worker = threading.Thread(target=self._run, args=(cmd,), daemon=True)
        self._ack(cmd.id, "accepted")
        self._publish_state()
        self._worker.start()

    def _run(self, cmd) -> None:
        # Handlers return the final ack; it is sent only after busy is cleared, so the Pi
        # can send the next command as soon as it sees "done".
        try:
            if isinstance(cmd, GlassInit):
                result = self._glass_init()
            elif isinstance(cmd, GlassRelease):
                result = self._glass_release()
            elif isinstance(cmd, Pour):
                result = self._pour(cmd)
            else:
                result = self._move(cmd)
        except _Stopped:
            result = ("error", "stopped", None)
        except Exception:
            log.exception("simulator command failed")
            result = ("error", "invalid", None)
        with self._lock:
            self.state.busy = False
        self._publish_state()
        self._ack(cmd.id, *result)

    def _sleep(self, sim_s: float) -> None:
        if self._stop.wait(sim_s * self.time_scale):
            raise _Stopped

    def _glass_init(self) -> tuple:
        self.state.lift = "moving"
        self._publish_state()
        self._sleep(0.5)
        self.state.lift = "up"
        self._gross_g = GLASS_WEIGHT_G if self.glass_present else 0.0
        self._tare_g = self._gross_g
        self.state.scale_g = 0.0
        self.state.poured_ml = 0.0
        self.state.glass = self._gross_g >= GLASS_MIN_G
        if not self.state.glass:
            return ("error", "no_glass", None)
        return ("done", None, None)

    def _glass_release(self) -> tuple:
        self.state.lift = "moving"
        self._publish_state()
        self._sleep(0.5)
        self.state.lift = "down"
        self.state.glass = False
        self._gross_g = self._tare_g = 0.0
        self.state.scale_g = 0.0
        return ("done", None, None)

    def _move(self, cmd: Move) -> tuple:
        current = self.state.bays[cmd.bay - 1]
        self._sleep(abs(cmd.degrees - current) / 60.0 + 0.1)
        self.state.bays[cmd.bay - 1] = cmd.degrees
        return ("done", None, None)

    def _pour(self, cmd: Pour) -> tuple:
        if not self.state.glass:
            return ("error", "no_glass", None)
        if self.state.poured_ml + cmd.ml > MAX_GLASS_ML + 0.01:
            return ("error", "limit", None)

        start_g = self._gross_g
        target_g = cmd.ml * cmd.g_per_ml
        last_gain_g, stall_s, ticks = 0.0, 0.0, 0

        def poured_g() -> float:
            return self._gross_g - start_g

        def finish(status: str, code: str | None = None) -> tuple:
            ml = round(poured_g() / cmd.g_per_ml, 1)
            self.state.poured_ml += ml
            return (status, code, ml)

        try:
            while poured_g() < target_g:
                self._sleep(TICK_S)
                ticks += 1
                left = self.bay_liquid_ml[cmd.bay]
                ml = min(self.flow_ml_s * TICK_S, left, (target_g - poured_g()) / cmd.g_per_ml)
                self.bay_liquid_ml[cmd.bay] = left - ml
                self._gross_g += ml * cmd.g_per_ml
                self.state.scale_g = round(self._gross_g - self._tare_g, 1)

                if poured_g() - last_gain_g >= STALL_G:
                    last_gain_g, stall_s = poured_g(), 0.0
                else:
                    stall_s += TICK_S
                    if stall_s >= STALL_S - 1e-9:
                        return finish("error", "bay_empty")
                if ticks % PROGRESS_EVERY_TICKS == 0:
                    self._progress(cmd.id, poured_g(), target_g)
        except _Stopped:
            return finish("error", "stopped")
        self._progress(cmd.id, poured_g(), target_g)
        return finish("done")

    def _ack(self, cmd_id: str, status: str, code: str | None = None, poured_ml: float | None = None) -> None:
        ack = Ack(id=cmd_id, status=status, code=code, poured_ml=poured_ml)
        self._t.publish(HUB_ACK, ack.model_dump_json(exclude_none=True).encode())

    def _progress(self, cmd_id: str, grams: float, target: float) -> None:
        msg = Progress(id=cmd_id, grams=round(grams, 1), target=round(target, 1))
        self._t.publish(HUB_PROGRESS, msg.model_dump_json().encode(), qos=0)

    def _publish_state(self) -> None:
        self._t.publish(HUB_STATE, self.state.model_dump_json().encode(), retain=True)


def main() -> None:
    from ..transport import PahoTransport

    parser = argparse.ArgumentParser(description="Simulated CWAutoJuiceHub ESP32")
    parser.add_argument("--host", default="localhost")
    parser.add_argument("--port", type=int, default=1883)
    parser.add_argument("--flow", type=float, default=20.0, help="pour speed in ml/s")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO)

    transport = PahoTransport(args.host, args.port, client_id="cwjh-hub-sim", will=(HUB_ONLINE, b"0"))
    sim = HubSim(transport, flow_ml_s=args.flow)
    sim.start()
    log.info("simulated hub running against %s:%s, Ctrl+C to quit", args.host, args.port)
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        transport.publish(HUB_ONLINE, b"0", retain=True)
        sim.stop()


if __name__ == "__main__":
    main()
