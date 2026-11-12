// Pins and motion defaults. See docs/hardware.md.
#pragma once

#include <stdint.h>

#define FW_VERSION "0.4.0"

// SX1509 I/O expander, ADDR jumpers untouched.
constexpr uint8_t SX1509_ADDR = 0x3E;
// 100 kHz is safe on a breadboard. Every STEP pulse is two I2C writes, so this
// caps the step rate at about 320 steps/s (400 kHz gives roughly 3x).
constexpr uint32_t I2C_CLOCK = 100000;

// STEP and DIR are shared by all bay drivers (SX1509 I/O numbers).
constexpr uint8_t SX_STEP = 1;
constexpr uint8_t SX_DIR = 2;

// ENABLE pin (SX1509 I/O number, active low on the DRV8825) of bay 1, 2, ...
// Add one entry per wired bay; the Nano reports the count in READY.
constexpr uint8_t BAY_ENABLE[] = {3};
constexpr uint8_t BAY_COUNT = sizeof(BAY_ENABLE) / sizeof(BAY_ENABLE[0]);

// Home switch of bay 1, 2, ... (SX1509 I/O number), pressed when the bay is at rest.
// Input with the SX1509's internal pull-up; the switch closes to GND (pressed = low).
// NO_SWITCH for a bay without one.
constexpr uint8_t NO_SWITCH = 0xFF;
constexpr uint8_t BAY_HOME[] = {4};
static_assert(sizeof(BAY_HOME) == BAY_COUNT, "one BAY_HOME entry per bay");

// Motion profile until the Pi sends one (P request): constant 300 steps/s, no curves.
constexpr uint16_t DEFAULT_STEPS_PER_S = 300;
