#include "motors.h"

#include <Arduino.h>
#include <math.h>
#include <Wire.h>  // before SparkFunSX1509.h, which uses TwoWire
#include <SparkFunSX1509.h>

namespace cwjh {

namespace {
SX1509 io;

constexpr uint8_t REG_DATA_B = 0x10;  // RegDataB, RegDataA follows: level on each I/O

// One I2C transaction for all 16 levels (the library's digitalRead needs two).
bool readLevels(uint16_t& levels) {
  Wire.beginTransmission(SX1509_ADDR);
  Wire.write(REG_DATA_B);
  if (Wire.endTransmission(false) != 0) return false;
  if (Wire.requestFrom(SX1509_ADDR, (uint8_t)2) != 2) return false;
  const uint8_t b = Wire.read();
  const uint8_t a = Wire.read();
  levels = (static_cast<uint16_t>(b) << 8) | a;
  return true;
}
}  // namespace

bool Motors::begin() {
  Wire.begin();
  if (!io.begin(SX1509_ADDR)) return false;
  Wire.setClock(I2C_CLOCK);

  // Drivers off first so no motor jumps.
  for (uint8_t i = 0; i < BAY_COUNT; i++) {
    io.pinMode(BAY_ENABLE[i], OUTPUT);
    setEnabled(i, false);
  }
  io.pinMode(SX_STEP, OUTPUT);
  io.pinMode(SX_DIR, OUTPUT);
  io.digitalWrite(SX_STEP, LOW);
  for (uint8_t i = 0; i < BAY_COUNT; i++) {
    if (BAY_HOME[i] != NO_SWITCH) io.pinMode(BAY_HOME[i], INPUT_PULLUP);
  }
  return true;
}

int8_t Motors::homeSwitch(uint8_t bay) const {
  const uint8_t pin = BAY_HOME[bay - 1];
  uint16_t levels;
  if (pin == NO_SWITCH || !readLevels(levels)) return -1;
  return (levels & (1u << pin)) ? 0 : 1;  // pull-up: pressed reads low
}

void Motors::zero(uint8_t bay) {
  const uint8_t index = bay - 1;
  pos_[index] = 0;
  setEnabled(index, needsTorque(index));
}

void Motors::setEnabled(uint8_t index, bool on) {
  io.digitalWrite(BAY_ENABLE[index], on ? LOW : HIGH);
  enabled_[index] = on;
}

void Motors::setHold(uint8_t bay, bool on) {
  const uint8_t index = bay - 1;
  hold_[index] = on;
  setEnabled(index, needsTorque(index));
}

void Motors::disableAll() {
  for (uint8_t i = 0; i < BAY_COUNT; i++) {
    hold_[i] = false;
    setEnabled(i, false);
  }
}

MoveResult Motors::moveTo(uint8_t bay, long target, bool (*shouldStop)(), int8_t switchLevel) {
  const uint8_t index = bay - 1;

  for (uint8_t i = 0; i < BAY_COUNT; i++) {
    if (i == index || !enabled_[i]) continue;
    if (needsTorque(i)) return MoveResult::Busy;
    setEnabled(i, false);
  }

  const bool forward = target >= pos_[index];
  io.digitalWrite(SX_DIR, forward ? HIGH : LOW);
  if (!enabled_[index]) {
    setEnabled(index, true);
    delay(2);  // DRV8825 wake-up after ENABLE
  }

  // S-curve timing, see Profile. All speeds in steps/s, times in s.
  const Profile& p = profile_[index];
  const long total = labs(target - pos_[index]);
  const float vMin = p.minSpeed;
  const float tStart = p.startMs / 1000.0f;
  const float tEnd = p.endMs / 1000.0f;
  float vPeak = p.maxSpeed;
  if (tStart + tEnd > 0) {
    // Each curve covers (vPeak + vMin) / 2 * its time in steps.
    if ((vPeak + vMin) / 2 * (tStart + tEnd) > total) vPeak = max(vMin, 2.0f * total / (tStart + tEnd) - vMin);
  }
  const float endSteps = (vPeak + vMin) / 2 * tEnd;

  const uint32_t started = micros();
  uint32_t endStarted = 0;
  bool ending = false;
  float v = tStart > 0 ? vMin : vPeak;
  float vAtEnd = vPeak;
  long done = 0;
  uint32_t next = started;
  MoveResult result = MoveResult::Done;

  while (true) {
    if (switchLevel >= 0 && homeSwitch(bay) == switchLevel) {
      result = MoveResult::Found;
      break;
    }
    if (pos_[index] == target) break;
    if (shouldStop()) {
      result = MoveResult::Stopped;
      break;
    }
    io.digitalWrite(SX_STEP, HIGH);
    io.digitalWrite(SX_STEP, LOW);
    pos_[index] += forward ? 1 : -1;
    done++;

    const uint32_t now = micros();
    if (tEnd > 0 && total - done <= endSteps) {
      if (!ending) {
        ending = true;
        endStarted = now;
        vAtEnd = v;  // start the end curve from wherever the start curve got to
      }
      const float x = min(1.0f, (now - endStarted) / 1e6f / tEnd);
      v = vMin + (vAtEnd - vMin) * (1 + cosf(PI * x)) / 2;
    } else if (tStart > 0) {
      const float x = min(1.0f, (now - started) / 1e6f / tStart);
      v = vMin + (vPeak - vMin) * (1 - cosf(PI * x)) / 2;
    } else {
      v = vPeak;
    }
    const uint32_t period = static_cast<uint32_t>(1e6f / max(v, 1.0f));

    next += period;
    while ((int32_t)(micros() - next) < 0) {
    }
    // I2C may be slower than the requested rate; don't try to catch up.
    if ((int32_t)(micros() - next) > (int32_t)period) next = micros();
  }

  if (result != MoveResult::Stopped) setEnabled(index, needsTorque(index));
  return result;
}

}  // namespace cwjh
