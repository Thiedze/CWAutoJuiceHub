// Decides when a pour is finished, from scale readings only. No Arduino dependencies,
// so it is unit tested on the host (pio test -e native).
#pragma once

#include <stdint.h>

namespace cwjh {

struct PourConfig {
  float inflight_g;     // stop this much early; it still falls after the valve closes
  float stall_g;        // must gain at least this ...
  uint32_t stall_ms;    // ... within this time, or the bay is empty
  float glass_lost_g;   // net weight below start minus this means the glass is gone
  uint32_t timeout_base_ms;
  float min_flow_g_s;   // timeout = base + target / min_flow
};

enum class PourOutcome { Running, Done, BayEmpty, NoGlass, Timeout };

class PourController {
 public:
  explicit PourController(const PourConfig& config) : config_(config) {}

  void begin(float target_g, float start_net_g, uint32_t now_ms);
  PourOutcome update(float net_g, uint32_t now_ms);

  float pouredGrams() const { return poured_g_ > 0.0f ? poured_g_ : 0.0f; }
  float targetGrams() const { return target_g_; }

 private:
  PourConfig config_;
  float target_g_ = 0.0f;
  float start_g_ = 0.0f;
  float poured_g_ = 0.0f;
  float last_gain_g_ = 0.0f;
  uint32_t start_ms_ = 0;
  uint32_t last_gain_ms_ = 0;
  uint32_t timeout_ms_ = 0;
};

}  // namespace cwjh
