# Hardware

## Overview

- **Raspberry Pi:** runs the backend, the touch UI (Chromium kiosk) and all machine logic.
  During development a laptop takes its place.
- **Arduino Nano** (ATmega328P), connected to the Pi over USB: moves the stepper motors on
  request ([protocol](protocol.md)). Nothing else.
- **SX1509 I/O expander** (SparkFun breakout) on the Nano's I2C bus: drives STEP, DIR and the
  ENABLE line of each bay driver.
- **DRV8825 stepper driver** per bay, one stepper motor per bay.

Wired today: one bay. Planned: 12 liquid dispensers in 4 quarters of 3, a lift stepper that
raises the glass onto a load cell (HX711), and a LED per bay.

## Wiring

### Nano to SX1509

| Nano | SX1509 |
|---|---|
| A4 (SDA) | SDA |
| A5 (SCL) | SCL |
| 3V3 | 3V3 |
| GND | GND |

The SX1509 runs on 3.3 V. Its address is 0x3E with the ADDR jumpers untouched.

### SX1509 to DRV8825

| SX1509 I/O | DRV8825 | |
|---|---|---|
| 1 | STEP | shared by all bays |
| 2 | DIR | shared by all bays |
| 3 | ENABLE of bay 1 | active low; one I/O per bay |

STEP and DIR are shared because only one bay moves at a time. A bay is selected by switching
on only its driver (ENABLE low). To add a bay, wire its ENABLE to a free SX1509 I/O and add
that number to `BAY_ENABLE` in `firmware/src/config.h`.

Every STEP pulse is two I2C writes to the SX1509, so the step rate is limited to about 320
steps per second (`I2C_CLOCK` 100 kHz; 400 kHz gives roughly three times that).

### DRV8825

Following the Pololu wiring diagram:

| DRV8825 pin | Connect to |
|---|---|
| RESET, SLEEP | bridged together, to 3.3 V |
| VMOT, GND (top right) | motor supply, **at least 8.2 V**, with a 100 µF capacitor across it |
| GND (bottom right) | the same ground as the Nano and the SX1509 |
| B2, B1, A1, A2 | the two motor coils (a coil pair measures a few ohms) |
| M0, M1, M2 | microstep mode (see Calibration) |
| FAULT | leave free; the DRV8825 has no logic supply pin |

- Below 8.2 V on VMOT the driver stays off.
- Without the common ground the driver never sees the STEP pulses.
- Set the current limit with the potentiometer before running the motor.
- Never connect or disconnect the motor while the driver is powered.

## Nano firmware

```sh
cd firmware
pio run -t upload          # the clone used here has the old bootloader (env nano_oldboot)
pio run -e nano -t upload  # a Nano with the new bootloader
pio test -e native         # parser tests on the host
```

Notes from the bring-up:

- Many micro-USB cables only carry power. If the Nano lights up but no
  `/dev/cu.usbserial-*` (macOS) or `/dev/ttyUSB*` (Linux) appears, try another cable.
- If the upload times out with `not in sync`, use the other bootloader env.
- `firmware/bringup/nano-stepper` is a standalone sketch for checking the wiring by hand.
  Its `x` command drives STEP, DIR and ENABLE high and low and reads back the real pin
  level.

## Calibration

These are backend settings (environment variables, see the README):

- **`CWJH_INVERT_DIRECTION`**: if filling a bay tilts it the wrong way, flip this (`1` or `0`).
  It flips every move, so the pour stays opposite to the tilt. The default `1` matches the
  bay motor on the bench. Swapping the wires of one motor coil has the same effect.
- **`CWJH_STEPS_PER_DEGREE`**: motor steps per degree of bay tilt. The default 8.889 assumes
  1/16 microsteps (200 steps x 16 / 360). Command a 30 degree tilt (fill a bay in the UI),
  measure the real angle, and scale the value.
- **`CWJH_POUR_OPEN_STEPS`**: how far the bay turns from rest to pour, opposite to the tilt
  direction. The default -400 is an assumption; check the direction and the angle on the
  machine.
- **`CWJH_POUR_FLOW_ML_S`**: until there is a scale, a pour is timed. Pour for a known time
  into a measuring cup and divide the volume by the time.
- **`CWJH_STEP_RATE`**: steps per second. Above about 320 the SX1509 cannot keep up and the
  motor just runs at its limit.

Position 0 of every bay is wherever the motor is when the Nano boots, and opening the
serial port reboots the Nano. Leave the bays at rest when stopping the backend.
