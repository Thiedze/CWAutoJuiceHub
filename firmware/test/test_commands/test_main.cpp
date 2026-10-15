// Host-side tests: pio test -e native
#include <string.h>
#include <unity.h>

#include "commands.h"
#include "pour.h"

using namespace cwjh;

static ParseResult parse(const char* topic, const char* json, Command& cmd) {
  return parseCommand(topic, reinterpret_cast<const uint8_t*>(json), strlen(json), cmd);
}

void setUp() {}
void tearDown() {}

void test_parses_pour() {
  Command cmd;
  TEST_ASSERT_EQUAL(ParseResult::Ok, parse("cwjh/cmd/pour", R"({"id":"a1","bay":3,"ml":50,"g_per_ml":1.3})", cmd));
  TEST_ASSERT_EQUAL(CommandType::Pour, cmd.type);
  TEST_ASSERT_EQUAL_STRING("a1", cmd.id);
  TEST_ASSERT_EQUAL(3, cmd.bay);
  TEST_ASSERT_FLOAT_WITHIN(0.001, 50.0, cmd.ml);
  TEST_ASSERT_FLOAT_WITHIN(0.001, 1.3, cmd.g_per_ml);
}

void test_pour_density_defaults_to_water() {
  Command cmd;
  TEST_ASSERT_EQUAL(ParseResult::Ok, parse("cwjh/cmd/pour", R"({"id":"a","bay":1,"ml":10})", cmd));
  TEST_ASSERT_FLOAT_WITHIN(0.001, 1.0, cmd.g_per_ml);
}

void test_rejects_bad_pours() {
  Command cmd;
  const char* bad[] = {
      R"({"id":"a","bay":0,"ml":10})",
      R"({"id":"a","bay":13,"ml":10})",
      R"({"id":"a","bay":1,"ml":0})",
      R"({"id":"a","bay":1,"ml":401})",
      R"({"id":"a","bay":1,"ml":"lots"})",
      R"({"id":"a","bay":1,"ml":10,"g_per_ml":5})",
      R"({"id":"a","ml":10})",
  };
  for (const char* json : bad) {
    TEST_ASSERT_EQUAL_MESSAGE(ParseResult::Invalid, parse("cwjh/cmd/pour", json, cmd), json);
    TEST_ASSERT_EQUAL_STRING("a", cmd.id);  // id kept so the error ack can be matched
  }
}

void test_move_degrees_are_cut() {
  Command cmd;
  TEST_ASSERT_EQUAL(ParseResult::Ok, parse("cwjh/cmd/move", R"({"id":"m","bay":12,"degrees":90})", cmd));
  TEST_ASSERT_FLOAT_WITHIN(0.001, 30.0, cmd.degrees);
  TEST_ASSERT_EQUAL(ParseResult::Ok, parse("cwjh/cmd/move", R"({"id":"m","bay":12,"degrees":-4})", cmd));
  TEST_ASSERT_FLOAT_WITHIN(0.001, 0.0, cmd.degrees);
}

void test_rejects_missing_or_long_id() {
  Command cmd;
  TEST_ASSERT_EQUAL(ParseResult::Invalid, parse("cwjh/cmd/stop", R"({})", cmd));
  TEST_ASSERT_EQUAL(ParseResult::Invalid, parse("cwjh/cmd/stop", R"({"id":""})", cmd));
  TEST_ASSERT_EQUAL(ParseResult::Invalid,
                    parse("cwjh/cmd/stop", R"({"id":"0123456789012345678901234567890123456789"})", cmd));
  TEST_ASSERT_EQUAL(ParseResult::Invalid, parse("cwjh/cmd/stop", "not json", cmd));
}

void test_unknown_topic() {
  Command cmd;
  TEST_ASSERT_EQUAL(ParseResult::UnknownTopic, parse("cwjh/cmd/dance", R"({"id":"x"})", cmd));
}

