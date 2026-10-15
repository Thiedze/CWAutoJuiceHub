# Hardware

## Overview

- Raspberry Pi: runs the MQTT broker, the backend and the touch UI (Chromium kiosk).
- ESP32 DevKit: drives the motors, reads the scale, talks MQTT.
- 12 liquid dispensers in 4 quarters of 3. Each dispenser has one stepper motor and one LED.
- 1 lift stepper that raises the glass onto the scale.
- 1 load cell with an HX711 amplifier.

## Pin budget

The wiki ADR ("step motor control") counts 13 motors at 2 pins each, so 26 pins. An ESP32
DevKit only has about 21 GPIOs that are safe as outputs, and the HX711 and the 12 LEDs need
more. So the firmware uses this wiring instead (**assumption, confirm before wiring**):

- Only one bay pours or tilts at a time, so the **12 dispenser drivers share one STEP and one
  DIR line**. Each driver is selected through its own ENABLE input.
- The 12 ENABLE lines and the 12 LEDs are driven by a chain of **4x 74HC595** shift registers
  (32 outputs, 24 used), which needs 3 GPIOs.
- The lift has its own STEP, DIR and ENABLE.
- The HX711 needs DATA and CLOCK.

That is 10 GPIOs in total.

| Function | GPIO |
|---|---|
| Bay STEP (shared) | 25 |
| Bay DIR (shared) | 26 |
| Lift STEP | 32 |
| Lift DIR | 33 |
| Lift ENABLE | 27 |
| 74HC595 DATA (SER) | 23 |
| 74HC595 CLOCK (SRCLK) | 18 |
| 74HC595 LATCH (RCLK) | 5 |
| HX711 DATA (DOUT) | 16 |
| HX711 CLOCK (SCK) | 17 |

The pins are set in `firmware/src/config.h`.

### Shift register outputs

| Output | Function |
|---|---|
| Q0..Q11 | ENABLE of bay 1..12 (active low, as on A4988 and DRV8825 drivers) |
| Q16..Q27 | LED of bay 1..12 (active high) |
| others | unused |

Outputs 12..15 are left free so each bay's LED is at `16 + bay index`, which keeps the wiring
easy to follow.

## Calibration

Set in `firmware/src/config.h`:

- `SCALE_FACTOR`: HX711 raw counts per gram. Put a known weight on the scale and divide.
- `BAY_STEPS_PER_DEGREE`: motor steps per degree of bay tilt.
- `INFLIGHT_OFFSET_G`: liquid that still falls after the motor stops. The pour stops at
  `target - INFLIGHT_OFFSET_G`. Measure by pouring and comparing the final weight to the target.
- `GLASS_MIN_G`: minimum weight that counts as a glass.

## Network

The Pi runs Mosquitto. Install it on the Pi (`sudo apt install mosquitto`) and allow
anonymous clients on the local network, or set a user and password and put them into
`firmware/src/secrets.h`.

If no Wi-Fi is available, the Pi opens a hotspot that the ESP32 joins (see the wiki). With
NetworkManager on Raspberry Pi OS:

```sh
sudo nmcli device wifi hotspot ifname wlan0 ssid CWAutoJuiceHub password <password>
```

The Pi is then reachable at `10.42.0.1`. Set that as `MQTT_HOST` in `secrets.h`.
