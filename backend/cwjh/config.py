"""Settings, read from environment variables with the CWJH_ prefix."""

import os
from dataclasses import dataclass


def _env(name: str, default: str) -> str:
    return os.environ.get(f"CWJH_{name}", default)


@dataclass(frozen=True)
class Settings:
    mqtt_host: str = "localhost"
    mqtt_port: int = 1883
    mqtt_username: str | None = None
    mqtt_password: str | None = None
    db_url: str = "sqlite:///cwjh.db"
    seed: bool = True
    # Time limit for quick commands (move, glass init, glass release).
    ack_timeout_s: float = 30.0
    # Slowest expected flow; a pour of x ml gets 10 s + x / min_flow seconds.
    min_flow_ml_s: float = 5.0

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            mqtt_host=_env("MQTT_HOST", cls.mqtt_host),
            mqtt_port=int(_env("MQTT_PORT", str(cls.mqtt_port))),
            mqtt_username=_env("MQTT_USERNAME", "") or None,
            mqtt_password=_env("MQTT_PASSWORD", "") or None,
            db_url=_env("DB_URL", cls.db_url),
            seed=_env("SEED", "1") not in ("0", "false", "no"),
            ack_timeout_s=float(_env("ACK_TIMEOUT_S", str(cls.ack_timeout_s))),
            min_flow_ml_s=float(_env("MIN_FLOW_ML_S", str(cls.min_flow_ml_s))),
        )

    def pour_timeout_s(self, ml: float) -> float:
        return 10.0 + ml / self.min_flow_ml_s
