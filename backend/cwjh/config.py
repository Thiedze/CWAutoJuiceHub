"""Settings, read from environment variables with the CWJH_ prefix."""

import os
from dataclasses import dataclass


def _env(name: str, default: str) -> str:
    return os.environ.get(f"CWJH_{name}", default)


@dataclass(frozen=True)
class Settings:
    # USB serial port of the Nano: a device path, "auto" (first USB serial port) or "sim"
    # (in-process simulated Nano, no hardware needed).
    serial_port: str = "auto"
    # Number of motors the simulated Nano reports.
    sim_bays: int = 12
    db_url: str = "sqlite:///cwjh.db"
    seed: bool = True
    # Time limit for quick commands (move, glass init, glass release).
    ack_timeout_s: float = 30.0
    # Motion, see docs/hardware.md. 8.889 = 200 steps * 16 microsteps / 360 degrees.
    steps_per_degree: float = 8.889
    step_rate: int = 300  # steps/s; STEP over the SX1509 manages about 320
    # Flip every motor move. True matches the bay motor wired on the bench: positive Nano
    # steps turned it the wrong way for tilting.
    invert_direction: bool = True
    # A pour turns the bay this far from rest (opposite to the bottle tilt). Calibrate!
    pour_open_steps: int = -400
    # Until there is a scale, a pour is timed with this flow. Calibrate!
    pour_flow_ml_s: float = 10.0
    # Slowest expected flow; a pour of x ml gets 10 s + x / min_flow seconds (or the
    # timed flow, when that is slower).
    min_flow_ml_s: float = 5.0

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            serial_port=_env("SERIAL_PORT", cls.serial_port),
            sim_bays=int(_env("SIM_BAYS", str(cls.sim_bays))),
            db_url=_env("DB_URL", cls.db_url),
            seed=_env("SEED", "1") not in ("0", "false", "no"),
            ack_timeout_s=float(_env("ACK_TIMEOUT_S", str(cls.ack_timeout_s))),
            steps_per_degree=float(_env("STEPS_PER_DEGREE", str(cls.steps_per_degree))),
            step_rate=int(_env("STEP_RATE", str(cls.step_rate))),
            invert_direction=_env("INVERT_DIRECTION", "1") not in ("0", "false", "no"),
            pour_open_steps=int(_env("POUR_OPEN_STEPS", str(cls.pour_open_steps))),
            pour_flow_ml_s=float(_env("POUR_FLOW_ML_S", str(cls.pour_flow_ml_s))),
            min_flow_ml_s=float(_env("MIN_FLOW_ML_S", str(cls.min_flow_ml_s))),
        )

    def pour_timeout_s(self, ml: float) -> float:
        return 10.0 + ml / min(self.min_flow_ml_s, self.pour_flow_ml_s)
