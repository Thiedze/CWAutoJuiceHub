// Wi-Fi and MQTT with automatic reconnect and a last will.
#pragma once

#include <PubSubClient.h>
#include <WiFi.h>

#include <functional>

class Link {
 public:
  using MessageHandler = std::function<void(const char* topic, const uint8_t* payload, unsigned int len)>;
  using ConnectHandler = std::function<void()>;

  void begin(MessageHandler on_message, ConnectHandler on_connect);
  // Call every loop; reconnects without blocking for long.
  void loop();
  bool connected() { return mqtt_.connected(); }
  void publish(const char* topic, const char* payload, bool retain = false);

 private:
  WiFiClient wifi_;
  PubSubClient mqtt_{wifi_};
  MessageHandler on_message_;
  ConnectHandler on_connect_;
  uint32_t last_attempt_ms_ = 0;
};
