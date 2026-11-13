// SPDX-License-Identifier: GPL-3.0-or-later
// Native 25F71 offsets, verified against EA3273B7-AF0F-3E06-B0F5-072DFCDD5395.
// getCurrent{Src,Dst}Rect_gated and isCurrentLayerValid_gated choose the
// current request for updated planes, otherwise the family's DisplayedData.
// The family owns every surface throughout this synchronous gated copy and
// transfers/releases ownership in swap_complete_gated AFTER our final read.
void NativeGOPDisplay::program_hardware_gated()
{
    IOMFBSwapIORequest *swap = at<IOMFBSwapIORequest *>(this, kCurrentSwap);
    const uint32_t mask = at<uint32_t>(swap, kSwapLayers);
    const uint32_t swapID = at<uint32_t>(swap, 0x334);
    const uint64_t bytes = uint64_t(fVideo.v_width) * fVideo.v_height * 4;
    if (!fComposition) fComposition = static_cast<uint32_t *>(IOMalloc(bytes));
    bool success = fComposition && fScanout;
    unsigned active = 0;
    if (success) {
        // A complete scene is rebuilt so clears and moved cursors restore the
        // underlying pixels instead of leaving old plane contents in scanout.
        bzero(fComposition, bytes);
        // AppleParavirtDisplay identifies plane 1 as cursor. Scene planes
        // remain opaque and the cursor is applied last, including cursor-only swaps.
        const unsigned order[] = {0, 2, 3, 1};
        for (unsigned i : order) {
            const bool updated = (mask & (1u << i)) != 0;
            IOSurface *surface = updated
                ? at<IOSurface *>(swap, 0xac0 + i * 8)
                : at<IOSurface *>(this, 0x1510 + i * 8);
            if (!surface) continue; // mask set + null is a real plane clear
            NabuGopRect src{}, dst{};
            const uint8_t *srcAddress = static_cast<const uint8_t *>(updated ? static_cast<void *>(swap) : static_cast<void *>(this));
            memcpy(&src, srcAddress + (updated ? 0x11 : 0x11e9) + i * 16, sizeof(src));
            memcpy(&dst, srcAddress + (updated ? 0x51 : 0x1229) + i * 16, sizeof(dst));
            const uint32_t rotation = updated ? at<uint32_t>(swap, 0x268 + i * 4) : at<uint32_t>(this, 0x13dc + i * 4);
            // The family's validity check ignores disabled (zero-area) planes.
            if (!src.width || !src.height || !dst.width || !dst.height) continue;
            if (fFrames < 4 && fCopyFailures < 4)
                nabuDisplayLog("NABU-DISPLAY: swap=%u mask=%#x layer=%u updated=%u surface=%p src=%d,%d,%d,%d dst=%d,%d,%d,%d rotation=%u\n",
                      swapID, mask, i, updated, surface, src.x, src.y, src.width, src.height,
                      dst.x, dst.y, dst.width, dst.height, rotation);
            if (rotation || !copyLayer(surface, src, dst, i == 1 &&
                ioSurfaceWidth(surface) <= 256 && ioSurfaceHeight(surface) <= 256, i)) { success = false; break; }
            active |= 1u << i;
        }
    }
    if (fFrames < 4 && fCopyFailures < 4)
        nabuDisplayLog("NABU-DISPLAY: planes-done swap=%u active=%#x success=%u\n", swapID, active, success);
    // Do not replace the diagnostics with an empty first scene. A later
    // explicit plane clear is a valid black presentation.
    if (!active && !fFrames) success = false;
    if (success) {
        const bool first = fFrames == 0;
        bool claimed = !first || sheng_fb_claim_gui();
        if (fFrames < 4 && fCopyFailures < 4)
            nabuDisplayLog("NABU-DISPLAY: claim swap=%u claimed=%u first=%u\n", swapID, claimed, first);
        if (claimed) {
            // Native vcputc does not consult the shared diagnostic owner.
            // All source pixels are staged; drain/disable that console before
            // the first GOP store so it cannot overwrite the new GUI image.
            if (first) {
                nabuDisplayLog("NABU-DISPLAY: console-disable-begin swap=%u\n", swapID);
                PE_initialize_console(nullptr, kPEDisableScreen);
                nabuDisplayLog("NABU-DISPLAY: console-disable-done swap=%u\n", swapID);
            }
            if (fFrames < 4 && fCopyFailures < 4) nabuDisplayLog("NABU-DISPLAY: scanout-copy-begin swap=%u\n", swapID);
            uint64_t lit = 0, center = 0;
            for (uint64_t y = 0; y < fVideo.v_height; y++) {
                uint32_t *row = fComposition + y * fVideo.v_width;
                uint64_t saved;
                if (!sheng_fb_begin(SHENG_FB_GUI, &saved)) { success = false; break; }
                memcpy(fScanout + y * fVideo.v_rowBytes, row, fVideo.v_width * 4);
                sheng_fb_end(saved);
                for (uint64_t x = 0; x < fVideo.v_width; x++) lit += (row[x] & 0xffffff) != 0;
                if (y == fVideo.v_height / 2) center = row[fVideo.v_width / 2];
            }
#if defined(__aarch64__)
            __asm__ volatile("dsb sy" ::: "memory");
#endif
            if (fFrames < 4 && fCopyFailures < 4) nabuDisplayLog("NABU-DISPLAY: scanout-copy-done swap=%u rows=%lu success=%u\n", swapID, fVideo.v_height, success);
            success = success && __atomic_load_n(&sheng_framebuffer.owner, __ATOMIC_ACQUIRE) == SHENG_FB_GUI;
            if (success) {
                fFrames++;
                fLastLit = lit; fLastCenter = center;
                if (first) {
                    sheng_panel_ready = 0; sheng_status_pixels = 0;
                    sheng_boot_checkpoint(9);
                }
                if (fFrames <= 8 || fFrames % 50 == 0)
                    nabuDisplayLog("NABU-DISPLAY: frame=%llu swap=%u update=%#x active=%#x pixels=%llu lit=%llu centre=%#llx copied=1\n",
                          (unsigned long long)fFrames, swapID, mask, active,
                          (unsigned long long)(uint64_t(fVideo.v_width) * fVideo.v_height),
                          (unsigned long long)lit, (unsigned long long)center);
            } else if (first && __atomic_load_n(&sheng_framebuffer.owner, __ATOMIC_ACQUIRE) == SHENG_FB_GUI) {
                sheng_fb_abort_gui();
                if (__atomic_load_n(&sheng_framebuffer.owner, __ATOMIC_ACQUIRE) == SHENG_FB_BOOT)
                    PE_initialize_console(nullptr, kPEEnableScreen);
            }
        } else success = false;
    }
    if (!success) {
        fCopyFailures++;
        if (fCopyFailures <= 8 || fCopyFailures % 50 == 0)
            nabuDisplayLog("NABU-DISPLAY: swap=%u update=%#x copy_failed=%llu shown=%llu active=%#x\n",
                  swapID, mask, (unsigned long long)fCopyFailures, (unsigned long long)fFrames, active);
        // Error retirement releases this request and signals its real native
        // completion state; it must not be reported as a displayed swap.
        failCurrentSwap(swap);
        fDropped++;
        return;
    }
    if (fFrames <= 4) nabuDisplayLog("NABU-DISPLAY: native-complete-begin swap=%u\n", swapID);
    swap_complete_gated();
    fCompleted++;
    if (fCompleted <= 8 || fCompleted % 50 == 0)
        nabuDisplayLog("NABU-DISPLAY: swap=%u complete=%llu shown=%llu current=%p\n",
              swapID, (unsigned long long)fCompleted, (unsigned long long)fFrames,
              at<IOMFBSwapIORequest *>(this, kCurrentSwap));
}

