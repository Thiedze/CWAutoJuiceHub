// HX711 load cell, read without blocking and smoothed.
#pragma once

#include <HX711.h>

class Scale {
 public:
  void begin(uint8_t dout, uint8_t sck, float factor);
  // Call every loop. Returns true when a new reading came in.
  bool update();
  // Weight relative to the empty scale (zeroed at boot).
  float grossGrams() const { return gross_g_; }
  // Weight relative to the last tare (the glass).
  float netGrams() const { return gross_g_ - tare_g_; }
  void tare() { tare_g_ = gross_g_; }
  void clearTare() { tare_g_ = 0.0f; }

 private:
  HX711 hx_;
  float factor_ = 1.0f;
  long zero_raw_ = 0;
  float gross_g_ = 0.0f;
  float tare_g_ = 0.0f;
  bool primed_ = false;
};