void test_format_ack() {
  char buf[128];
  formatAck(buf, sizeof buf, "a1", "error", "bay_empty", 12.34f);
  TEST_ASSERT_EQUAL_STRING(R"({"id":"a1","status":"error","code":"bay_empty","poured_ml":12.3})", buf);
  formatAck(buf, sizeof buf, "a1", "done", nullptr, -1.0f);
  TEST_ASSERT_EQUAL_STRING(R"({"id":"a1","status":"done"})", buf);
}

void test_format_state() {
  char buf[256];
  HubState s;
  s.glass = true;
  s.lift = Lift::Up;
  s.bays[2] = 30.0f;
  formatState(buf, sizeof buf, s, "t");
  TEST_ASSERT_NOT_NULL(strstr(buf, R"("lift":"up")"));
  TEST_ASSERT_NOT_NULL(strstr(buf, R"("bays":[0,0,30,0,0,0,0,0,0,0,0,0])"));
}

static const PourConfig CFG{3.0f, 2.0f, 3000, 30.0f, 10000, 5.0f};

void test_pour_stops_early_for_inflight() {
  PourController pour(CFG);
  pour.begin(50.0f, 100.0f, 0);
  TEST_ASSERT_EQUAL(PourOutcome::Running, pour.update(120.0f, 1000));
  TEST_ASSERT_EQUAL(PourOutcome::Running, pour.update(146.9f, 2000));
  TEST_ASSERT_EQUAL(PourOutcome::Done, pour.update(147.0f, 2100));
  TEST_ASSERT_FLOAT_WITHIN(0.01, 47.0, pour.pouredGrams());
}

void test_pour_detects_empty_bay() {
  PourController pour(CFG);
  pour.begin(50.0f, 0.0f, 0);
  TEST_ASSERT_EQUAL(PourOutcome::Running, pour.update(10.0f, 500));
  TEST_ASSERT_EQUAL(PourOutcome::Running, pour.update(11.0f, 3000));  // +1 g, not enough to reset
  TEST_ASSERT_EQUAL(PourOutcome::BayEmpty, pour.update(11.5f, 3500));
  TEST_ASSERT_FLOAT_WITHIN(0.01, 11.5, pour.pouredGrams());
}

void test_pour_detects_removed_glass() {
  PourController pour(CFG);
  pour.begin(50.0f, 20.0f, 0);
  TEST_ASSERT_EQUAL(PourOutcome::NoGlass, pour.update(-250.0f, 100));
}

void test_pour_times_out() {
  PourController pour(CFG);
  pour.begin(50.0f, 0.0f, 0);  // timeout = 10 s + 50 / 5 s = 20 s
  float g = 0.0f;
  uint32_t t = 0;
  for (; t < 19000; t += 1000) {
    g += 2.0f;  // slow, but never stalls
    TEST_ASSERT_EQUAL(PourOutcome::Running, pour.update(g, t));
  }
  TEST_ASSERT_EQUAL(PourOutcome::Timeout, pour.update(g + 2.0f, 20000));
}

void test_pour_survives_millis_wrap() {
  PourController pour(CFG);
  uint32_t start = 0xFFFFFF00u;
  pour.begin(50.0f, 0.0f, start);
  TEST_ASSERT_EQUAL(PourOutcome::Running, pour.update(5.0f, start + 1000));  // wrapped
  TEST_ASSERT_EQUAL(PourOutcome::Done, pour.update(48.0f, start + 2000));
}

int main() {
  UNITY_BEGIN();
  RUN_TEST(test_parses_pour);
  RUN_TEST(test_pour_density_defaults_to_water);
  RUN_TEST(test_rejects_bad_pours);
  RUN_TEST(test_move_degrees_are_cut);
  RUN_TEST(test_rejects_missing_or_long_id);
  RUN_TEST(test_unknown_topic);
  RUN_TEST(test_format_ack);
  RUN_TEST(test_format_state);
  RUN_TEST(test_pour_stops_early_for_inflight);
  RUN_TEST(test_pour_detects_empty_bay);
  RUN_TEST(test_pour_detects_removed_glass);
  RUN_TEST(test_pour_times_out);
  RUN_TEST(test_pour_survives_millis_wrap);
  return UNITY_END();
}
