/* SPDX-License-Identifier: GPL-3.0-or-later */
#ifndef NABU_SESSION_IPC_H
#define NABU_SESSION_IPC_H

/* Included by mach_msg.c after its IPC headers. This observes userspace names,
 * not bootstrap service names. No pointers escape the locked snapshot. */
struct nabu_session_peer {
    const char *state;
    int pid;
    uint32_t receiver_port;
};

static inline struct nabu_session_peer
nabu_session_peer_snapshot(uint32_t remote)
{
    struct nabu_session_peer peer = { "invalid-name", -1, 0 };
    ipc_space_t space = current_space(); /* current task keeps its space alive */
    if (!MACH_PORT_VALID(remote)) return peer;
    if (!lck_ticket_lock_try(&space->is_lock, &ipc_lck_grp)) {
        peer.state = "space-busy";
        return peer;
    }
    if (!is_active(space)) {
        peer.state = "space-inactive";
        is_read_unlock(space);
        return peer;
    }
    ipc_entry_t entry = ipc_entry_lookup(space, remote);
    if (entry == IE_NULL || !(entry->ie_bits &
        (MACH_PORT_TYPE_SEND | MACH_PORT_TYPE_SEND_ONCE | MACH_PORT_TYPE_RECEIVE))) {
        is_read_unlock(space);
        return peer;
    }
    /* The space lock pins the entry's port. The try-lock avoids waiting while
     * holding that lock, including inversion against SMR port-first readers. */
    ipc_port_t port = entry->ie_port;
    if (!ip_mq_lock_try(port)) {
        peer.state = "port-busy";
        is_read_unlock(space);
        return peer;
    }
    peer.state = "unknown";
    if (ip_active(port)) {
        /* This native helper owns the receiver/space/task lifetime contract.
         * task_pid() is an atomic token read; no proc lookup or borrowed task. */
        peer.pid = ipc_port_get_receiver_task_locked(port, NULL);
        peer.receiver_port = ip_get_receiver_name(port);
        if (peer.pid >= 0) peer.state = "pid-snapshot";
    } else {
        peer.state = "port-inactive";
    }
    ip_mq_unlock(port);
    is_read_unlock(space);
    return peer;
}

struct nabu_session_call {
    uint64_t tid, call, options;
    uint32_t msgid, remote, local, receive;
    unsigned channel;
    int selected;
    unsigned login_slot; /* one-based, zero means not admitted/reserved */
    char name[32];
    struct nabu_session_peer peer;
};

/* Bounded, nonblocking correlation storage; no thread/port/task ref retained.
 * A collision or busy slot is explicitly unavailable, never another call's ID.
 * A userspace trap cannot nest on one thread. A newer entry replaces any stale
 * same-tid record left by a missed nonblocking cleanup. */
#define NABU_SESSION_CALL_SLOTS 192U
static struct {
    unsigned busy;
    uint64_t owner; /* independently invalidated by its thread at next entry */
    struct nabu_session_call record;
} nabu_session_calls[NABU_SESSION_CALL_SLOTS];
static uint64_t nabu_session_call_sequence;

static inline unsigned
nabu_session_call_index(const struct nabu_session_call *record)
{
    return ((record->channel - 3U) / 2U) * 64U +
        (unsigned)(record->tid ^ (record->tid >> 32)) % 64U;
}

static inline void
nabu_session_call_invalidate(const struct nabu_session_call *record)
{
    unsigned index = nabu_session_call_index(record);
    uint64_t owner = record->tid;
    /* Never clear another tid's record, even while its slot is busy. The same
     * userspace thread cannot execute its old receive callback concurrently. */
    __atomic_compare_exchange_n(&nabu_session_calls[index].owner, &owner, 0,
        0, __ATOMIC_RELAXED, __ATOMIC_RELAXED);
}

static inline int
nabu_session_call_slot(uint64_t tid, struct nabu_session_call *record, int store)
{
    unsigned index = nabu_session_call_index(record);
    unsigned expected = 0;
    if (!__atomic_compare_exchange_n(&nabu_session_calls[index].busy, &expected, 1,
        0, __ATOMIC_ACQUIRE, __ATOMIC_RELAXED)) return 0;
    struct nabu_session_call *saved = &nabu_session_calls[index].record;
    uint64_t owner = __atomic_load_n(&nabu_session_calls[index].owner, __ATOMIC_RELAXED);
    int found = owner == tid;
    if (store) {
        if (!owner || found) {
            *saved = *record;
            __atomic_store_n(&nabu_session_calls[index].owner, tid, __ATOMIC_RELAXED);
        } else found = 0;
    } else if (found) {
        *record = *saved;
        __atomic_store_n(&nabu_session_calls[index].owner, 0, __ATOMIC_RELAXED);
    }
    __atomic_store_n(&nabu_session_calls[index].busy, 0, __ATOMIC_RELEASE);
    return found;
}

