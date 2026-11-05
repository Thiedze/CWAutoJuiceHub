// Bring-up test: spin one bay stepper (DRV8825) through the SX1509 I/O expander
// on an Arduino Nano. Driven from the serial monitor (115200 baud, newline).
//
// Every STEP pulse is two I2C writes to the SX1509, so the step rate is limited
// to a few hundred steps per second. Good enough to check wiring and direction.

#include <Arduino.h>
#include <Wire.h>  // must come before SparkFunSX1509.h, which uses TwoWire
#include <SparkFunSX1509.h>

// SX1509 address with the ADDR jumpers untouched.
constexpr uint8_t SX1509_ADDR = 0x3E;
// 100 kHz is the safe choice on a breadboard; 400000 gives roughly 3x the step rate.
constexpr uint32_t I2C_CLOCK = 100000;

// SX1509 I/O numbers (0..15) wired to the DRV8825. STEP and DIR can be
// changed at runtime with the p command to find the right wiring.
uint8_t sxStep = 1;
uint8_t sxDir = 2;
constexpr uint8_t SX_ENABLE = 3;  // active low on the DRV8825

SX1509 io;
uint16_t stepsPerSecond = 200;  // full step: one turn per second
bool hold = false;              // keep the driver enabled after a move

void scanI2c() {
  Serial.print(F("I2C devices:"));
  uint8_t found = 0;
  for (uint8_t addr = 1; addr < 127; addr++) {
    Wire.beginTransmission(addr);
    if (Wire.endTransmission() == 0) {
      Serial.print(F(" 0x"));
      Serial.print(addr, HEX);
      found++;
    }
  }
  Serial.println(found ? "" : " none (check SDA=A4, SCL=A5, 3V3, GND)");
}

void setEnabled(bool on) { io.digitalWrite(SX_ENABLE, on ? LOW : HIGH); }

void setStepDirPins(uint8_t step, uint8_t dir) {
  sxStep = step;
  sxDir = dir;
  io.pinMode(sxStep, OUTPUT);
  io.pinMode(sxDir, OUTPUT);
  io.digitalWrite(sxStep, LOW);
}

// Reads a 16-bit SX1509 register pair (bank B first, then bank A). RegData
// returns the level actually present on each I/O, also for outputs; the
// library's digitalRead() only works for inputs.
uint16_t readSxWord(uint8_t reg) {
  Wire.beginTransmission(SX1509_ADDR);
  Wire.write(reg);
  if (Wire.endTransmission(false) != 0) return 0xFFFF;
  if (Wire.requestFrom(SX1509_ADDR, (uint8_t)2) != 2) return 0xFFFF;
  const uint8_t b = Wire.read();
  const uint8_t a = Wire.read();
  return ((uint16_t)b << 8) | a;
}

constexpr uint8_t SX_REG_DIR_B = 0x0E;   // 1 = input
constexpr uint8_t SX_REG_DATA_B = 0x10;

bool pinLevel(uint8_t pin) { return readSxWord(SX_REG_DATA_B) & (1u << pin); }

// Drives each pin high and low and reads back what is really on the pin.
// A pin that stays low while driven high is shorted, overloaded, or its bank
// supply (VCC1 for 0..7, VCC2 for 8..15) is missing.
void selfCheck() {
  Serial.print(F("RegDir  "));
  Serial.println(readSxWord(SX_REG_DIR_B), BIN);
  Serial.print(F("RegData "));
  Serial.println(readSxWord(SX_REG_DATA_B), BIN);

  const uint8_t pins[] = {sxStep, sxDir, SX_ENABLE};
  const char *names[] = {"STEP", "DIR", "ENABLE"};
  for (uint8_t i = 0; i < 3; i++) {
    io.digitalWrite(pins[i], HIGH);
    delay(5);
    const bool high = pinLevel(pins[i]);
    io.digitalWrite(pins[i], LOW);
    delay(5);
    const bool low = pinLevel(pins[i]);
    Serial.print(names[i]);
    Serial.print(F(" (pin "));
    Serial.print(pins[i]);
    Serial.print(F("): set HIGH reads "));
    Serial.print(high);
    Serial.print(F(", set LOW reads "));
    Serial.print(low);
    Serial.println(high && !low ? F("  ok") : F("  FAIL"));
  }
  setEnabled(hold);
}

