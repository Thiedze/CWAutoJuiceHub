// Parsing and formatting of docs/protocol.md messages. No Arduino dependencies, so it
// is unit tested on the host (pio test -e native).
#pragma once

#include <stddef.h>
#include <stdint.h>

namespace cwjh {

constexpr int BAY_COUNT = 12;
constexpr float MAX_GLASS_ML = 400.0f;
constexpr float MAX_TILT_DEG = 30.0f;
constexpr float MIN_G_PER_ML = 0.5f;
constexpr float MAX_G_PER_ML = 2.0f;
constexpr size_t ID_SIZE = 37;  // 36 chars + terminator

constexpr const char* TOPIC_CMD_ALL = "cwjh/cmd/#";
constexpr const char* TOPIC_ACK = "cwjh/hub/ack";
constexpr const char* TOPIC_PROGRESS = "cwjh/hub/progress";
constexpr const char* TOPIC_STATE = "cwjh/hub/state";
constexpr const char* TOPIC_ONLINE = "cwjh/hub/online";

enum class CommandType { None, GlassInit, GlassRelease, Pour, Move, Stop, State };

struct Command {
  CommandType type = CommandType::None;
  char id[ID_SIZE] = {0};
  int bay = 0;
  float ml = 0.0f;
  float g_per_ml = 1.0f;
  float degrees = 0.0f;
};

enum class ParseResult { Ok, UnknownTopic, Invalid };

// On Invalid, out.id is still set when the payload had a usable id.
ParseResult parseCommand(const char* topic, const uint8_t* payload, size_t len, Command& out);

float clampDegrees(float degrees);

// poured_ml < 0 leaves the field out. code may be nullptr.
size_t formatAck(char* buf, size_t size, const char* id, const char* status, const char* code, float poured_ml);
size_t formatProgress(char* buf, size_t size, const char* id, float grams, float target);

enum class Lift { Down, Up, Moving };

struct HubState {
  bool busy = false;
  float scale_g = 0.0f;
  bool glass = false;
  Lift lift = Lift::Down;
  float poured_ml = 0.0f;
  float bays[BAY_COUNT] = {0};
};

size_t formatState(char* buf, size_t size, const HubState& state, const char* fw);

}  // namespace cwjh
