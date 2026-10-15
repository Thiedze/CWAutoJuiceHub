// Steppers and shift register outputs. The 12 bay drivers share STEP and DIR, so only
// one bay can be enabled and moving at a time. See docs/hardware.md.
#pragma once

#include <AccelStepper.h>

#include "commands.h"

class Motors {
 public:
  void begin();
  // Call every loop iteration; never blocks.
  void run();
  void stopAll();

  // Bay motion. moveBay() enables only that bay's driver.
  void moveBay(int bay, long target_steps);
  bool bayIdle() { return bay_.distanceToGo() == 0; }
  long bayPosition(int bay) const { return bay_pos_[bay - 1]; }
  // Enabled bay other than `bay` that is away from its rest position, or 0.
  int otherDisplacedBay(int bay) const;

  void moveLift(long target_steps);
  bool liftIdle() { return lift_.distanceToGo() == 0; }
  long liftPosition() { return lift_.currentPosition(); }

  void setLed(int bay, bool on);

 private:
  void selectBay(int bay);
  void writeShiftRegister();

  AccelStepper bay_{AccelStepper::DRIVER, 0, 0};
  AccelStepper lift_{AccelStepper::DRIVER, 0, 0};
  int active_bay_ = 0;
  long bay_pos_[cwjh::BAY_COUNT] = {0};
  uint32_t outputs_ = 0;
};
