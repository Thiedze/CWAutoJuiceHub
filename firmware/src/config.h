// Pins, limits and calibration. See docs/hardware.md.
#pragma once

#include <stdint.h>

#define FW_VERSION "0.1.0"

// Shared STEP and DIR for the 12 bay drivers; each is selected via its ENABLE.
constexpr uint8_t PIN_BAY_STEP = 25;
constexpr uint8_t PIN_BAY_DIR = 26;

constexpr uint8_t PIN_LIFT_STEP = 32;
constexpr uint8_t PIN_LIFT_DIR = 33;
constexpr uint8_t PIN_LIFT_ENABLE = 27;

// 4x 74HC595: Q0..Q11 bay ENABLE (active low), Q16..Q27 bay LED (active high).
constexpr uint8_t PIN_SR_DATA = 23;
constexpr uint8_t PIN_SR_CLOCK = 18;
constexpr uint8_t PIN_SR_LATCH = 5;
constexpr uint8_t SR_LED_OFFSET = 16;

constexpr uint8_t PIN_HX711_DOUT = 16;
constexpr uint8_t PIN_HX711_SCK = 17;

// Motion.
constexpr float BAY_STEPS_PER_DEGREE = 8.889f;  // 200 steps * 16 microsteps / 360
constexpr float BAY_MAX_SPEED = 1600.0f;        // steps/s
constexpr float BAY_ACCEL = 4000.0f;            // steps/s^2
// Pouring opens the dispenser by turning the bay motor this far from its rest
// position (opposite direction to the bottle tilt). ASSUMPTION, see docs/hardware.md.
constexpr long POUR_OPEN_STEPS = -400;

constexpr long LIFT_UP_STEPS = 6400;
constexpr float LIFT_MAX_SPEED = 2000.0f;
constexpr float LIFT_ACCEL = 3000.0f;

// Scale.
constexpr float SCALE_FACTOR = 420.0f;    // raw counts per gram, calibrate!
constexpr float GLASS_MIN_G = 50.0f;      // lighter than this is "no glass"
constexpr uint32_t SCALE_SETTLE_MS = 800;  // wait after the lift stops before weighing

// Pour.
constexpr float INFLIGHT_OFFSET_G = 3.0f;  // still falls after the valve closes
constexpr float STALL_G = 2.0f;            // must gain this much ...
constexpr uint32_t STALL_MS = 3000;        // ... within this time, or the bay is empty
constexpr float GLASS_LOST_G = 30.0f;      // weight drop that means the glass was removed
constexpr uint32_t POUR_TIMEOUT_BASE_MS = 10000;
constexpr float POUR_MIN_FLOW_G_S = 5.0f;

// Reporting.
constexpr uint32_t PROGRESS_INTERVAL_MS = 200;
constexpr uint32_t STATE_INTERVAL_MS = 2000;
