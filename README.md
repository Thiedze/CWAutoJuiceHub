# CWAutoJuiceHub

An automatic cocktail and juice machine. A Raspberry Pi runs the touch UI, the cocktail
database and all the machine logic. An Arduino Nano, plugged into the Pi over USB, only
moves the stepper motors of the dispenser bays when the Pi tells it to. Design notes live in
the [wiki](https://github.com/Thiedze/CWAutoJuiceHub/wiki).

```
backend/    Raspberry Pi: FastAPI, SQLite, touch UI, machine logic, simulated Nano
firmware/   Arduino Nano motor controller: PlatformIO + Arduino
            bringup/nano-stepper: standalone wiring test for one stepper
docs/       protocol.md (serial protocol Pi <-> Nano), hardware.md (wiring, calibration)
```

The first version drives one bay. The lift, the scale and the other bays come later; until
the scale is there, a pour is timed.

## Try it without hardware

The backend can run against a simulated Nano inside the same process.

```sh
cd backend
python3 -m venv .venv
.venv/bin/pip install -e '.[dev]'
CWJH_SERIAL_PORT=sim .venv/bin/uvicorn cwjh.main:app --port 8000
```

Open <http://localhost:8000>, tap the gear, fill a few bays, then make a drink. Add
`?serial` to the URL to see every line sent to and from the Nano.

## Tests

```sh
cd backend && .venv/bin/pytest           # backend, end to end against the simulated Nano
cd firmware && pio test -e native        # firmware line parser, on the host
```

## Running for real

Flash the Nano once (see [docs/hardware.md](docs/hardware.md) for wiring):

```sh
cd firmware && pio run -t upload
```

Then plug the Nano into the Pi (or, for testing, into your computer) and start the backend.
It finds the Nano on the first USB serial port:

```sh
cd backend && .venv/bin/uvicorn cwjh.main:app --host 0.0.0.0 --port 8000
chromium-browser --kiosk http://localhost:8000
```

Calibrate the motion and the timed pour as described in [docs/hardware.md](docs/hardware.md).

## Settings

Environment variables for the backend:

| Variable | Default | |
|---|---|---|
| `CWJH_SERIAL_PORT` | `auto` | Nano's serial port: `auto` (first USB serial port), a path such as `/dev/ttyUSB0`, or `sim` |
| `CWJH_SIM_BAYS` | `12` | motors the simulated Nano reports |
| `CWJH_DB_URL` | `sqlite:///cwjh.db` | |
| `CWJH_SEED` | `1` | load sample ingredients and recipes into an empty DB |
| `CWJH_ACK_TIMEOUT_S` | `30` | time limit for a tilt and for each motor move |
| `CWJH_STEPS_PER_DEGREE` | `8.889` | motor steps per degree of bay tilt (200 steps x 16 microsteps / 360) |
| `CWJH_STEP_RATE` | `300` | steps per second; the SX1509 manages about 320 |
| `CWJH_INVERT_DIRECTION` | `1` | flip every motor move; `1` matches the bench wiring, `0` if the bay tilts the wrong way |
| `CWJH_POUR_OPEN_STEPS` | `-400` | how far a pour turns the bay from rest |
| `CWJH_POUR_FLOW_ML_S` | `10` | flow of the timed pour, in ml/s |
| `CWJH_MIN_FLOW_ML_S` | `5` | slowest expected pour in ml/s; sets the pour timeout |
