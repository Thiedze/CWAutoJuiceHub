// Bay steppers (DRV8825) behind the SX1509: shared STEP and DIR, one ENABLE per bay.
#pragma once

#include <stdint.h>

#include "config.h"

namespace cwjh {

enum class MoveResult : uint8_t { Done, Stopped, Busy };

class Motors {
 public:
  bool begin();

  // Absolute position in steps of bay 1..BAY_COUNT. 0 is the rest position at boot.
  long position(uint8_t bay) const { return pos_[bay - 1]; }
  const long* positions() const { return pos_; }

  void setSpeed(uint16_t stepsPerSecond) { speed_ = stepsPerSecond; }

  // A bay keeps torque while it is away from rest or while hold is on.
  void setHold(uint8_t bay, bool on);

  // Blocks until bay reaches target. shouldStop is polled between steps.
  // Busy: another bay must keep torque, and it would follow the shared STEP line.
  MoveResult moveTo(uint8_t bay, long target, bool (*shouldStop)());

  // Emergency stop: every driver off. Positions are kept but may be off if a
  // loaded bay slips without torque.
  void disableAll();

 private:
  void setEnabled(uint8_t index, bool on);
  bool needsTorque(uint8_t index) const { return hold_[index] || pos_[index] != 0; }

  long pos_[BAY_COUNT] = {0};
  bool hold_[BAY_COUNT] = {false};
  bool enabled_[BAY_COUNT] = {false};
  uint16_t speed_ = DEFAULT_STEPS_PER_S;
};

}  // namespace cwjh
