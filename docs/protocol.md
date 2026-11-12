# Serial protocol

This is the contract between the Raspberry Pi (backend) and the Arduino Nano (motor
controller). The Nano only moves motors; every decision (angles, pour times, limits) is
made by the backend in `backend/cwjh/hub.py`.

## Transport

- USB serial, 115200 baud, 8N1.
- ASCII lines ending in `\n` (the Nano sends `\r\n`; the backend strips it).
- Opening the port resets the Nano (DTR). It boots in about 1.6 s and then sends `READY`.
  After a reset every position is 0, so the backend reads the positions again.

## Requests (Pi to Nano)

Every request starts with a sequence number `<n>` (1..65535) chosen by the Pi. The reply
carries the same number. `0` is reserved for the probe described below.

| Request | Reply | Meaning |
|---|---|---|
| `<n> G <bay> <pos>` | `<n> OK <pos>` | Move bay (1..12) to an absolute position in steps (-100000..100000). The reply comes when the move is done. |
| `<n> F <bay> <pos> <level>` | `<n> OK <pos> <found>` | Find: like `G`, but stop as soon as the bay's home switch reads `level` (1 pressed, 0 released). The switch is checked before every step, also before the first. `found` is 1 when the move ended at the switch, 0 when it reached `pos`. |
| `<n> Z <bay>` | `<n> OK` | Zero: the current position of bay becomes 0. |
| `<n> W` | `<n> SW <s1> ... <sN>` | Home switch of each wired bay: `1` pressed, `0` released, `-` no switch. |
| `<n> S` | `<n> OK` | Stop: disable every driver at once. |
| `<n> Q` | `<n> STATE <busy> <pos1> ... <posN>` | Busy flag (0/1) and the position of each wired bay. |
| `<n> V <steps/s>` | `<n> OK` | Step rate, 1..5000. Through the SX1509 the Nano manages about 320. |
| `<n> H <bay> <0/1>` | `<n> OK` | Hold: keep the bay's driver on (torque) even at rest. |

Any request can also be answered with `<n> ERR <code>`:

| Code | Meaning |
|---|---|
| `invalid` | Unknown request or a value out of range. |
| `busy` | A move is running. Also when another bay must keep its torque, because the bays share one STEP line. |
| `no_motor` | The bay has no motor wired (see `BAY_ENABLE` in `firmware/src/config.h`). |
| `no_switch` | `F` for a bay without a home switch (see `BAY_HOME`). |
| `stopped` | The move was cancelled by `S`. |

A line without a usable sequence number gets no reply.

### While a move runs

The Nano reads serial between steps:

- `Q` and `W` are answered right away (`busy` is 1).
- `S` stops the move. The move answers `<n> ERR stopped`, then the stop answers `<m> OK`.
- Everything else is answered with `ERR busy`.

### Torque

A bay's driver is on while it moves, while it is away from position 0 (so a tilted bottle
stays put), and while hold is on. `S` turns every driver off and clears hold; the positions
are kept, but a loaded bay may slip.

## Nano to Pi without a request

| Line | When |
|---|---|
| `READY <fw> <bays>` | After boot. `bays` is the number of wired bays. |
| `FAIL sx1509` | Every second if the SX1509 does not answer on I2C. |

If no `READY` arrives within 4 s after the port opened (a Nano that did not reset), the
backend sends `0 Q` and takes the number of positions in the reply as the bay count.

## Liveness

The backend sends `Q` every 5 s. If the Nano does not answer within 3 s, or the USB port
goes away, the backend reports the hub offline, fails the running command with `offline`,
and reopens the port every second until the Nano is back.

## Homing

Each bay can have a home switch that is pressed when the bay is at rest. After every
(re)connect the backend homes each bay that `W` reports a switch for, before it reports the
hub online:

1. Slow down to `home_step_rate` (`V`).
2. If the switch is released, search in growing steps to both sides with `F ... 1`:
   ±50, ±100, ±200, ±400 steps, up to `home_search_steps` (600) from where the bay stood.
   If it is not found, the bay goes back to where it was and is reported as not homed.
3. On the switch: leave it toward the tilt side with `F ... 0`, then come back with `F ... 1`.
   The position where it presses again is the edge seen from the tilt side; `Z` makes it 0.
4. Restore the step rate (`V`).

The result is in the hub state (`homed`: true, false, or null for no switch). A bay without
a switch keeps the position it had when the Nano booted.

## How the backend uses it

| Backend command | Nano requests |
|---|---|
| Tilt a bay for filling (`Move`, 0..30 degrees) | `G <bay> <degrees x steps_per_degree>`, sign flipped when `invert_direction` is on |
| Pour (timed, until there is a scale) | `G <bay> <pour_open_steps>`, wait `ml / pour_flow_ml_s`, `G <bay> 0` |
| Abort or emergency stop | `S`; a pour then closes its dispenser with `G <bay> 0` |
| Timeout | `S` |
| Glass placed / taken | nothing yet (no lift); the backend only resets its poured total |

The backend refuses a pour that would bring the glass over 400 ml (`limit`) before it moves
anything.
