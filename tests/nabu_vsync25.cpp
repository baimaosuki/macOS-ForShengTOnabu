#include <array>
#include <cassert>
#include <cstdint>
#include <cstring>
#include <vector>
#include "../platform/nabu/macos26/display/nabu_vsync25.h"

static unsigned notes, sends, period;
static void *expectedDisplay;
static std::array<uint64_t, 3> nativeResult, received;
static std::vector<unsigned> order;
template<class T> static void fixtureStore(void *display, size_t offset, T value)
{
    memcpy(static_cast<uint8_t *>(display) + offset, &value, sizeof(value));
}
// Native entry-point substitutes. The tested header must use the exact linked
// symbol names and forward native outputs, without manufacturing any fields.
extern "C" void nabuVsync25NativeNote(void *display, unsigned nanoseconds)
{
    using namespace nabu_vsync25;
    assert(display == expectedDisplay && wantedGated(display));
    ++notes;
    period = nanoseconds;
    order.push_back(1);
    fixtureStore(display, kCount, nativeResult[0]);
    fixtureStore(display, kLastMach, nativeResult[1]);
    fixtureStore(display, kInterval, nativeResult[2]);
}
extern "C" void nabuVsync25NativeNotify(void *display, uint64_t count,
                                       uint64_t now, uint64_t interval)
{
    using namespace nabu_vsync25;
    assert(display == expectedDisplay && wantedGated(display));
    assert(order.back() == 1);
    received = {count, now, interval};
    ++sends;
    order.push_back(2);
}

int main()
{
    using namespace nabu_vsync25;
    std::array<uint8_t, 0x1af8> state;
    state.fill(0x5a);
    void *display = state.data();
    expectedDisplay = display;
    fixtureStore<uintptr_t>(display, kType5Head, 0);
    const auto empty = state;
    assert(!nabuVsync25TickGated(display, 16000000));
    assert(!nabuVsync25TickGated(nullptr, 16000000));
    assert(state == empty && !notes && !sends);
    fixtureStore<uintptr_t>(display, kType5Head, 0x1234);
    const auto registered = state;
    assert(!nabuVsync25TickGated(display, 0));
    assert(state == registered && !notes && !sends);
    // Distinct high words ensure Count/lastMach/interval retain native order
    // and full width. Header must read AFTER native note has updated them.
    nativeResult = {0x10000000aULL, 0x200000030ULL, 0x300000040ULL};
    assert(nabuVsync25TickGated(display, 16000000));
    assert(period == 16000000 && received == nativeResult);
    auto expected = registered;
    fixtureStore(expected.data(), 0x100, nativeResult[0]);
    fixtureStore(expected.data(), 0x108, nativeResult[1]);
    fixtureStore(expected.data(), 0x110, nativeResult[2]);
    // All other family bytes stay untouched by this adapter, including the
    // native ISR's other counters/flags. Those belong to the original method.
    assert(state == expected);
    // A late firing forwards the native measured delta, not the requested
    // period; it emits once, with no invented catch-up frames or clock reads.
    nativeResult = {0x10000000bULL, 0x200000090ULL, 0x60};
    assert(nabuVsync25TickGated(display, 16000000));
    assert(received == nativeResult && notes == 2 && sends == 2);
    assert((order == std::vector<unsigned>{1, 2, 1, 2}));
    // Removal under the same gate stops both timing work and notifications.
    fixtureStore<uintptr_t>(display, kType5Head, 0);
    const auto removed = state;
    assert(!nabuVsync25TickGated(display, 16000000));
    assert(state == removed && notes == 2 && sends == 2);
    // Native reset clears lastMach/interval, preserving Count. The adapter
    // has no cached state and simply forwards the next native result.
    fixtureStore<uintptr_t>(display, kType5Head, 0x5678);
    fixtureStore<uint64_t>(display, kLastMach, 0);
    fixtureStore<uint64_t>(display, kInterval, 0);
    nativeResult = {0x10000000cULL, 0x4000000a0ULL, 307200};
    assert(nabuVsync25TickGated(display, 16000000));
    assert(received == nativeResult && notes == 3 && sends == 3);
}
