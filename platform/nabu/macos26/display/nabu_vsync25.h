// SPDX-License-Identifier: GPL-3.0-or-later
#pragma once
#include <stddef.h>
#include <stdint.h>
#include <string.h>

// ONLY macOS 25F71 IOMobileGraphicsFamily, UUID
// EA3273B7-AF0F-3E06-B0F5-072DFCDD5395. This is a software refresh clock,
// not evidence that a hardware vblank or a framebuffer presentation occurred.
// Call once from the existing IOTimerEventSource callback, with its family's
// work-loop gate held. Never call from swap_begin_gated: submissions and type-5
// registrations also call that method and are not refresh events.
// The same gate protects registration/removal and the native sender's list walk.
namespace nabu_vsync25 {
constexpr size_t kCount = 0x100;
constexpr size_t kLastMach = 0x108;
constexpr size_t kInterval = 0x110;
constexpr size_t kType3Head = 0x17e8; // completion reports also consume +0x108
constexpr size_t kType5Head = 0x1808; // +0x17b8 + 5 * 16

template<class T> inline T load(const void *display, size_t offset)
{
    T value;
    memcpy(&value, static_cast<const uint8_t *>(display) + offset, sizeof(value));
    return value;
}
inline bool wantedGated(const void *display)
{
    return display && load<uintptr_t>(display, kType5Head) != 0;
}
inline bool clockWantedGated(const void *display)
{
    return display && (load<uintptr_t>(display, kType3Head) != 0 || wantedGated(display));
}
} // namespace nabu_vsync25

// Exact exported nonvirtual originals; neither has a virtual slot.
// note_vbl_ISRj, 25F71 unslid 0xfffffe000a89bc5c, has no command-gate entry,
// swap-begin call, notification, or IRQ acknowledgement. It reads native clock
// (virtual slot 349, actually mach_continuous_time despite its method name),
// and on the first tick converts refreshPeriodNanoseconds
// using ns_to_absolute_time (slot 398). It owns ALL native timing stores:
// +0xf8 uint32 interrupt count; +0x100 uint64 count; +0x108/+0x110 time/interval;
// +0x120/+0x128 secondary delta/time; +0x19f8 first-interval flag.
extern "C" void nabuVsync25NativeNote(void *, unsigned)
    __asm__("__ZN25IOMobileFramebufferLegacy12note_vbl_ISREj");

// swap_notify_gatedEyyy, unslid 0xfffffe000a894cc4, tail-calls the unchanged
// swap_notify_dispatch_gatedEyyy at 0xfffffe000a89bd34. Keep list traversal,
// async reference lifetime, six-slot encoding and CanDrop inside this sender.
extern "C" void nabuVsync25NativeNotify(void *, uint64_t, uint64_t, uint64_t)
    __asm__("__ZN25IOMobileFramebufferLegacy17swap_notify_gatedEyyy");

// Call only on actual timer firing while hardware/display is active. Keep the
// existing timer/rearm/lifecycle; this helper creates no timer, hardware IRQ,
// power transition, swap completion or synthetic presentation.
// For the current setTimeoutMS(16) timer pass 16000000u, NOT 16666666u (60 Hz).
// Subsequent native intervals are measured between actual timer firings.
// reset_vbl_timings_gated's original +0x108/+0x110 clearing is honored by note;
// no extra local timing state survives reset. getDefaultVBLDelta returns zero
// on this family and is deliberately unused.
static inline bool nabuVsync25TickGated(void *display, unsigned refreshPeriodNanoseconds)
{
    using namespace nabu_vsync25;
    if (!clockWantedGated(display) || !refreshPeriodNanoseconds) return false;
    nabuVsync25NativeNote(display, refreshPeriodNanoseconds);
    // Completion-only clients consume the same native clock in SwapInfo.word0.
    // They require timing updates, but must not receive type-5 notifications.
    // The return value counts notification attempts, not clock updates.
    if (!wantedGated(display)) return false;
    // Exact clear_up_swap_queue_gated loads at unslid 0xfffffe000a8947a4:
    // x1=Count, x2=last native continuous-clock reading, x3=interval. SHENG 27.3 and iOS16 timer paths
    // establish the architecture, but their payload semantics MUST NOT be used.
    nabuVsync25NativeNotify(display, load<uint64_t>(display, kCount),
                            load<uint64_t>(display, kLastMach),
                            load<uint64_t>(display, kInterval));
    return true;
}
