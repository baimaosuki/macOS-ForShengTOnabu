// SPDX-License-Identifier: GPL-3.0-or-later
#pragma once
#ifndef NABU_SOURCE_MAPS_HOST_TEST
#include <IOKit/IOLocks.h>
#include <kern/thread.h>
#endif

// One retained mapping per plane. IOMemoryMap holds its descriptor strongly;
// no borrowed IOSurface pointer survives the native request's retirement.
// Old mappings are unmapped away from the framebuffer command gate. The
// detached worker owns only this state, never the display object.
struct NabuGopSourceReaper {
    static constexpr unsigned capacity = 8;
    IOLock *lock = nullptr;
    IOMemoryMap *queue[capacity]{};
    unsigned head = 0, count = 0, released = 0;
    bool stopping = false;

    static void work(void *argument, wait_result_t) {
        auto *self = static_cast<NabuGopSourceReaper *>(argument);
        IOLockLock(self->lock);
        for (;;) {
            while (!self->count && !self->stopping)
                IOLockSleep(self->lock, self, THREAD_UNINT);
            if (!self->count && self->stopping) break;
            IOMemoryMap *mapping = self->queue[self->head];
            self->queue[self->head] = nullptr;
            self->head = (self->head + 1) % capacity;
            --self->count;
            IOLockUnlock(self->lock);
            if (self->released < 4) nabuDisplayLog("NABU-DISPLAY: source-unmap-begin mapping=%p\n", mapping);
            mapping->release();
            if (self->released++ < 4) nabuDisplayLog("NABU-DISPLAY: source-unmap-done count=%u\n", self->released);
            IOLockLock(self->lock);
        }
        IOLockUnlock(self->lock);
        IOLockFree(self->lock);
        delete self;
        thread_terminate(current_thread());
    }

    static NabuGopSourceReaper *create() {
        auto *self = new NabuGopSourceReaper;
        if (!self) return nullptr;
        self->lock = IOLockAlloc();
        if (!self->lock) { delete self; return nullptr; }
        thread_t thread = THREAD_NULL;
        if (kernel_thread_start(work, self, &thread) != KERN_SUCCESS) {
            IOLockFree(self->lock); delete self; return nullptr;
        }
        thread_deallocate(thread);
        return self;
    }

    // Called before replacing a plane mapping. Never wait for an old unmap
    // while holding the framebuffer gate. The caller keeps the old mapping
    // when this bounded queue is full and retires the new swap as an error.
    bool retire(IOMemoryMap *mapping) {
        if (!mapping) return true;
        IOLockLock(lock);
        bool accepted = !stopping && count < capacity - 4;
        if (accepted) {
            queue[(head + count) % capacity] = mapping;
            ++count;
            IOLockWakeup(lock, this, true);
        }
        IOLockUnlock(lock);
        return accepted;
    }

    // Four entries are reserved for the display's current plane mappings.
    // stop first closes the native workloop, then transfers those references.
    void finish(IOMemoryMap **current) {
        IOLockLock(lock);
        for (unsigned i = 0; i < 4; i++) if (current[i]) {
            queue[(head + count) % capacity] = current[i];
            ++count;
            current[i] = nullptr;
        }
        stopping = true;
        IOLockWakeup(lock, this, true);
        IOLockUnlock(lock);
    }
};

struct NabuGopSourceMap {
    IOMemoryMap *mapping = nullptr;
    IOMemoryDescriptor *memory = nullptr; // valid only while mapping retains it
    uint64_t base = 0, span = 0;
};
