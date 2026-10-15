# MQTT protocol

This is the contract between the Raspberry Pi (backend) and the ESP32 (hub). Both sides
must follow it exactly. It refines the "protocol" page of the wiki.

## Transport

- MQTT 3.1.1, broker runs on the Pi (Mosquitto, port 1883).
- All topics start with `cwjh/`.
- All payloads are UTF-8 JSON objects, except `cwjh/hub/online`.
- QoS 1 for commands and acks, QoS 0 for progress.

## Commands (Pi to hub)

Every command carries an `id` (string, max 36 chars, chosen by the Pi, unique per command).
The hub answers each command on `cwjh/hub/ack` using the same `id`.

| Topic | Payload | Effect |
|---|---|---|
| `cwjh/cmd/glass/init` | `{"id"}` | Raise the lift, tare the scale, check that a glass is present. |
| `cwjh/cmd/glass/release` | `{"id"}` | Lower the lift so the glass can be taken. |
| `cwjh/cmd/pour` | `{"id", "bay", "ml", "g_per_ml"}` | Pour from `bay` (1..12) until the scale has gained `ml * g_per_ml` grams. |
| `cwjh/cmd/move` | `{"id", "bay", "degrees"}` | Tilt `bay` to an absolute angle for bottle insertion. |
| `cwjh/cmd/stop` | `{"id"}` | Emergency stop: disable every motor at once. |
| `cwjh/cmd/state` | `{"id"}` | Publish a fresh `cwjh/hub/state`. |

### Limits

- `degrees` is clamped to `0..30`. Values outside are cut, not rejected (as the wiki says).
- `ml` must be `> 0`. The total poured since the last `glass/init` must not exceed 400 ml.
  A pour that would exceed it is rejected with code `limit`. The Pi enforces the same limit
  before sending.
- `g_per_ml` must be in `0.5..2.0` (density of the liquid).

### Concurrency

The hub runs one command at a time. While it is busy, any new command except `stop` and
`state` is rejected with code `busy`.

The hub must clear its busy flag **before** it publishes the final `done` or `error` ack. The
Pi sends the next command as soon as it sees the ack, and would otherwise get `busy`.

## Hub to Pi

### `cwjh/hub/ack`

```json
{"id": "abc", "status": "accepted"}
{"id": "abc", "status": "done", "poured_ml": 39.6}
{"id": "abc", "status": "error", "code": "bay_empty", "poured_ml": 12.0}
```

- `accepted` is sent right away for commands that take time (`glass/init`, `glass/release`,
  `pour`, `move`). Instant commands (`stop`, `state`) only get `done`.
- `done` ends a command. `pour` adds `poured_ml`.
- `error` ends a command. `pour` adds `poured_ml` when something was poured.

Error codes:

| Code | Meaning |
|---|---|
| `invalid` | Payload could not be parsed, or a field is missing or out of range. |
| `busy` | Another command is still running. |
| `no_glass` | No glass on the scale (on `glass/init`, or the glass was removed while pouring). |
| `bay_empty` | The scale stopped gaining weight while pouring. |
| `limit` | The pour would exceed the 400 ml glass limit. |
| `timeout` | The command did not finish within its time limit. |
| `stopped` | The command was cancelled by `stop`. |

### `cwjh/hub/progress`

Sent about 5 times per second while pouring.

```json
{"id": "abc", "grams": 21.4, "target": 40.0}
```

### `cwjh/hub/state` (retained)

Published on every change and in reply to `cwjh/cmd/state`.

```json
{
  "busy": false,
  "scale_g": 0.0,
  "glass": true,
  "lift": "up",
  "poured_ml": 120.0,
  "bays": [0, 0, 30, 0, 0, 0, 0, 0, 0, 0, 0, 0],
  "fw": "0.1.0"
}
```

- `lift` is `"up"`, `"down"` or `"moving"`.
- `poured_ml` is the total poured since the last `glass/init`.
- `bays` holds the current tilt angle of bay 1..12.

### `cwjh/hub/online` (retained)

`"1"` after the hub connects. `"0"` is set as the MQTT last will, so the broker publishes it
when the hub drops off the network.

## Flows

### Initialise a bay (fill with a bottle)

1. User picks a bay and a liquid in the UI.
2. Pi sends `move {bay, degrees: 30}`, waits for `done`.
3. User inserts the bottle and confirms in the UI.
4. Pi sends `move {bay, degrees: 0}`, waits for `done`, and stores the bay contents.

### Make a cocktail

1. User picks a cocktail. The UI asks for a glass.
2. Pi sends `glass/init`, waits for `done`.
3. For each liquid step in order: Pi sends `pour`, waits for `done`, then subtracts
   `poured_ml` from the bay volume.
4. Solid steps (fruit, ice and so on) are manual: the UI shows them and waits for "next".
5. Pi sends `glass/release`. The drink is ready.

On any `error` the Pi stops the flow and shows the error. On a timeout waiting for an ack
the Pi sends `stop`.
