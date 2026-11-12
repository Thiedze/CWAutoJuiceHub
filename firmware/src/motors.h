// Bay steppers (DRV8825) behind the SX1509: shared STEP and DIR, one ENABLE per bay.
#pragma once

#include <stdint.h>

#include "config.h"

namespace cwjh {

enum class MoveResult : uint8_t { Done, Found, Stopped, Busy };

// Speed over a move: from minSpeed up to maxSpeed along an S-curve (half a cosine) that
// takes startMs, then back down to minSpeed along an S-curve of endMs before the target.
// A move too short for both curves at full height gets a lower peak.
struct Profile {
  uint16_t maxSpeed = DEFAULT_STEPS_PER_S;  // steps/s
  uint16_t minSpeed = DEFAULT_STEPS_PER_S;  // steps/s at the start and the end
  uint16_t startMs = 0;
  uint16_t endMs = 0;
};

class Motors {
 public:
  bool begin();

  // Absolute position in steps of bay 1..BAY_COUNT. 0 is the rest position at boot.
  long position(uint8_t bay) const { return pos_[bay - 1]; }
  const long* positions() const { return pos_; }

  void setProfile(uint8_t bay, const Profile& profile) { profile_[bay - 1] = profile; }

  // A bay keeps torque while it is away from rest or while hold is on.
  void setHold(uint8_t bay, bool on);

  // Blocks until bay reaches target. shouldStop is polled between steps.
  // With switchLevel 0 or 1 the move also ends (Found) as soon as the bay's home switch
  // reads that level (1 pressed, 0 released), checked before every step.
  // Busy: another bay must keep torque, and it would follow the shared STEP line.
  MoveResult moveTo(uint8_t bay, long target, bool (*shouldStop)(), int8_t switchLevel = -1);

  // Home switch of bay: 1 pressed, 0 released, -1 no switch or I2C error.
  int8_t homeSwitch(uint8_t bay) const;

  // Declare the current position of bay as 0.
  void zero(uint8_t bay);

  // Emergency stop: every driver off. Positions are kept but may be off if a
  // loaded bay slips without torque.
  void disableAll();

 private:
  void setEnabled(uint8_t index, bool on);
  bool needsTorque(uint8_t index) const { return hold_[index] || pos_[index] != 0; }

  long pos_[BAY_COUNT] = {0};
  bool hold_[BAY_COUNT] = {false};
  bool enabled_[BAY_COUNT] = {false};
  Profile profile_[BAY_COUNT];
};

}  // namespace cwjh
