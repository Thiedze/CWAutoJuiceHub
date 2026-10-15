#include "link.h"

#include "commands.h"

#if __has_include("secrets.h")
#include "secrets.h"
#else
#warning "secrets.h not found, using placeholder values from secrets.example.h"
#include "secrets.example.h"
#endif

namespace {
constexpr uint32_t RETRY_MS = 2000;
constexpr const char* CLIENT_ID = "cwjh-hub";
}  // namespace

void Link::begin(MessageHandler on_message, ConnectHandler on_connect) {
  on_message_ = on_message;
  on_connect_ = on_connect;
  WiFi.mode(WIFI_STA);
  WiFi.setAutoReconnect(true);
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);

  mqtt_.setServer(MQTT_HOST, MQTT_PORT);
  mqtt_.setBufferSize(512);
  mqtt_.setKeepAlive(15);
  mqtt_.setCallback([this](char* topic, uint8_t* payload, unsigned int len) { on_message_(topic, payload, len); });
}

void Link::loop() {
  if (mqtt_.connected()) {
    mqtt_.loop();
    return;
  }
  if (WiFi.status() != WL_CONNECTED) return;
  uint32_t now = millis();
  if (now - last_attempt_ms_ < RETRY_MS) return;
  last_attempt_ms_ = now;

  const char* user = MQTT_USERNAME[0] ? MQTT_USERNAME : nullptr;
  const char* pass = MQTT_PASSWORD[0] ? MQTT_PASSWORD : nullptr;
  if (mqtt_.connect(CLIENT_ID, user, pass, cwjh::TOPIC_ONLINE, 1, true, "0")) {
    mqtt_.subscribe(cwjh::TOPIC_CMD_ALL, 1);
    mqtt_.publish(cwjh::TOPIC_ONLINE, "1", true);
    if (on_connect_) on_connect_();
  }
}

void Link::publish(const char* topic, const char* payload, bool retain) {
  if (mqtt_.connected()) mqtt_.publish(topic, payload, retain);
}