static inline int
nabu_session_ipc_select(struct nabu_session_call *call)
{
    if (!nabu_session_enabled()) return 0;
    proc_name(proc_selfpid(), call->name, sizeof(call->name));
    if (!strcmp(call->name, "WindowServer")) call->channel = 3;
    else if (!strcmp(call->name, "loginwindow")) call->channel = 5;
    else if (!strcmp(call->name, "opendirectoryd")) call->channel = 7;
    else return 0;
    call->selected = 1;
    call->tid = thread_tid(current_thread());
    return 1;
}

/* loginwindow: per-call reservation in 60-second per-tid epochs. This development
 * observer starts an explicit late-capture phase at 400 seconds; no product
 * behavior depends on that diagnostic boundary. Epoch reset governs only NEW
 * admissions; a
 * pending call keeps its separate reserved completion across every boundary.
 * Slots are permanent tid-owned: no other thread can consume credit or collide.
 * The journal itself may still lose bytes on contention or wrap (see core).
 */
#define NABU_SESSION_LOGIN_SLOTS 16U
#define NABU_SESSION_LOGIN_TID_BYTES (128U * 1024U)
#define NABU_SESSION_LOGIN_CALL_BYTES (512U + 256U + 256U)
struct nabu_session_login_record {
    uint64_t owner, epoch, wait_ns;
    unsigned epoch_valid, reserved_bytes, dropped_calls, marker_reported;
    unsigned waiting, result_logged;
    uint32_t receive_result;
    struct nabu_session_call call;
};
static struct nabu_session_login_record nabu_session_login[NABU_SESSION_LOGIN_SLOTS];
static unsigned nabu_session_login_slots_drop_reported;

static inline uint64_t
nabu_session_now_ns(void)
{
    uint64_t ns = 0;
    absolutetime_to_nanoseconds(mach_absolute_time(), &ns);
    return ns;
}

static inline struct nabu_session_login_record *
nabu_session_login_find(uint64_t tid, int claim)
{
    for (unsigned n = 0; n < NABU_SESSION_LOGIN_SLOTS; ++n) {
        uint64_t owner = __atomic_load_n(&nabu_session_login[n].owner, __ATOMIC_ACQUIRE);
        if (owner == tid) return &nabu_session_login[n];
    }
    if (!claim) return NULL;
    for (unsigned n = 0; n < NABU_SESSION_LOGIN_SLOTS; ++n) {
        uint64_t owner = 0;
        if (__atomic_compare_exchange_n(&nabu_session_login[n].owner, &owner, tid,
            0, __ATOMIC_ACQ_REL, __ATOMIC_RELAXED)) return &nabu_session_login[n];
    }
    return NULL;
}

static inline void
nabu_session_login_complete(struct nabu_session_login_record *slot,
    const char *stage, uint32_t result, int known)
{
    if (!slot->call.selected || slot->result_logged) return;
    slot->result_logged = 1;
    slot->receive_result = result;
    nabu_session_reserved_emit(256,
        "event=ipc-receive-result name=loginwindow call=%llu result=%x result_known=%u "
        "stage=%s wait_ns=%llu\n", (unsigned long long)slot->call.call,
        result, (unsigned)known, stage, (unsigned long long)slot->wait_ns);
}

