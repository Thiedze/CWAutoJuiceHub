// Pins and motion defaults. See docs/hardware.md.
#pragma once

#include <stdint.h>

#define FW_VERSION "0.2.0"

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

constexpr uint16_t DEFAULT_STEPS_PER_S = 300;
