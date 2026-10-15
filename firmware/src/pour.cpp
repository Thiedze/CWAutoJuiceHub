#include "pour.h"

namespace cwjh {

void PourController::begin(float target_g, float start_net_g, uint32_t now_ms) {
  target_g_ = target_g;
  start_g_ = start_net_g;
  poured_g_ = 0.0f;
  last_gain_g_ = 0.0f;
  start_ms_ = now_ms;
  last_gain_ms_ = now_ms;
  timeout_ms_ = config_.timeout_base_ms + static_cast<uint32_t>(target_g / config_.min_flow_g_s * 1000.0f);
}

PourOutcome PourController::update(float net_g, uint32_t now_ms) {
  // Unsigned subtraction keeps working when millis() wraps around.
  poured_g_ = net_g - start_g_;

  if (poured_g_ < -config_.glass_lost_g) return PourOutcome::NoGlass;
  if (poured_g_ >= target_g_ - config_.inflight_g) return PourOutcome::Done;

  if (poured_g_ - last_gain_g_ >= config_.stall_g) {
    last_gain_g_ = poured_g_;
    last_gain_ms_ = now_ms;
  } else if (now_ms - last_gain_ms_ >= config_.stall_ms) {
    return PourOutcome::BayEmpty;
  }

  if (now_ms - start_ms_ >= timeout_ms_) return PourOutcome::Timeout;
  return PourOutcome::Running;
}

}  // namespace cwjh
