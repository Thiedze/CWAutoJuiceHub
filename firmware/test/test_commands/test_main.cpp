// Host-side tests: pio test -e native
#include <string.h>
#include <unity.h>

#include "commands.h"

using namespace cwjh;

void setUp() {}
void tearDown() {}

void test_parses_go() {
  Request r;
  TEST_ASSERT_EQUAL(ParseResult::Ok, parseRequest("7 G 1 -400", r));
  TEST_ASSERT_EQUAL(7, r.seq);
  TEST_ASSERT_TRUE(r.op == Op::Go);
  TEST_ASSERT_EQUAL(1, r.bay);
  TEST_ASSERT_EQUAL(-400, r.value);
}

void test_parses_other_requests() {
  Request r;
  TEST_ASSERT_EQUAL(ParseResult::Ok, parseRequest("1 S", r));
  TEST_ASSERT_TRUE(r.op == Op::Stop);
  TEST_ASSERT_EQUAL(ParseResult::Ok, parseRequest("2 Q", r));
  TEST_ASSERT_TRUE(r.op == Op::Query);
  TEST_ASSERT_EQUAL(ParseResult::Ok, parseRequest("3 P 2 250 40 300 600", r));
  TEST_ASSERT_TRUE(r.op == Op::Profile);
  TEST_ASSERT_EQUAL(2, r.bay);
  TEST_ASSERT_EQUAL(250, r.value);
  TEST_ASSERT_EQUAL(40, r.minSpeed);
  TEST_ASSERT_EQUAL(300, r.startMs);
  TEST_ASSERT_EQUAL(600, r.endMs);
  TEST_ASSERT_EQUAL(ParseResult::Ok, parseRequest("4 H 12 1", r));
  TEST_ASSERT_TRUE(r.op == Op::Hold);
  TEST_ASSERT_EQUAL(12, r.bay);
  TEST_ASSERT_EQUAL(1, r.value);
}

void test_parses_find_zero_and_switches() {
  Request r;
  TEST_ASSERT_EQUAL(ParseResult::Ok, parseRequest("8 F 1 -600 1", r));
  TEST_ASSERT_TRUE(r.op == Op::Find);
  TEST_ASSERT_EQUAL(1, r.bay);
  TEST_ASSERT_EQUAL(-600, r.value);
  TEST_ASSERT_EQUAL(1, r.level);
  TEST_ASSERT_EQUAL(ParseResult::Ok, parseRequest("9 Z 2", r));
  TEST_ASSERT_TRUE(r.op == Op::Zero);
  TEST_ASSERT_EQUAL(2, r.bay);
  TEST_ASSERT_EQUAL(ParseResult::Ok, parseRequest("10 W", r));
  TEST_ASSERT_TRUE(r.op == Op::Switches);
}

void test_tolerates_extra_spaces() {
  Request r;
  TEST_ASSERT_EQUAL(ParseResult::Ok, parseRequest("  65535   G  2  30  ", r));
  TEST_ASSERT_EQUAL(65535, r.seq);
  TEST_ASSERT_EQUAL(2, r.bay);
  TEST_ASSERT_EQUAL(30, r.value);
}

void test_empty_and_missing_seq() {
  Request r;
  TEST_ASSERT_EQUAL(ParseResult::Empty, parseRequest("", r));
  TEST_ASSERT_EQUAL(ParseResult::Empty, parseRequest("   ", r));
  TEST_ASSERT_EQUAL(ParseResult::NoSeq, parseRequest("G 1 10", r));
  TEST_ASSERT_EQUAL(ParseResult::NoSeq, parseRequest("70000 Q", r));
  TEST_ASSERT_EQUAL(ParseResult::NoSeq, parseRequest("-1 Q", r));
}

void test_rejects_invalid_requests_but_keeps_seq() {
  const char* bad[] = {
      "5",            // no op
      "5 X",          // unknown op
      "5 GO 1 10",    // op is one letter
      "5 G 1",        // missing position
      "5 G 0 10",     // bay 0
      "5 G 13 10",    // bay > 12
      "5 G 1 100001", // too far
      "5 G 1 1x",     // junk number
      "5 G 1 10 9",   // extra argument
      "5 H 1 2",      // hold is 0 or 1
      "5 V 300",      // V is gone, speed is part of P
      "5 P 1 300 50 0",       // P needs 5 values
      "5 P 1 0 1 0 0",        // top speed 0
      "5 P 1 5001 50 0 0",    // too fast
      "5 P 1 300 301 0 0",    // start speed above top speed
      "5 P 1 300 50 10001 0", // start curve too long
      "5 P 13 300 50 0 0",    // bay > 12
      "5 S now",      // stop takes nothing
      "5 F 1 10",     // find needs a level
      "5 F 1 10 2",   // level is 0 or 1
      "5 Z",          // zero needs a bay
      "5 Z 13",       // bay > 12
      "5 W 1",        // switches takes nothing
  };
  for (const char* line : bad) {
    Request r;
    TEST_ASSERT_EQUAL_MESSAGE(ParseResult::Invalid, parseRequest(line, r), line);
    TEST_ASSERT_EQUAL_MESSAGE(5, r.seq, line);
  }
}

void test_formats_replies() {
  char buf[64];
  formatReady(buf, sizeof(buf), "0.2.0", 1);
  TEST_ASSERT_EQUAL_STRING("READY 0.2.0 1", buf);
  formatOk(buf, sizeof(buf), 3);
  TEST_ASSERT_EQUAL_STRING("3 OK", buf);
  formatOkValue(buf, sizeof(buf), 4, -267);
  TEST_ASSERT_EQUAL_STRING("4 OK -267", buf);
  formatErr(buf, sizeof(buf), 5, "no_motor");
  TEST_ASSERT_EQUAL_STRING("5 ERR no_motor", buf);
  const long pos[] = {267, 0, -12};
  formatState(buf, sizeof(buf), 6, true, pos, 3);
  TEST_ASSERT_EQUAL_STRING("6 STATE 1 267 0 -12", buf);
}

void test_formats_find_and_switches() {
  char buf[64];
  formatFound(buf, sizeof(buf), 7, -12, true);
  TEST_ASSERT_EQUAL_STRING("7 OK -12 1", buf);
  formatFound(buf, sizeof(buf), 8, -600, false);
  TEST_ASSERT_EQUAL_STRING("8 OK -600 0", buf);
  const int8_t states[] = {1, 0, -1};
  formatSwitches(buf, sizeof(buf), 9, states, 3);
  TEST_ASSERT_EQUAL_STRING("9 SW 1 0 -", buf);
}

void test_state_is_truncated_safely() {
  char buf[16];
  const long pos[] = {100000, 100000, 100000};
  formatState(buf, sizeof(buf), 1, false, pos, 3);
  TEST_ASSERT_EQUAL('\0', buf[sizeof(buf) - 1]);
}

int main(int, char**) {
  UNITY_BEGIN();
  RUN_TEST(test_parses_go);
  RUN_TEST(test_parses_other_requests);
  RUN_TEST(test_parses_find_zero_and_switches);
  RUN_TEST(test_tolerates_extra_spaces);
  RUN_TEST(test_empty_and_missing_seq);
  RUN_TEST(test_rejects_invalid_requests_but_keeps_seq);
  RUN_TEST(test_formats_replies);
  RUN_TEST(test_formats_find_and_switches);
  RUN_TEST(test_state_is_truncated_safely);
  return UNITY_END();
}
