# CWAutoJuiceHub

An automatic cocktail and juice machine. A Raspberry Pi runs the touch UI, the cocktail
database and the logic; an ESP32 drives 12 dispenser bays and a glass lift, and stops each
pour by weight using a load cell. They talk MQTT. Design notes live in the
[wiki](https://github.com/Thiedze/CWAutoJuiceHub/wiki).

```
backend/    Raspberry Pi: FastAPI, SQLite, touch UI, hub simulator
firmware/   ESP32: PlatformIO + Arduino
docs/       protocol.md (MQTT contract), hardware.md (pins, calibration, network)
```

## Try it without hardware

The backend can run against a simulated hub inside the same process. No broker and no ESP32
are needed.

```sh
cd backend
python3 -m venv .venv
.venv/bin/pip install -e '.[dev]'
CWJH_MQTT_HOST=sim .venv/bin/uvicorn cwjh.main:app --port 8000
```

Open <http://localhost:8000>, tap the gear, fill a few bays, then make a drink.

## Tests

```sh
cd backend && .venv/bin/pytest           # backend, end to end against the simulator
cd firmware && pio test -e native        # firmware parsing and pour logic, on the host
```

## Running for real

On the Pi:

```sh
sudo apt install mosquitto
cd backend && CWJH_MQTT_HOST=localhost .venv/bin/uvicorn cwjh.main:app --host 0.0.0.0 --port 8000
chromium-browser --kiosk http://localhost:8000
```

To test against a real broker but still without the ESP32, also run
`.venv/bin/python -m cwjh.sim.hub_sim --host localhost`.

For the ESP32, copy `firmware/src/secrets.example.h` to `secrets.h`, fill it in, and run
`pio run -t upload`. Calibrate the scale and pour offsets as described in
[docs/hardware.md](docs/hardware.md).

## Settings

Environment variables for the backend:

| Variable | Default | |
|---|---|---|
| `CWJH_MQTT_HOST` | `localhost` | `sim` runs the in-process simulator |
| `CWJH_MQTT_PORT` | `1883` | |
| `CWJH_MQTT_USERNAME`, `CWJH_MQTT_PASSWORD` | empty | |
| `CWJH_DB_URL` | `sqlite:///cwjh.db` | |
| `CWJH_SEED` | `1` | load sample ingredients and recipes into an empty DB |
| `CWJH_ACK_TIMEOUT_S` | `30` | time limit for lift and tilt commands |
| `CWJH_MIN_FLOW_ML_S` | `5` | slowest expected pour in ml/s; sets the pour timeout |
