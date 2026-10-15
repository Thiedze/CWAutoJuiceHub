// CWAutoJuiceHub ESP32 firmware. Implements docs/protocol.md.
// The loop never blocks: every command is a small state machine advanced by tick().
#include <Arduino.h>

#include "commands.h"
#include "config.h"
#include "link.h"
#include "motors.h"
#include "pour.h"
#include "scale.h"

using namespace cwjh;

namespace {

enum class Phase {
  Idle,
  LiftUp,       // glass/init: raising
  GlassSettle,  // glass/init: let the scale settle, then weigh and tare
  LiftDown,     // glass/release
  BayMove,      // move: tilting a bay
  PourOpen,     // pour: opening the dispenser
  Pouring,      // pour: watching the scale
  PourClose,    // pour: closing the dispenser
  PourSettle,   // pour: let the last drops land, then report
};

constexpr uint32_t COMMAND_TIMEOUT_MS = 30000;
constexpr uint32_t POUR_SETTLE_MS = 600;

Link mqtt;
Motors motors;
Scale scale;
PourController pour({INFLIGHT_OFFSET_G, STALL_G, STALL_MS, GLASS_LOST_G, POUR_TIMEOUT_BASE_MS, POUR_MIN_FLOW_G_S});

HubState state;
Command current;
Phase phase = Phase::Idle;
uint32_t phase_started_ms = 0;
uint32_t last_progress_ms = 0;
uint32_t last_state_ms = 0;
bool state_dirty = true;
const char* pour_error = nullptr;  // set when a pour ends badly, reported after closing

char buf[512];

void publishState() {
  state.scale_g = scale.netGrams();
  for (int i = 0; i < BAY_COUNT; ++i) state.bays[i] = motors.bayPosition(i + 1) / BAY_STEPS_PER_DEGREE;
  formatState(buf, sizeof buf, state, FW_VERSION);
  mqtt.publish(TOPIC_STATE, buf, true);
  last_state_ms = millis();
  state_dirty = false;
}

void ack(const char* id, const char* status, const char* code = nullptr, float poured_ml = -1.0f) {
  formatAck(buf, sizeof buf, id, status, code, poured_ml);
  mqtt.publish(TOPIC_ACK, buf);
}

void enter(Phase next) {
  phase = next;
  phase_started_ms = millis();
  state_dirty = true;
}

// Ends the running command. Busy is cleared and state published before the ack, so the
// Pi can send its next command as soon as it sees the ack.
void finish(const char* status, const char* code = nullptr, float poured_ml = -1.0f) {
  if (current.type == CommandType::Pour || current.type == CommandType::Move) {
    // LED stays on while a bay is tilted for bottle insertion.
    motors.setLed(current.bay, motors.bayPosition(current.bay) != 0);
  }
  enter(Phase::Idle);
  state.busy = false;
  publishState();
  ack(current.id, status, code, poured_ml);
}

float pouredMl() { return pour.pouredGrams() / current.g_per_ml; }

void startCommand(const Command& cmd) {
  current = cmd;
  state.busy = true;
  ack(cmd.id, "accepted");

  switch (cmd.type) {
    case CommandType::GlassInit:
      state.lift = Lift::Moving;
      scale.clearTare();
      motors.moveLift(LIFT_UP_STEPS);
      enter(Phase::LiftUp);
      break;

    case CommandType::GlassRelease:
      state.lift = Lift::Moving;
      motors.moveLift(0);
      enter(Phase::LiftDown);
      break;

    case CommandType::Move:
      motors.setLed(cmd.bay, true);
      motors.moveBay(cmd.bay, lroundf(cmd.degrees * BAY_STEPS_PER_DEGREE));
      enter(Phase::BayMove);
      break;

    case CommandType::Pour:
      if (!state.glass) return finish("error", "no_glass");
      if (state.poured_ml + cmd.ml > MAX_GLASS_ML + 0.01f) return finish("error", "limit");
      pour_error = nullptr;
      motors.setLed(cmd.bay, true);
      motors.moveBay(cmd.bay, POUR_OPEN_STEPS);
      enter(Phase::PourOpen);
      break;

    default:
      break;
  }
}

void onMessage(const char* topic, const uint8_t* payload, unsigned int len) {
  Command cmd;
  ParseResult result = parseCommand(topic, payload, len, cmd);
  if (result == ParseResult::UnknownTopic) return;
  if (result == ParseResult::Invalid) return ack(cmd.id, "error", "invalid");

  if (cmd.type == CommandType::Stop) {
    motors.stopAll();
    if (state.busy) {
      if (current.type == CommandType::Pour) {
        finish("error", "stopped", pouredMl());
      } else {
        if (state.lift == Lift::Moving) state.lift = Lift::Down;  // unknown; safest assumption
        finish("error", "stopped");
      }
    }
    return ack(cmd.id, "done");
  }
  if (cmd.type == CommandType::State) {
    publishState();
    return ack(cmd.id, "done");
  }
  if (state.busy) return ack(cmd.id, "error", "busy");

  // Bays share STEP and DIR, so a bay can only move while all others are at rest.
  if ((cmd.type == CommandType::Move || cmd.type == CommandType::Pour) && motors.otherDisplacedBay(cmd.bay)) {
    return ack(cmd.id, "error", "busy");
  }
  startCommand(cmd);
}

void tick() {
  uint32_t now = millis();
  uint32_t elapsed = now - phase_started_ms;

  switch (phase) {
    case Phase::Idle:
      return;

    case Phase::LiftUp:
      if (motors.liftIdle()) enter(Phase::GlassSettle);
      break;

    case Phase::GlassSettle:
      if (elapsed < SCALE_SETTLE_MS) return;
      state.lift = Lift::Up;
      state.poured_ml = 0.0f;
      state.glass = scale.grossGrams() >= GLASS_MIN_G;
      scale.tare();
      return state.glass ? finish("done") : finish("error", "no_glass");

    case Phase::LiftDown:
      if (!motors.liftIdle()) break;
      state.lift = Lift::Down;
      state.glass = false;
      scale.clearTare();
      return finish("done");

    case Phase::BayMove:
      if (motors.bayIdle()) return finish("done");
      break;

    case Phase::PourOpen:
      if (!motors.bayIdle()) break;
      pour.begin(current.ml * current.g_per_ml, scale.netGrams(), now);
      enter(Phase::Pouring);
      return;

    case Phase::Pouring: {
      PourOutcome outcome = pour.update(scale.netGrams(), now);
      if (now - last_progress_ms >= PROGRESS_INTERVAL_MS) {
        last_progress_ms = now;
        formatProgress(buf, sizeof buf, current.id, pour.pouredGrams(), pour.targetGrams());
        mqtt.publish(TOPIC_PROGRESS, buf);
      }
      if (outcome == PourOutcome::Running) return;
      if (outcome == PourOutcome::BayEmpty) pour_error = "bay_empty";
      if (outcome == PourOutcome::NoGlass) pour_error = "no_glass";
      if (outcome == PourOutcome::Timeout) pour_error = "timeout";
      motors.moveBay(current.bay, 0);
      enter(Phase::PourClose);
      return;
    }

    case Phase::PourClose:
      if (motors.bayIdle()) enter(Phase::PourSettle);
      return;

    case Phase::PourSettle: {
      if (elapsed < POUR_SETTLE_MS) return;
      pour.update(scale.netGrams(), now);  // final reading, includes the in-flight drops
      float ml = pouredMl();
      state.poured_ml += ml;
      if (pour_error && strcmp(pour_error, "no_glass") == 0) state.glass = false;
      return pour_error ? finish("error", pour_error, ml) : finish("done", nullptr, ml);
    }
  }

  // Watchdog for the motion phases; pours have their own timeout in PourController.
  if (elapsed > COMMAND_TIMEOUT_MS) {
    motors.stopAll();
    finish("error", "timeout");
  }
}

}  // namespace

void setup() {
  Serial.begin(115200);
  motors.begin();
  scale.begin(PIN_HX711_DOUT, PIN_HX711_SCK, SCALE_FACTOR);
  mqtt.begin(onMessage, [] { publishState(); });
  Serial.println("CWAutoJuiceHub " FW_VERSION);
}

void loop() {
  motors.run();
  mqtt.loop();
  motors.run();
  scale.update();
  tick();

  uint32_t now = millis();
  if (state_dirty || now - last_state_ms >= STATE_INTERVAL_MS) publishState();
}
