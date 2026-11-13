#include <assert.h>
#include <stdint.h>
#include <stdio.h>
#include <stdarg.h>
#include <string.h>
#include <stdbool.h>
#include <stdlib.h>

#ifndef NABU_SESSION_EVI_ENTRY_SEQ
#define NABU_SESSION_EVI_ENTRY_SEQ 465U
#define NABU_SESSION_EVI_TIME_NS 403087435052ULL
#define NABU_SESSION_EVI_MSGID 30176U
#endif
#define NABU_SESSION_TRACE_HOST_TEST 1
#define MACH64_SEND_MSG 1U
#define MACH64_RCV_MSG 2U
#define MACH_PORT_TYPE_SEND 0x10000U
#define MACH_PORT_TYPE_RECEIVE 0x20000U
#define MACH_PORT_TYPE_SEND_ONCE 0x40000U
#define MACH_PORT_VALID(p) ((p) != 0U && (p) != UINT32_MAX)
#define IE_NULL NULL

static int enabled = 1, self_pid = 41, receiver_pid = 17;
static uint64_t tid = 111, now_ns = 900;
static const char *self_name = "WindowServer";
static int space_locked, port_locked, space_busy, port_busy;
static int space_active = 1, port_active = 1, entry_present = 1;
static unsigned lookups, pid_reads, peer_names, writes;
static char journal[8000000];
static size_t used;
struct ipc_port { int unused; } port;
typedef struct ipc_port *ipc_port_t;
struct ipc_entry { uint32_t ie_bits; ipc_port_t ie_port; } entry = {
    MACH_PORT_TYPE_SEND, &port
};
typedef struct ipc_entry *ipc_entry_t;
struct ipc_space { int is_lock; } space;
typedef struct ipc_space *ipc_space_t;
static int ipc_lck_grp;
static ipc_space_t current_space(void) { return &space; }
static int lck_ticket_lock_try(int *lock, int *grp) {
    assert(lock == &space.is_lock && grp == &ipc_lck_grp);
    assert(!space_locked && !port_locked);
    if (space_busy) return 0;
    space_locked = 1;
    return 1;
}
static int is_active(ipc_space_t p) { assert(p == &space && space_locked); return space_active; }
static void is_read_unlock(ipc_space_t p) {
    assert(p == &space && space_locked && !port_locked); space_locked = 0;
}
static ipc_entry_t ipc_entry_lookup(ipc_space_t p, uint32_t name) {
    assert(p == &space && space_locked && !port_locked && name == 0x123);
    lookups++;
    return entry_present ? &entry : NULL;
}
static int ip_mq_lock_try(ipc_port_t p) {
    assert(p == &port && space_locked && !port_locked);
    if (port_busy) return 0;
    port_locked = 1; return 1;
}
static void ip_mq_unlock(ipc_port_t p) { assert(p == &port && port_locked); port_locked = 0; }
static int ip_active(ipc_port_t p) { assert(p == &port && port_locked); return port_active; }
static int ipc_port_get_receiver_task_locked(ipc_port_t p, void *task_out) {
    assert(p == &port && space_locked && port_locked && task_out == NULL);
    pid_reads++; return receiver_pid;
}
static uint32_t ip_get_receiver_name(ipc_port_t p) {
    assert(p == &port && port_locked); return 0x987;
}
static int PE_parse_boot_argn(const char *name, void *out, int size) {
    assert(!space_locked && !port_locked);
    assert(!strcmp(name, "nabu-session-log") && size == 4);
    *(unsigned *)out = (unsigned)enabled; return 1;
}
static int proc_selfpid(void) { assert(!space_locked && !port_locked); return self_pid; }
static int current_thread(void) { return 1; }
static uint64_t thread_tid(int t) { assert(t == 1); return tid; }
static uint64_t mach_absolute_time(void) { return now_ns; }
static void absolutetime_to_nanoseconds(uint64_t t, uint64_t *out) { *out = t; }
static void proc_name(int pid, char *out, int size) {
    assert(!space_locked && !port_locked);
    if (pid == self_pid) snprintf(out, (size_t)size, "%s", self_name);
    else { assert(pid == receiver_pid); peer_names++; snprintf(out, (size_t)size, "launchd"); }
}
static void nabu_capture_append(const char *s, unsigned count) {
    assert(!space_locked && !port_locked && count < 512);
    assert(used + count < sizeof(journal));
    memcpy(journal + used, s, count); used += count; journal[used] = 0; writes++;
}
#include "session_trace.h"
#include "session_ipc.h"

