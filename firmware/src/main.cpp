// Nano motor controller: moves bay steppers on request from the Pi over USB serial.
// All machine logic lives in the backend. Protocol: docs/protocol.md.

#include <Arduino.h>

#include "commands.h"
#include "config.h"
#include "motors.h"

using namespace cwjh;

namespace {

Motors motors;

char line[40];
uint8_t lineLen = 0;
bool lineOverflow = false;
char out[96];

bool moving = false;
bool stopRequested = false;
uint16_t stopSeq = 0;

void reply(size_t) { Serial.println(out); }

// Collects serial input; true when a complete line is in `line`.
bool readLine() {
  while (Serial.available()) {
    const char c = Serial.read();
    if (c == '\r') continue;
    if (c == '\n') {
      line[lineLen] = '\0';
      const bool ok = !lineOverflow;
      lineLen = 0;
      lineOverflow = false;
      if (ok) return true;
      continue;  // too long to be a valid request
    }
    if (lineLen < sizeof(line) - 1) {
      line[lineLen++] = c;
    } else {
      lineOverflow = true;
    }
  }
  return false;
}

bool pollWhileMoving();

// Go and Find: both move until the target; Find also ends at the home switch.
void handleMove(const Request& req) {
  const bool find = req.op == Op::Find;
  moving = true;
  stopRequested = false;
  const MoveResult result =
      motors.moveTo(req.bay, req.value, pollWhileMoving, find ? static_cast<int8_t>(req.level) : -1);
  moving = false;

  switch (result) {
    case MoveResult::Done:
    case MoveResult::Found:
      if (find) {
        reply(formatFound(out, sizeof(out), req.seq, motors.position(req.bay), result == MoveResult::Found));
      } else {
        reply(formatOkValue(out, sizeof(out), req.seq, motors.position(req.bay)));
      }
      break;
    case MoveResult::Busy:
      reply(formatErr(out, sizeof(out), req.seq, "busy"));
      break;
    case MoveResult::Stopped:
      motors.disableAll();
      reply(formatErr(out, sizeof(out), req.seq, "stopped"));
      reply(formatOk(out, sizeof(out), stopSeq));
      break;
  }
}

void handle(const char* text) {
  Request req;
  switch (parseRequest(text, req)) {
    case ParseResult::Ok:
      break;
    case ParseResult::Invalid:
      reply(formatErr(out, sizeof(out), req.seq, "invalid"));
      return;
    case ParseResult::Empty:
    case ParseResult::NoSeq:
      return;
  }

  if (req.op == Op::Stop) {
    if (moving) {
      stopRequested = true;  // answered once the move has stopped
      stopSeq = req.seq;
    } else {
      motors.disableAll();
      reply(formatOk(out, sizeof(out), req.seq));
    }
    return;
  }
  if (req.op == Op::Query) {
    reply(formatState(out, sizeof(out), req.seq, moving, motors.positions(), BAY_COUNT));
    return;
  }
  if (req.op == Op::Switches) {
    int8_t states[BAY_COUNT];
    for (uint8_t i = 0; i < BAY_COUNT; i++) states[i] = motors.homeSwitch(i + 1);
    reply(formatSwitches(out, sizeof(out), req.seq, states, BAY_COUNT));
    return;
  }
  if (moving) {
    reply(formatErr(out, sizeof(out), req.seq, "busy"));
    return;
  }
  if (req.bay > BAY_COUNT) {
    reply(formatErr(out, sizeof(out), req.seq, "no_motor"));
    return;
  }
  if (req.op == Op::Find && BAY_HOME[req.bay - 1] == NO_SWITCH) {
    reply(formatErr(out, sizeof(out), req.seq, "no_switch"));
    return;
  }

  switch (req.op) {
    case Op::Go:
    case Op::Find:
      handleMove(req);
      break;
    case Op::Zero:
      motors.zero(req.bay);
      reply(formatOk(out, sizeof(out), req.seq));
      break;
    case Op::Hold:
      motors.setHold(req.bay, req.value != 0);
      reply(formatOk(out, sizeof(out), req.seq));
      break;
    case Op::Profile: {
      Profile profile;
      profile.maxSpeed = static_cast<uint16_t>(req.value);
      profile.minSpeed = req.minSpeed;
      profile.startMs = req.startMs;
      profile.endMs = req.endMs;
      motors.setProfile(req.bay, profile);
      reply(formatOk(out, sizeof(out), req.seq));
      break;
    }
    default:
      break;
  }
}

// Called between steps: answers Q and S, rejects the rest with busy.
bool pollWhileMoving() {
  if (readLine()) handle(line);
  return stopRequested;
}

}  // namespace

void setup() {
  Serial.begin(115200);
  if (!motors.begin()) {
    while (true) {
      Serial.println(F("FAIL sx1509"));
      delay(1000);
    }
  }
  reply(formatReady(out, sizeof(out), FW_VERSION, BAY_COUNT));
}

void loop() {
  if (readLine()) handle(line);
}
