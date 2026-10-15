#include "commands.h"

#include <ArduinoJson.h>
#include <math.h>
#include <string.h>

namespace cwjh {

namespace {

struct TopicType {
  const char* topic;
  CommandType type;
};

constexpr TopicType TOPICS[] = {
    {"cwjh/cmd/glass/init", CommandType::GlassInit},
    {"cwjh/cmd/glass/release", CommandType::GlassRelease},
    {"cwjh/cmd/pour", CommandType::Pour},
    {"cwjh/cmd/move", CommandType::Move},
    {"cwjh/cmd/stop", CommandType::Stop},
    {"cwjh/cmd/state", CommandType::State},
};

float round1(float value) { return roundf(value * 10.0f) / 10.0f; }

bool readNumber(JsonVariantConst v, float& out) {
  if (!v.is<float>()) return false;
  out = v.as<float>();
  return isfinite(out);
}

}  // namespace

float clampDegrees(float degrees) {
  if (degrees < 0.0f) return 0.0f;
  if (degrees > MAX_TILT_DEG) return MAX_TILT_DEG;
  return degrees;
}

ParseResult parseCommand(const char* topic, const uint8_t* payload, size_t len, Command& out) {
  out = Command{};
  for (const auto& t : TOPICS) {
    if (strcmp(topic, t.topic) == 0) out.type = t.type;
  }
  if (out.type == CommandType::None) return ParseResult::UnknownTopic;

  JsonDocument doc;
  if (deserializeJson(doc, payload, len) || !doc.is<JsonObject>()) return ParseResult::Invalid;

  const char* id = doc["id"].as<const char*>();
  if (id == nullptr || id[0] == '\0' || strlen(id) >= ID_SIZE) return ParseResult::Invalid;
  strncpy(out.id, id, ID_SIZE - 1);

  if (out.type == CommandType::Pour || out.type == CommandType::Move) {
    if (!doc["bay"].is<int>()) return ParseResult::Invalid;
    out.bay = doc["bay"].as<int>();
    if (out.bay < 1 || out.bay > BAY_COUNT) return ParseResult::Invalid;
  }

  if (out.type == CommandType::Pour) {
    if (!readNumber(doc["ml"], out.ml) || out.ml <= 0.0f || out.ml > MAX_GLASS_ML) return ParseResult::Invalid;
    if (!doc["g_per_ml"].isNull()) {
      if (!readNumber(doc["g_per_ml"], out.g_per_ml)) return ParseResult::Invalid;
    }
    if (out.g_per_ml < MIN_G_PER_ML || out.g_per_ml > MAX_G_PER_ML) return ParseResult::Invalid;
  }

  if (out.type == CommandType::Move) {
    if (!readNumber(doc["degrees"], out.degrees)) return ParseResult::Invalid;
    // The wiki says angles beyond the limit are cut, not rejected.
    out.degrees = clampDegrees(out.degrees);
  }
  return ParseResult::Ok;
}

size_t formatAck(char* buf, size_t size, const char* id, const char* status, const char* code, float poured_ml) {
  JsonDocument doc;
  doc["id"] = id;
  doc["status"] = status;
  if (code) doc["code"] = code;
  if (poured_ml >= 0.0f) doc["poured_ml"] = round1(poured_ml);
  return serializeJson(doc, buf, size);
}

size_t formatProgress(char* buf, size_t size, const char* id, float grams, float target) {
  JsonDocument doc;
  doc["id"] = id;
  doc["grams"] = round1(grams);
  doc["target"] = round1(target);
  return serializeJson(doc, buf, size);
}

size_t formatState(char* buf, size_t size, const HubState& state, const char* fw) {
  static const char* LIFT_NAMES[] = {"down", "up", "moving"};
  JsonDocument doc;
  doc["busy"] = state.busy;
  doc["scale_g"] = round1(state.scale_g);
  doc["glass"] = state.glass;
  doc["lift"] = LIFT_NAMES[static_cast<int>(state.lift)];
  doc["poured_ml"] = round1(state.poured_ml);
  JsonArray bays = doc["bays"].to<JsonArray>();
  for (float deg : state.bays) bays.add(round1(deg));
  doc["fw"] = fw;
  return serializeJson(doc, buf, size);
}

}  // namespace cwjh
