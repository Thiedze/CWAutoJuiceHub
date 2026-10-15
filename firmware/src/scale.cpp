#include "scale.h"

namespace {
// Exponential smoothing; HX711 runs at 10 Hz (or 80 Hz with RATE high), so this
// settles in well under a second while still damping pour splashes.
constexpr float ALPHA = 0.4f;
}  // namespace

void Scale::begin(uint8_t dout, uint8_t sck, float factor) {
  factor_ = factor;
  hx_.begin(dout, sck);
  // One blocking zero at boot is fine; nothing else runs yet.
  zero_raw_ = hx_.wait_ready_timeout(2000) ? hx_.read_average(10) : 0;
}

bool Scale::update() {
  if (!hx_.is_ready()) return false;
  float grams = (hx_.read() - zero_raw_) / factor_;
  gross_g_ = primed_ ? gross_g_ + ALPHA * (grams - gross_g_) : grams;
  primed_ = true;
  return true;
}