void printStatus() {
  Serial.print(F("STEP "));
  Serial.print(sxStep);
  Serial.print(F(", DIR "));
  Serial.print(sxDir);
  Serial.print(F(", ENABLE "));
  Serial.print(SX_ENABLE);
  Serial.print(F(", speed "));
  Serial.print(stepsPerSecond);
  Serial.print(F(" steps/s, hold "));
  Serial.println(hold ? F("on") : F("off"));
}

void printHelp() {
  Serial.println(F("Commands:"));
  Serial.println(F("  m <steps>   move, negative = other direction (any key aborts)"));
  Serial.println(F("  v <n>       speed in steps/s"));
  Serial.println(F("  t           test: 200 steps forward, then back"));
  Serial.println(F("  p <s> <d>   use SX1509 pin s as STEP and pin d as DIR"));
  Serial.println(F("  h           toggle hold (driver stays enabled, motor keeps torque)"));
  Serial.println(F("  x           self-check: drive STEP, DIR, ENABLE and read the real pin level"));
  Serial.println(F("  ?           this help"));
  printStatus();
}

// Returns the number of steps actually done.
long move(long steps) {
  io.digitalWrite(sxDir, steps >= 0 ? HIGH : LOW);
  setEnabled(true);
  delay(2);  // DRV8825 wake-up after ENABLE

  const long total = labs(steps);
  const uint32_t period = 1000000UL / stepsPerSecond;
  const uint32_t startMs = millis();
  uint32_t next = micros();
  long done = 0;

  while (done < total) {
    if (Serial.available()) {
      while (Serial.available()) Serial.read();
      Serial.println(F("aborted"));
      break;
    }
    io.digitalWrite(sxStep, HIGH);
    io.digitalWrite(sxStep, LOW);
    done++;
    next += period;
    while ((int32_t)(micros() - next) < 0) {
    }
    // If I2C is slower than the requested rate, don't try to catch up.
    if ((int32_t)(micros() - next) > (int32_t)period) next = micros();
  }

  const uint32_t ms = millis() - startMs;
  if (!hold) setEnabled(false);

  Serial.print(done);
  Serial.print(F(" steps in "));
  Serial.print(ms);
  Serial.print(F(" ms"));
  if (ms > 0) {
    Serial.print(F(" = "));
    Serial.print(done * 1000L / (long)ms);
    Serial.print(F(" steps/s"));
  }
  Serial.println();
  return done;
}

void handle(char *line) {
  while (*line == ' ') line++;
  const char cmd = *line;
  char *rest;
  const long arg = strtol(line + 1, &rest, 10);
  const long arg2 = strtol(rest, nullptr, 10);

  switch (cmd) {
    case 'm':
      move(arg);
      break;
    case 'v':
      if (arg < 1 || arg > 5000) {
        Serial.println(F("speed must be 1..5000"));
      } else {
        stepsPerSecond = arg;
        printStatus();
      }
      break;
    case 't':
      if (move(200) == 200) {
        delay(500);
        move(-200);
      }
      break;
    case 'p':
      if (arg < 0 || arg > 15 || arg2 < 0 || arg2 > 15 || arg == arg2 || arg == SX_ENABLE ||
          arg2 == SX_ENABLE) {
        Serial.println(F("pins must be 0..15, different, and not ENABLE"));
      } else {
        setStepDirPins(arg, arg2);
        printStatus();
      }
      break;
    case 'h':
      hold = !hold;
      setEnabled(hold);
      printStatus();
      break;
    case 'x':
      selfCheck();
      break;
    case '?':
      printHelp();
      break;
    case '\0':
      break;
    default:
      Serial.println(F("unknown command, ? for help"));
  }
}

void setup() {
  Serial.begin(115200);
  Serial.println(F("\nnano-stepper bring-up"));

  Wire.begin();
  scanI2c();

  if (!io.begin(SX1509_ADDR)) {
    Serial.println(F("SX1509 not found at 0x3E. Fix wiring and reset."));
    while (true) {
    }
  }
  Wire.setClock(I2C_CLOCK);

  // Disable the driver before anything else so the motor does not jump.
  io.pinMode(SX_ENABLE, OUTPUT);
  setEnabled(false);
  setStepDirPins(sxStep, sxDir);

  Serial.println(F("SX1509 ok, driver disabled."));
  printHelp();
}

void loop() {
  static char buf[24];
  static uint8_t len = 0;

  while (Serial.available()) {
    const char c = Serial.read();
    if (c == '\r') continue;
    if (c == '\n') {
      buf[len] = '\0';
      handle(buf);
      len = 0;
    } else if (len < sizeof(buf) - 1) {
      buf[len++] = c;
    }
  }
}