static void reset(void) {
    memset(nabu_session_events, 0, sizeof(nabu_session_events));
    memset(nabu_session_sample_second, 0, sizeof(nabu_session_sample_second));
    now_ns = 900;
    memset(nabu_session_login, 0, sizeof(nabu_session_login));
    nabu_session_login_slots_drop_reported = 0;
    memset(nabu_session_calls, 0, sizeof(nabu_session_calls));
    used = writes = lookups = pid_reads = peer_names = 0; journal[0] = 0;
    enabled = space_active = port_active = entry_present = 1;
    self_name = "WindowServer"; self_pid = 41; tid = 111; receiver_pid = 17;
    space_busy = port_busy = space_locked = port_locked = 0;
    entry.ie_bits = MACH_PORT_TYPE_SEND;
}
static void snapshot_paths(void) {
    struct nabu_session_peer p;
    reset();
    p = nabu_session_peer_snapshot(0); assert(!strcmp(p.state, "invalid-name") && !lookups);
    p = nabu_session_peer_snapshot(UINT32_MAX); assert(!lookups);
    space_busy = 1; p = nabu_session_peer_snapshot(0x123);
    assert(!strcmp(p.state, "space-busy") && !lookups);
    space_busy = 0; space_active = 0; p = nabu_session_peer_snapshot(0x123);
    assert(!strcmp(p.state, "space-inactive") && !lookups);
    space_active = 1; entry_present = 0; p = nabu_session_peer_snapshot(0x123);
    assert(!strcmp(p.state, "invalid-name") && !pid_reads);
    entry_present = 1; entry.ie_bits = 0x80000; p = nabu_session_peer_snapshot(0x123);
    assert(!strcmp(p.state, "invalid-name") && !pid_reads);
    entry.ie_bits = MACH_PORT_TYPE_SEND_ONCE; port_busy = 1;
    p = nabu_session_peer_snapshot(0x123); assert(!strcmp(p.state, "port-busy") && !pid_reads);
    port_busy = 0; port_active = 0; p = nabu_session_peer_snapshot(0x123);
    assert(!strcmp(p.state, "port-inactive") && !pid_reads);
    port_active = 1; receiver_pid = -1; p = nabu_session_peer_snapshot(0x123);
    assert(!strcmp(p.state, "unknown") && p.pid == -1 && p.receiver_port == 0x987);
    receiver_pid = 17; p = nabu_session_peer_snapshot(0x123);
    assert(!strcmp(p.state, "pid-snapshot") && p.pid == 17);
    assert(!space_locked && !port_locked && !peer_names);
}
static void selection_and_continuation(void) {
    reset(); enabled = 0;
    struct nabu_session_call c = nabu_session_ipc_begin(3, 420, 0x123, 0x456, 0x789);
    assert(!c.selected && !writes && !lookups);
    enabled = 1; self_name = "lsd"; c = nabu_session_ipc_begin(3, 420, 0x123, 0x456, 0x789);
    assert(!c.selected && !writes && !lookups);
    self_name = "WindowServer"; c = nabu_session_ipc_begin(3, 420, 0x123, 0x456, 0x789);
    assert(c.selected && c.call && writes == 1 && lookups == 1 && peer_names == 1);
    assert(strstr(journal, "remote=123 local=456 receive=789 peer=pid-snapshot peer_pid=17"));
    assert(strstr(journal, "peer_name_at_lookup=launchd peer_receive=987 result=0 status=success"));
    /* An internal receive during send cannot steal an as-yet uninstalled call. */
    nabu_session_ipc_receive_result(2, 0x10004003);
    assert(strstr(journal, "call=0 assoc=unavailable options=2 msgid=0 msgid_known=0"));
    nabu_session_ipc_wait(&c);
    char needle[100]; snprintf(needle, sizeof(needle), "call=%llu assoc=entry options=3 msgid=420", (unsigned long long)c.call);
    used = 0; journal[0] = 0;
    nabu_session_ipc_receive_result(3, 0x10004004); /* continuation has no stack c */
    assert(strstr(journal, needle) && strstr(journal, "local=456 receive=789"));
    assert(strstr(journal, "result=10004004 status=too-large"));
    used = 0; journal[0] = 0;
    nabu_session_ipc_receive_result(2, 0x10004003);
    assert(strstr(journal, "assoc=unavailable") && strstr(journal, "status=timed-out"));
    /* Synchronous end still knows its stack-local association and final result. */
    nabu_session_ipc_end(&c, 0x10004003); assert(strstr(journal, needle));
    /* Receive-only packed send fields are explicitly not message metadata. */
    c = nabu_session_ipc_begin(2, 999, 0x123, 0x456, 0x789);
    assert(!c.msgid && !c.local && !c.remote && c.receive == 0x789);
}
static void collision_and_isolation(void) {
    reset();
    struct nabu_session_call a = nabu_session_ipc_begin(3, 11, 0x123, 2, 3);
    nabu_session_ipc_wait(&a);
    tid += 64; struct nabu_session_call b = nabu_session_ipc_begin(3, 22, 0x123, 4, 5);
    nabu_session_ipc_wait(&b);
    used = 0; journal[0] = 0; nabu_session_ipc_receive_result(3, 0x10004003);
    assert(strstr(journal, "assoc=unavailable") && !strstr(journal, "msgid=11"));
    tid -= 64; used = 0; journal[0] = 0; nabu_session_ipc_receive_result(3, 0x10004004);
    assert(strstr(journal, "msgid=11 msgid_known=1"));
    /* Busy correlation slot fails without spinning or touching its record. */
    unsigned index = (unsigned)tid % 64;
    nabu_session_calls[index].busy = 1;
    used = 0; journal[0] = 0; nabu_session_ipc_receive_result(3, 0x10004003);
    assert(strstr(journal, "assoc=unavailable"));
    /* If a continuation cannot remove its record, next entry invalidates it
     * without waiting for a colliding slot user. No old ID may be attributed. */
    nabu_session_calls[index].owner = tid;
    nabu_session_calls[index].record = a;
    struct nabu_session_call newer = nabu_session_ipc_begin(3, 66, 0x123, 2, 3);
    assert(newer.call != a.call && nabu_session_calls[index].owner == 0);
    nabu_session_calls[index].busy = 0;
    used = 0; journal[0] = 0;
    nabu_session_ipc_receive_result(2, 0x10004003);
    assert(strstr(journal, "assoc=unavailable") && !strstr(journal, "msgid=11"));
    reset(); self_name = "opendirectoryd";
    for (unsigned i = 0; i < 1000; i++) {
        struct nabu_session_call c = nabu_session_ipc_begin(3, i, 0x123, 2, 3);
        nabu_session_ipc_wait(&c); nabu_session_ipc_end(&c, 0x10004003);
    }
    assert(writes == 514); /* independent burst plus explicit exhaustion markers */
    assert(strstr(journal, "sample=budget-exhausted-now-1-per-sec"));
    /* OD still reports at 250 seconds; initial traffic cannot silence it. */
    now_ns = 250000000000ULL;
    struct nabu_session_call late = nabu_session_ipc_begin(3, 777, 0x123, 2, 3);
    nabu_session_ipc_end(&late, 0x10004004);
    assert(writes == 516 && strstr(journal, "sample=1-per-sec") && strstr(journal, "msgid=777"));
    /* Expected adjustment below accounts for these two late samples. */
    self_name = "WindowServer"; self_pid = 99;
    struct nabu_session_call c = nabu_session_ipc_begin(3, 333, 0x123, 2, 3);
    nabu_session_ipc_wait(&c); nabu_session_ipc_receive_result(3, 0x10004004);
    assert(writes == 519 && strstr(journal, "name=WindowServer") && strstr(journal, "msgid=333"));
    self_name = "loginwindow"; self_pid = 105;
    c = nabu_session_ipc_begin(3, 444, 0x123, 2, 3); nabu_session_ipc_end(&c, 0x10004003);
    assert(writes == 522 && strstr(journal, "name=loginwindow"));
}
static void error_and_truncation(void) {
    reset();
    for (unsigned i = 0; i < 800; i++) {
        struct nabu_session_call c = nabu_session_ipc_begin(3, i, 0x123, 2, 3);
        nabu_session_ipc_end(&c, 0);
    }
    unsigned before = writes;
    struct nabu_session_call c = nabu_session_ipc_begin(3, 55, 0x123, 2, 3);
    nabu_session_ipc_end(&c, 0x10004004);
    assert(writes == before + 1 && strstr(journal, "result=10004004 status=too-large"));
    reset();
    char long_text[900]; memset(long_text, 'x', sizeof(long_text) - 1); long_text[899] = 0;
    nabu_session_emit(3, "%s", long_text);
    assert(used == 511 && !strcmp(journal + used - 13, " truncated=1\n"));
    assert(journal[used - 1] == '\n');
}
static unsigned occurrences(const char *haystack, const char *needle) {
    unsigned count = 0;
    for (const char *p = haystack; (p = strstr(p, needle)); p += strlen(needle)) count++;
    return count;
}
static void loginwindow_call_admission(void) {
    reset(); self_name = "loginwindow"; self_pid = 168; tid = 620;
    /* Reproduce the real old channel state rather than rely on test clock
     * mirroring: entry seq465 at403s and success/error channels exhausted. */
    nabu_session_events[5] = NABU_SESSION_EVI_ENTRY_SEQ; nabu_session_events[6] = 380;
    nabu_session_events[10] = 300;
    nabu_session_sample_second[5] = nabu_session_sample_second[6] = 403;
    nabu_session_sample_second[10] = 403;
    now_ns = NABU_SESSION_EVI_TIME_NS;
    unsigned legacy_writes = writes;
    nabu_session_emit(5, "event=legacy-dropped-call\n");
    nabu_session_emit(6, "event=legacy-dropped-completion\n");
    assert(writes == legacy_writes); /* demonstrate the prior silent loss */
    struct nabu_session_call c = nabu_session_ipc_begin(3, NABU_SESSION_EVI_MSGID, 0x123, 0x1f0b, 0x1f0b);
    assert(c.selected && c.login_slot);
    char id[64]; snprintf(id, sizeof(id), "call=%llu ", (unsigned long long)c.call);
    /* Internal receives during send cannot close the admitted outer call. */
    unsigned before = writes;
    nabu_session_ipc_receive_result(2, 0x10004003); assert(writes == before);
    nabu_session_ipc_wait(&c); assert(writes == before); /* wait timestamp only */
    /* Another thread exhausts its complete epoch without spending our credit. */
    tid = 650;
    for (unsigned i = 0; i < 300; ++i) {
        struct nabu_session_call other = nabu_session_ipc_begin(1, 11, 0x123, 0, 0);
        nabu_session_ipc_end(&other, 0);
    }
    tid = 620;
    struct nabu_session_login_record *slot = nabu_session_login_find(tid, 0);
    assert(slot && slot->reserved_bytes == NABU_SESSION_LOGIN_CALL_BYTES);
    /* Simulate continuation after both400s phase and next-minute boundary. */
    now_ns = 465000000000ULL;
    nabu_session_ipc_receive_result(3, 0);
    assert(occurrences(journal, id) == 2);
    assert(strstr(journal, "msgid=30176 msgid_known=1 remote=123 local=1f0b receive=1f0b"));
    assert(strstr(journal, "result=0 result_known=1 stage=receive wait_ns=403087435052"));
    before = writes; nabu_session_ipc_end(&c, 0); assert(writes == before);
    assert(slot->reserved_bytes == NABU_SESSION_LOGIN_CALL_BYTES);
    /* Same-second success and error completions never compete for one sample. */
    for (unsigned i = 0; i < 6; ++i) {
        c = nabu_session_ipc_begin(3, 30176 + i, 0x123, 2, 3);
        nabu_session_ipc_wait(&c);
        before = writes;
        uint32_t result = i & 1 ? 0x10004003 : 0;
        nabu_session_ipc_receive_result(3, result);
        assert(writes == before + 1);
        nabu_session_ipc_end(&c, result);
        assert(writes == before + 1);
    }
    assert(strstr(journal, "stage=receive wait_ns=465000000000"));
    c = nabu_session_ipc_begin(3, 30176, 0x123, 2, 3);
    nabu_session_ipc_wait(&c); nabu_session_ipc_receive_result(3, 0);
    before = writes;
    nabu_session_ipc_end(&c, 0x10004008);
    assert(writes == before + 1 && strstr(journal, "stage=final-correction receive_result=0"));
    assert(strstr(journal, "result=10004008 result_known=1"));
    /* Worst-width identifiers fit completion capacity, preserving final errno. */
    struct nabu_session_login_record *width = nabu_session_login_find(tid, 0);
    self_pid = 2147483647; tid = UINT64_MAX; now_ns = UINT64_MAX;
    c.tid = tid; c.call = UINT64_MAX; width->call = c;
    width->wait_ns = UINT64_MAX; width->result_logged = 1; width->receive_result = UINT32_MAX;
    size_t width_start = used;
    nabu_session_ipc_end(&c, 0xfffffffe);
    assert(used - width_start < 256 && !strstr(journal + width_start, "truncated=1"));
    self_pid = 168; tid = 620; now_ns = 465000000000ULL;
    /* An entry with no observed callback is explicitly closed as unknown at
     * the next trap, never silently overwritten or given a fake result. */
    c = nabu_session_ipc_begin(3, 99, 0x123, 2, 3); nabu_session_ipc_wait(&c);
    struct nabu_session_call next = nabu_session_ipc_begin(1, 100, 0x123, 0, 0);
    assert(strstr(journal, "result=0 result_known=0 stage=next-trap"));
    nabu_session_ipc_end(&next, 0x10000003);
}
static void loginwindow_continuation_final_error(void) {
    reset(); self_name = "loginwindow"; self_pid = 108; tid = 1243;
    /* This stack-local c is deliberately not passed to continuation_end. */
    struct nabu_session_call c = nabu_session_ipc_begin(3, 30176, 0x123, 2, 3);
    unsigned before = writes;
    nabu_session_ipc_continuation_end(0x10004008);
    assert(writes == before); /* entry alone is not a receive continuation */
    nabu_session_ipc_wait(&c);
    nabu_session_ipc_continuation_end(0x10004008);
    assert(writes == before); /* no native receive state yet */
    nabu_session_ipc_receive_result(3, 0);
    struct nabu_session_login_record *slot = nabu_session_login_find(tid, 0);
    assert(slot && slot->result_logged && slot->call.selected);
    before = writes;
    /* copyout can invoke internal kernel IPC receive results. They must not
     * replace the recorded outer result0 or consume its correction budget. */
    nabu_session_ipc_receive_result(2, 0x10004003);
    assert(writes == before && slot->receive_result == 0);
    nabu_session_ipc_continuation_end(0x10004008); /* MACH_RCV_INVALID_DATA */
    assert(writes == before + 1 && !slot->call.selected && !slot->waiting);
    assert(strstr(journal, "result=10004008 result_known=1 stage=final-correction receive_result=0"));
    before = writes;
    nabu_session_ipc_continuation_end(0x10004008); assert(writes == before);
    nabu_session_ipc_end(&c, 0x10004008); assert(writes == before);
    /* Equal final result is deduplicated but still retires the admitted call. */
    c = nabu_session_ipc_begin(3, 10019, 0x123, 2, 3); nabu_session_ipc_wait(&c);
    nabu_session_ipc_receive_result(3, 0x10004004); before = writes;
    nabu_session_ipc_continuation_end(0x10004004);
    assert(writes == before && !slot->call.selected && !slot->waiting);
    /* No current record, a different tid, and WS/OD cannot create fake calls. */
    tid = 9999; nabu_session_ipc_continuation_end(0x10004008); assert(writes == before);
    self_name = "WindowServer"; self_pid = 166; tid = 646;
    c = nabu_session_ipc_begin(3, 12, 0x123, 2, 3); nabu_session_ipc_wait(&c);
    nabu_session_ipc_receive_result(3, 0); before = writes;
    nabu_session_ipc_continuation_end(0x10004008); assert(writes == before);
}
static void loginwindow_epoch_bounds(void) {
    reset(); self_name = "loginwindow"; self_pid = 168; tid = 620;
    now_ns = 397540000000ULL;
    unsigned admitted = NABU_SESSION_LOGIN_TID_BYTES / NABU_SESSION_LOGIN_CALL_BYTES;
    for (unsigned i = 0; i < admitted; ++i) {
        struct nabu_session_call c = nabu_session_ipc_begin(1, i, 0x123, 0, 0);
        assert(c.selected); nabu_session_ipc_end(&c, 0);
    }
    size_t old_used = used;
    for (unsigned i = 0; i < 50; ++i) {
        struct nabu_session_call c = nabu_session_ipc_begin(3, i, 0x123, 2, 3);
        assert(!c.selected); nabu_session_ipc_wait(&c);
        nabu_session_ipc_receive_result(3, 0); nabu_session_ipc_end(&c, 0);
    }
    assert(occurrences(journal, "reason=epoch-byte-bound") == 1);
    assert(used - old_used < 192 && used <= NABU_SESSION_LOGIN_TID_BYTES + 384);
    /* Explicit development phase400 refresh admits403s30176 even if397s
     * exhausted. The completion is reserved, independent of next epoch. */
    now_ns = 403087435052ULL;
    struct nabu_session_call c = nabu_session_ipc_begin(3, 30176, 0x123, 2, 3);
    assert(c.selected); nabu_session_ipc_wait(&c);
    assert(strstr(journal, "epoch=7 limit=131072 previous_dropped=50"));
    now_ns = 464000000000ULL;
    nabu_session_ipc_receive_result(3, 0x10004004);
    assert(strstr(journal, "result=10004004 result_known=1 stage=receive"));
    /* Capacity exhaustion is explicit, no hashing collision steals another
     * loginwindow thread's association. Slot RAM remains fixed forever. */
    for (unsigned i = 1; i < NABU_SESSION_LOGIN_SLOTS; ++i) {
        tid = 1000 + i;
        c = nabu_session_ipc_begin(1, i, 0x123, 0, 0);
        assert(c.selected); nabu_session_ipc_end(&c, 0);
    }
    tid = 9999; c = nabu_session_ipc_begin(3, 9, 0x123, 2, 3);
    assert(!c.selected && strstr(journal, "reason=tid-slots-full slots=16"));
    unsigned before = writes;
    c = nabu_session_ipc_begin(3, 9, 0x123, 2, 3); assert(!c.selected && writes == before);
}
int main(void) {
    snapshot_paths(); selection_and_continuation(); collision_and_isolation(); error_and_truncation();
    loginwindow_call_admission(); loginwindow_epoch_bounds(); loginwindow_continuation_final_error();
    puts("session IPC lock, failure, continuation and per-process budget checks passed");
    return 0;
}