static inline int
nabu_session_login_admit(struct nabu_session_call *call)
{
    struct nabu_session_login_record *slot = nabu_session_login_find(call->tid, 1);
    if (!slot) {
        unsigned expected = 0;
        if (__atomic_compare_exchange_n(&nabu_session_login_slots_drop_reported, &expected, 1,
            0, __ATOMIC_RELAXED, __ATOMIC_RELAXED))
            nabu_session_reserved_emit(192, "event=ipc-drop name=loginwindow "
                "reason=tid-slots-full slots=%u call=%llu\n", NABU_SESSION_LOGIN_SLOTS,
                (unsigned long long)call->call);
        return 0;
    }
    /* A newer trap proves the old trap no longer waits, but cannot reconstruct
     * its unobserved result. Close it explicitly with the reserved bytes. */
    nabu_session_login_complete(slot, "next-trap", 0, 0);
    uint64_t ns = nabu_session_now_ns();
    uint64_t phase = 400000000000ULL;
    uint64_t epoch = ns < phase ? ns / 60000000000ULL :
        7ULL + (ns - phase) / 60000000000ULL;
    if (!slot->epoch_valid || slot->epoch != epoch) {
        nabu_session_reserved_emit(192, "event=ipc-epoch name=loginwindow epoch=%llu "
            "limit=%u previous_dropped=%u pending=0\n", (unsigned long long)epoch,
            NABU_SESSION_LOGIN_TID_BYTES, slot->dropped_calls);
        slot->epoch = epoch;
        slot->epoch_valid = 1;
        slot->reserved_bytes = slot->dropped_calls = slot->marker_reported = 0;
    }
    if (slot->reserved_bytes > NABU_SESSION_LOGIN_TID_BYTES - NABU_SESSION_LOGIN_CALL_BYTES) {
        slot->dropped_calls++;
        slot->call.selected = 0;
        if (!slot->marker_reported) {
            slot->marker_reported = 1;
            nabu_session_reserved_emit(192, "event=ipc-drop name=loginwindow reason=epoch-byte-bound "
                "epoch=%llu limit=%u dropped=%u pending=0 call=%llu\n",
                (unsigned long long)epoch, NABU_SESSION_LOGIN_TID_BYTES, slot->dropped_calls,
                (unsigned long long)call->call);
        }
        return 0;
    }
    slot->reserved_bytes += NABU_SESSION_LOGIN_CALL_BYTES;
    call->login_slot = (unsigned)(slot - nabu_session_login) + 1;
    slot->waiting = slot->result_logged = 0;
    slot->wait_ns = 0;
    slot->call = *call;
    return 1;
}

static inline void
nabu_session_ipc_report(const char *event, const struct nabu_session_call *call,
    uint32_t result, int completion)
{
    /* Name is intentionally resolved after all IPC locks are released. This
     * PID-based lookup is best effort, not an identity/liveness guarantee. */
    char peer_name[32] = "unknown";
    if (call->peer.pid >= 0) proc_name(call->peer.pid, peer_name, sizeof(peer_name));
    const char *status = result == 0x10004003U ? "timed-out" :
        result == 0x10004004U ? "too-large" : result ? "error" : "success";
    unsigned channel = result ? 9U + (call->channel - 3U) / 2U :
        call->channel + (unsigned)completion;
    if (call->login_slot) {
        nabu_session_reserved_emit(512,
            "event=ipc-enter name=loginwindow call=%llu msgid=%u msgid_known=%u "
            "remote=%x local=%x receive=%x peer=%s peer_pid=%d peer_name_at_lookup=%s peer_receive=%x\n",
            (unsigned long long)call->call, call->msgid,
            (unsigned)((call->options & MACH64_SEND_MSG) != 0), call->remote,
            call->local, call->receive, call->peer.state, call->peer.pid,
            peer_name[0] ? peer_name : "unknown", call->peer.receiver_port);
        return;
    }
    nabu_session_emit(channel,
        "event=%s name=%s call=%llu assoc=%s options=%llx msgid=%u msgid_known=%u "
        "remote=%x local=%x receive=%x peer=%s peer_pid=%d peer_name_at_lookup=%s "
        "peer_receive=%x result=%x status=%s\n",
        event, call->name, (unsigned long long)call->call,
        call->call ? "entry" : "unavailable", (unsigned long long)call->options,
        call->msgid, (unsigned)(call->call && (call->options & MACH64_SEND_MSG)),
        call->remote, call->local, call->receive, call->peer.state,
        call->peer.pid, peer_name[0] ? peer_name : "unknown", call->peer.receiver_port,
        result, status);
}