void NativeGOPDisplay::failCurrentSwap(IOMFBSwapIORequest *swap)
{
    // swap_begin_gated already removed this request from the TAILQ;
    // dropSwap_gated expects a linked entry and unlinks it itself.
    IOMFBSwapIORequest *next = at<IOMFBSwapIORequest *>(this, kSwapQueueFirst);
    at<IOMFBSwapIORequest *>(swap, kSwapNext) = next;
    at<IOMFBSwapIORequest **>(swap, kSwapPrev) = &at<IOMFBSwapIORequest *>(this, kSwapQueueFirst);
    if (next) at<IOMFBSwapIORequest **>(next, kSwapPrev) = &at<IOMFBSwapIORequest *>(swap, kSwapNext);
    else at<IOMFBSwapIORequest **>(this, kSwapQueueLast) = &at<IOMFBSwapIORequest *>(swap, kSwapNext);
    at<IOMFBSwapIORequest *>(this, kSwapQueueFirst) = swap;
    dropSwap_gated(swap, false);
}

bool NativeGOPDisplay::copyLayer(IOSurface *surface, const NabuGopRect &src,
                                const NabuGopRect &dst, bool cursor, unsigned plane)
{
    uint64_t width = ioSurfaceWidth(surface), height = ioSurfaceHeight(surface);
    uint64_t rowBytes = ioSurfaceBytesPerRow(surface), base = ioSurfaceBaseOffset(surface);
    uint32_t format = ioSurfacePixelFormat(surface);
    IOMemoryDescriptor *memory = ioSurfaceMemory(surface);
    const uint64_t pixelBytes = format == kFormatRGhA ? 8 : 4;
    NabuGopClip clip{};
    if (!memory || !width || !height || width > 16384 || height > 16384 ||
        (format != kFormatBGRA && format != kFormatRGhA) || rowBytes < width * pixelBytes ||
        base > memory->getLength() || rowBytes > (memory->getLength() - base) / height ||
        !nabuGopClip(src, dst, width, height, fVideo.v_width, fVideo.v_height, clip)) return false;
    if (clip.x0 >= clip.x1 || clip.y0 >= clip.y1) return true;
    if (format == kFormatRGhA && !fHalfToByte) {
        fHalfToByte = static_cast<uint8_t *>(IOMalloc(65536));
        if (!fHalfToByte) return false;
        for (uint32_t half = 0; half < 65536; half++) fHalfToByte[half] = halfToByte(half);
    }
    const uint64_t sourceRowBytes = width * pixelBytes;
    if (fRowBytes < sourceRowBytes) {
        auto *replacement = static_cast<uint16_t *>(IOMalloc(sourceRowBytes));
        if (!replacement) return false;
        if (fRow) IOFree(fRow, fRowBytes);
        fRow = replacement; fRowBytes = sourceRowBytes;
    }
    // IOSurface owns this descriptor and its backing through native retirement.
    // Match GopFramebuffer's synchronous CPU path: map that backing directly.
    // Re-preparing it here can re-enter VM wiring while the submitting thread
    // holds the framebuffer gate; it is not a new DMA transfer owned by us.
    const uint64_t span = (height - 1) * rowBytes + sourceRowBytes;
    NabuGopSourceMap &slot = fSourceMaps[plane];
    if (slot.mapping && (slot.memory != memory || slot.base != base || slot.span != span)) {
        if (!fSourceReaper || !fSourceReaper->retire(slot.mapping)) return false;
        slot = {};
    }
    if (!slot.mapping) {
        if (fFrames < 4 && fCopyFailures < 4)
            nabuDisplayLog("NABU-DISPLAY: source-map-begin frame=%llu memory=%p format=%#x size=%llux%llu row=%llu base=%llu span=%llu\n",
                  (unsigned long long)(fFrames + 1), memory, format,
                  (unsigned long long)width, (unsigned long long)height,
                  (unsigned long long)rowBytes, (unsigned long long)base, (unsigned long long)span);
        if (span > (512ULL << 20)) return false;
        IOMemoryMap *mapping = memory->createMappingInTask(kernel_task, 0,
            kIOMapAnywhere | kIOMapReadOnly, base, span);
        if (!mapping) return false;
        // Even a refused mapping stays in its bounded slot until the worker
        // can release it; no synchronous VM unmap occurs under this gate.
        slot = {mapping, memory, base, span};
    }
    const uint8_t *source = slot.mapping->getLength() >= span
        ? reinterpret_cast<const uint8_t *>(slot.mapping->getVirtualAddress()) : nullptr;
    if (!source) return false;
    if (fFrames < 4 && fCopyFailures < 4)
        nabuDisplayLog("NABU-DISPLAY: source-map-ready frame=%llu bytes=%llu cached=1\n", (unsigned long long)(fFrames + 1), (unsigned long long)span);
    uint64_t previousY = UINT64_MAX;
    for (int64_t y = clip.y0; y < clip.y1; y++) {
        const uint64_t sy = src.y + uint64_t(y - dst.y) * uint64_t(src.height) / uint64_t(dst.height);
        if (sy != previousY) {
            memcpy(fRow, source + sy * rowBytes, sourceRowBytes);
            previousY = sy;
        }
        nabuGopLayerRow(fComposition + y * fVideo.v_width, fRow, src, dst, clip,
                       format == kFormatRGhA ? fHalfToByte : nullptr, cursor);
    }
    if (fFrames < 4 && fCopyFailures < 4) nabuDisplayLog("NABU-DISPLAY: source-copy-done frame=%llu rows=%llu\n", (unsigned long long)(fFrames + 1), (unsigned long long)(clip.y1 - clip.y0));
    // The slot keeps the map+IOMD alive. No map destructor/unmap runs here.
    if (fFrames < 4 && fCopyFailures < 4) nabuDisplayLog("NABU-DISPLAY: source-copy-return frame=%llu plane=%u retained=1\n", (unsigned long long)(fFrames + 1), plane);
    return true;
}
