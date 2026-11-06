#include "commands.h"

#include <stdio.h>
#include <stdlib.h>

namespace cwjh {

namespace {

const char* skipSpaces(const char* p) {
  while (*p == ' ' || *p == '\t') p++;
  return p;
}

// Reads one integer that must be followed by a space or the end of the line.
bool readLong(const char*& p, long& out) {
  p = skipSpaces(p);
  char* end = nullptr;
  out = strtol(p, &end, 10);
  if (end == p) return false;
  if (*end != '\0' && *end != ' ' && *end != '\t') return false;
  p = end;
  return true;
}

bool atEnd(const char* p) { return *skipSpaces(p) == '\0'; }

}  // namespace

ParseResult parseRequest(const char* line, Request& out) {
  const char* p = skipSpaces(line);
  if (*p == '\0') return ParseResult::Empty;

  long seq;
  if (!readLong(p, seq) || seq < 0 || seq > 65535) return ParseResult::NoSeq;
  out = Request();
  out.seq = static_cast<uint16_t>(seq);

  p = skipSpaces(p);
  const char op = *p;
  if (op == '\0') return ParseResult::Invalid;
  p++;
  if (*p != '\0' && *p != ' ' && *p != '\t') return ParseResult::Invalid;

  long a = 0;
  long b = 0;
  switch (op) {
    case 'G':
      if (!readLong(p, a) || !readLong(p, b) || !atEnd(p)) return ParseResult::Invalid;
      if (a < 1 || a > MAX_BAYS || b < -MAX_POSITION || b > MAX_POSITION) return ParseResult::Invalid;
      out.op = Op::Go;
      out.bay = static_cast<uint8_t>(a);
      out.value = b;
      return ParseResult::Ok;
    case 'H':
      if (!readLong(p, a) || !readLong(p, b) || !atEnd(p)) return ParseResult::Invalid;
      if (a < 1 || a > MAX_BAYS || (b != 0 && b != 1)) return ParseResult::Invalid;
      out.op = Op::Hold;
      out.bay = static_cast<uint8_t>(a);
      out.value = b;
      return ParseResult::Ok;
    case 'V':
      if (!readLong(p, a) || !atEnd(p)) return ParseResult::Invalid;
      if (a < 1 || a > MAX_STEPS_PER_S) return ParseResult::Invalid;
      out.op = Op::Speed;
      out.value = a;
      return ParseResult::Ok;
    case 'S':
      if (!atEnd(p)) return ParseResult::Invalid;
      out.op = Op::Stop;
      return ParseResult::Ok;
    case 'Q':
      if (!atEnd(p)) return ParseResult::Invalid;
      out.op = Op::Query;
      return ParseResult::Ok;
    default:
      return ParseResult::Invalid;
  }
}

size_t formatReady(char* buf, size_t size, const char* fw, uint8_t bays) {
  return snprintf(buf, size, "READY %s %u", fw, static_cast<unsigned>(bays));
}

size_t formatOk(char* buf, size_t size, uint16_t seq) {
  return snprintf(buf, size, "%u OK", static_cast<unsigned>(seq));
}

size_t formatOkValue(char* buf, size_t size, uint16_t seq, long value) {
  return snprintf(buf, size, "%u OK %ld", static_cast<unsigned>(seq), value);
}

size_t formatErr(char* buf, size_t size, uint16_t seq, const char* code) {
  return snprintf(buf, size, "%u ERR %s", static_cast<unsigned>(seq), code);
}

size_t formatState(char* buf, size_t size, uint16_t seq, bool busy, const long* positions, uint8_t bays) {
  int n = snprintf(buf, size, "%u STATE %d", static_cast<unsigned>(seq), busy ? 1 : 0);
  for (uint8_t i = 0; i < bays && n > 0 && static_cast<size_t>(n) < size; i++) {
    n += snprintf(buf + n, size - n, " %ld", positions[i]);
  }
  return n < 0 ? 0 : static_cast<size_t>(n);
}

}  // namespace cwjh
