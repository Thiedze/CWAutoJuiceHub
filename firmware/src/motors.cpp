#include "motors.h"

#include <Arduino.h>

#include "config.h"

using cwjh::BAY_COUNT;

namespace {
constexpr uint32_t ALL_BAYS_DISABLED = (1u << BAY_COUNT) - 1;  // ENABLE is active low
}

void Motors::begin() {
  pinMode(PIN_SR_DATA, OUTPUT);
  pinMode(PIN_SR_CLOCK, OUTPUT);
  pinMode(PIN_SR_LATCH, OUTPUT);
  outputs_ = ALL_BAYS_DISABLED;
  writeShiftRegister();

  bay_ = AccelStepper(AccelStepper::DRIVER, PIN_BAY_STEP, PIN_BAY_DIR);
  bay_.setMaxSpeed(BAY_MAX_SPEED);
  bay_.setAcceleration(BAY_ACCEL);

  lift_ = AccelStepper(AccelStepper::DRIVER, PIN_LIFT_STEP, PIN_LIFT_DIR);
  lift_.setEnablePin(PIN_LIFT_ENABLE);
  lift_.setPinsInverted(false, false, true);
  lift_.setMaxSpeed(LIFT_MAX_SPEED);
  lift_.setAcceleration(LIFT_ACCEL);
  lift_.enableOutputs();
}

void Motors::run() {
  bay_.run();
  lift_.run();
  if (active_bay_) bay_pos_[active_bay_ - 1] = bay_.currentPosition();
}

void Motors::stopAll() {
  // Hard stop: no deceleration ramp, drivers released at once.
  bay_.setCurrentPosition(bay_.currentPosition());
  lift_.setCurrentPosition(lift_.currentPosition());
  if (active_bay_) bay_pos_[active_bay_ - 1] = bay_.currentPosition();
  outputs_ |= ALL_BAYS_DISABLED;
  active_bay_ = 0;
  writeShiftRegister();
}

int Motors::otherDisplacedBay(int bay) const {
  for (int i = 1; i <= BAY_COUNT; ++i) {
    if (i != bay && bay_pos_[i - 1] != 0) return i;
  }
  return 0;
}

void Motors::moveBay(int bay, long target_steps) {
  selectBay(bay);
  bay_.moveTo(target_steps);
}

void Motors::moveLift(long target_steps) { lift_.moveTo(target_steps); }

void Motors::setLed(int bay, bool on) {
  uint32_t bit = 1u << (SR_LED_OFFSET + bay - 1);
  outputs_ = on ? (outputs_ | bit) : (outputs_ & ~bit);
  writeShiftRegister();
}

void Motors::selectBay(int bay) {
  if (bay == active_bay_) return;
  // Only the selected driver is enabled. A disabled bay may not hold its position, so
  // the caller makes sure no other bay is displaced before selecting a new one.
  outputs_ |= ALL_BAYS_DISABLED;
  outputs_ &= ~(1u << (bay - 1));
  writeShiftRegister();
  active_bay_ = bay;
  bay_.setCurrentPosition(bay_pos_[bay - 1]);
}

void Motors::writeShiftRegister() {
  digitalWrite(PIN_SR_LATCH, LOW);
  for (int byte = 3; byte >= 0; --byte) {
    shiftOut(PIN_SR_DATA, PIN_SR_CLOCK, MSBFIRST, (outputs_ >> (byte * 8)) & 0xFF);
  }
  digitalWrite(PIN_SR_LATCH, HIGH);
}
