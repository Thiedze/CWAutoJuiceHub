#include "motors.h"

#include <Arduino.h>
#include <Wire.h>  // before SparkFunSX1509.h, which uses TwoWire
#include <SparkFunSX1509.h>

namespace cwjh {

namespace {
SX1509 io;
}

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
  return true;
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

MoveResult Motors::moveTo(uint8_t bay, long target, bool (*shouldStop)()) {
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

  const uint32_t period = 1000000UL / speed_;
  uint32_t next = micros();
  MoveResult result = MoveResult::Done;

  while (pos_[index] != target) {
    if (shouldStop()) {
      result = MoveResult::Stopped;
      break;
    }
    io.digitalWrite(SX_STEP, HIGH);
    io.digitalWrite(SX_STEP, LOW);
    pos_[index] += forward ? 1 : -1;

    next += period;
    while ((int32_t)(micros() - next) < 0) {
    }
    // I2C may be slower than the requested rate; don't try to catch up.
    if ((int32_t)(micros() - next) > (int32_t)period) next = micros();
  }

  if (result == MoveResult::Done) setEnabled(index, needsTorque(index));
  return result;
}

}  // namespace cwjh