static inline struct nabu_session_call
nabu_session_ipc_begin(uint64_t options, uint32_t msgid, uint32_t remote,
    uint32_t local, uint32_t receive)
{
    struct nabu_session_call call = {0};
    if (!nabu_session_ipc_select(&call)) return call;
    if (call.channel != 5) nabu_session_call_invalidate(&call);
    call.call = __atomic_add_fetch(&nabu_session_call_sequence, 1, __ATOMIC_RELAXED);
    call.options = options;
    call.receive = (options & MACH64_RCV_MSG) ? receive : 0;
    call.peer = (struct nabu_session_peer){ "no-send", -1, 0 };
    if (options & MACH64_SEND_MSG) {
        call.msgid = msgid;
        call.remote = remote;
        call.local = local;
        call.peer = nabu_session_peer_snapshot(remote);
    }
    if (call.channel == 5 && !nabu_session_login_admit(&call)) {
        call.selected = 0;
        return call;
    }
    nabu_session_ipc_report("ipc-enter", &call, 0, 0);
    return call;
}

static inline void
nabu_session_ipc_wait(struct nabu_session_call *call)
{
    if (!call->selected) return;
    /* Install only after send/copyin work, immediately before trap receive.
     * Internal receive_results during send must not steal this association. */
    if (call->login_slot) {
        struct nabu_session_login_record *slot = &nabu_session_login[call->login_slot - 1];
        if (slot->call.call != call->call || slot->waiting) return;
        slot->waiting = 1;
        slot->wait_ns = nabu_session_now_ns();
        return;
    } else {
        nabu_session_call_slot(call->tid, call, 1);
    }
    nabu_session_ipc_report("ipc-wait", call, 0, 0);
}

static inline void
nabu_session_ipc_receive_result(uint64_t options, uint32_t result)
{
    struct nabu_session_call call = {0};
    if (!nabu_session_ipc_select(&call)) return;
    if (call.channel == 5) {
        struct nabu_session_login_record *slot = nabu_session_login_find(call.tid, 0);
        /* No call0 event from send-internal IPC or an unadmitted call: neither
         * has reserved credit. Pending call remains until the real receive. */
        if (!slot || !slot->call.selected || !slot->waiting || slot->result_logged) return;
        nabu_session_login_complete(slot, "receive", result, 1);
        return;
    } else if (!nabu_session_call_slot(call.tid, &call, 0)) {
        call.options = options;
        call.peer = (struct nabu_session_peer){ "unavailable", -1, 0 };
    }
    nabu_session_ipc_report("ipc-receive-result", &call, result, 1);
}

/* Continuations return directly to userspace without mach_msg2_trap's stack
 * local end hook. Run only after the outer receive_results/copyout has fully
 * completed. No new call or name lookup: consume this thread's admitted record
 * only when the native receive-state callback already marked it completed. */
static inline void
nabu_session_ipc_continuation_end(uint32_t result)
{
    struct nabu_session_login_record *slot =
        nabu_session_login_find(thread_tid(current_thread()), 0);
    if (!slot || !slot->call.selected || !slot->waiting || !slot->result_logged) return;
    if (slot->receive_result != result) {
        nabu_session_reserved_emit(256,
            "event=ipc-return name=loginwindow call=%llu result=%x result_known=1 "
            "stage=final-correction receive_result=%x wait_ns=%llu\n",
            (unsigned long long)slot->call.call, result, slot->receive_result,
            (unsigned long long)slot->wait_ns);
    }
    slot->call.selected = 0;
    slot->waiting = 0;
}

static inline void
nabu_session_ipc_end(const struct nabu_session_call *call, uint32_t result)
{
    if (!call->selected) return;
    if (call->login_slot) {
        struct nabu_session_login_record *slot = &nabu_session_login[call->login_slot - 1];
        if (slot->call.call != call->call || !slot->call.selected) return;
        if (slot->result_logged && slot->receive_result != result) {
            /* The receive callback precedes copyout. Reserve a third record
             * at admission so its final trap error cannot be rate-dropped. */
            nabu_session_reserved_emit(256,
                "event=ipc-return name=loginwindow call=%llu result=%x result_known=1 "
                "stage=final-correction receive_result=%x wait_ns=%llu\n",
                (unsigned long long)call->call, result, slot->receive_result,
                (unsigned long long)slot->wait_ns);
        } else {
            nabu_session_login_complete(slot, "return", result, 1);
        }
        slot->call.selected = 0;
        return;
    } else {
        struct nabu_session_call unused = *call;
        nabu_session_call_slot(call->tid, &unused, 0);
        nabu_session_call_invalidate(call);
    }
    nabu_session_ipc_report("ipc-return", call, result, 1);
}
#endif
