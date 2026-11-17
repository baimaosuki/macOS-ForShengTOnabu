/* Read-only fallback when persistent logging is unavailable. The msgbuf ABI
 * is the same one used by SHENG BootLog.c. No allocation, locks or I/O here. */
#ifndef PANIC_MSGBUFP
#define PANIC_MSGBUFP 0UL
#endif
#ifndef PANIC_NABU_STATE
#define PANIC_NABU_STATE 0UL
#define PANIC_NABU_ROUNDS 0UL
#define PANIC_NABU_FAILURES 0UL
#define PANIC_NABU_MEMORY 0UL
#endif
struct panic_msgbuf { int magic, size, write, read; const char *bytes; };

static int diagnostic_span (const void *p, u64 n)
{
  u64 at = (u64)p, end = at + n;
  if (!n || end < at || !readable ((const char *)at, 0)) return 0;
  for (at = (at & ~4095UL) + 4096; at < end; at += 4096)
    if (!readable ((const char *)at, 0)) return 0;
  return 1;
}

static char diagnostic_byte (const struct panic_msgbuf *m, u32 at)
{
  return m->bytes[((u32)m->write + at) % (u32)m->size];
}

static int diagnostic_match (const struct panic_msgbuf *m, u32 at, const char *s)
{
  for (; *s; ++s, ++at)
    if (at >= (u32)m->size || diagnostic_byte (m, at) != *s) return 0;
  return 1;
}

static void panic_diagnostics (struct ctx *g)
{
  if (PANIC_NABU_STATE) {
    text (g, "\nESP writer state="); number (g, *(volatile const u32 *)PANIC_NABU_STATE, 10, 0, 0);
    text (g, " commits="); number (g, *(volatile const u32 *)PANIC_NABU_ROUNDS, 10, 0, 0);
    text (g, " failures="); number (g, *(volatile const u32 *)PANIC_NABU_FAILURES, 10, 0, 0);
    text (g, " memory="); number (g, *(volatile const u32 *)PANIC_NABU_MEMORY, 10, 0, 0);
  }
  if (!PANIC_MSGBUFP) return;
  const struct panic_msgbuf *m = *(const struct panic_msgbuf *const *)PANIC_MSGBUFP;
  if (!diagnostic_span (m, sizeof *m) || m->size <= 0 || m->size > 1024*1024 ||
      m->write < 0 || m->write >= m->size || !diagnostic_span (m->bytes, (u32)m->size)) {
    text (g, "\nUser context unavailable: invalid msgbuf"); return;
  }
  u32 last = (u32)m->size, status = (u32)m->size;
  for (u32 at = 0; at < (u32)m->size; ++at) {
    if (diagnostic_match (m, at, "NABU-CRITICAL: pid=")) last = at;
    if (diagnostic_match (m, at, "NABULOG2: ") &&
        !diagnostic_match (m, at, "NABULOG2: critical-exit")) status = at;
  }
  /* The last writer line names a refused GPT/FAT/token gate or completion. */
  if (status < (u32)m->size) {
    put (g, '\n');
    for (u32 n = 0; n < 180 && status+n < (u32)m->size; ++n) {
      char c = diagnostic_byte (m, status+n); if (!c || c == '\n') break; put (g, c);
    }
  }
  if (last == (u32)m->size) { text (g, "\nUser context unavailable: no critical record in msgbuf"); return; }
  unsigned frames = 0;
  for (u32 at = last; at < (u32)m->size;) {
    int frame = diagnostic_match (m, at, "NABU-CRITICAL: frame=");
    int show = diagnostic_match (m, at, "NABU-CRITICAL: ") &&
      (!frame || frames++ < 12) && !diagnostic_match (m, at, "NABU-CRITICAL: image=");
    if (show) put (g, '\n');
    for (u32 n = 0; at < (u32)m->size; ++at, ++n) {
      char c = diagnostic_byte (m, at);
      if (c == '\n') { ++at; break; }
      if (show && c && n < 180) put (g, c);
    }
  }
}
