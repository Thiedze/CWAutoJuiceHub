// Parsing and formatting of the serial line protocol (docs/protocol.md). No Arduino
// dependencies, so it is unit tested on the host (pio test -e native).
#pragma once

#include <stddef.h>
#include <stdint.h>

namespace cwjh {

constexpr uint8_t MAX_BAYS = 12;
constexpr long MAX_POSITION = 100000;  // steps, either direction
constexpr long MAX_STEPS_PER_S = 5000;
constexpr long MAX_CURVE_MS = 10000;

enum class Op : uint8_t { Go, Find, Zero, Switches, Stop, Query, Profile, Hold };

struct Request {
  uint16_t seq = 0;
  Op op = Op::Query;
  uint8_t bay = 0;    // Go, Find, Zero, Hold, Profile
  long value = 0;     // Go, Find: target position, Profile: top speed (steps/s), Hold: 0 or 1
  uint8_t level = 0;  // Find: stop when the home switch reads this (1 pressed, 0 released)
  // Profile: start speed (steps/s) and the length of the start and end curve (ms).
  uint16_t minSpeed = 0;
  uint16_t startMs = 0;
  uint16_t endMs = 0;
};

enum class ParseResult : uint8_t {
  Ok,
  Empty,    // blank line, ignore
  NoSeq,    // no sequence number, cannot be answered
  Invalid,  // answer "<seq> ERR invalid"
};

// line must be NUL terminated and without the line ending.
ParseResult parseRequest(const char* line, Request& out);

// Replies, each without the line ending.
size_t formatReady(char* buf, size_t size, const char* fw, uint8_t bays);
size_t formatOk(char* buf, size_t size, uint16_t seq);
size_t formatOkValue(char* buf, size_t size, uint16_t seq, long value);
size_t formatErr(char* buf, size_t size, uint16_t seq, const char* code);
size_t formatState(char* buf, size_t size, uint16_t seq, bool busy, const long* positions, uint8_t bays);
// Find: "<seq> OK <pos> <found>".
size_t formatFound(char* buf, size_t size, uint16_t seq, long position, bool found);
// Home switch per bay: 1 pressed, 0 released, -1 no switch ("-").
size_t formatSwitches(char* buf, size_t size, uint16_t seq, const int8_t* states, uint8_t bays);

}  // namespace cwjh
