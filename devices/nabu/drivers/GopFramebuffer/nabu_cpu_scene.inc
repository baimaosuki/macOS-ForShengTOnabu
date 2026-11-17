// SPDX-License-Identifier: GPL-3.0-or-later
// Included inside NativeLegacyDisplay. Offsets come only from26C5035r's fixture.
struct NabuCpuKeptPlane {
    OSObject *surface = nullptr;
    IOMemoryDescriptor *memory = nullptr;
    IOMemoryMap *mapping = nullptr;
    NabuGopRect source{}, destination{};
};
NabuCpuKeptPlane nabuPlanes[4]{};
uint32_t *nabuScene = nullptr;
uint64_t nabuSceneBytes = 0, nabuFrames = 0;
uint32_t nabuBackground = 0xff000000U;
uint8_t *nabuHalf = nullptr;
bool nabuSeenRgb = false;
unsigned nabuProbeStages = 0;
void nabuStageProbe(unsigned stage) {
    if (nabuSeenRgb || !bootMapping || !primary || stage < 1 || stage > 3) return;
    PE_Video video{};
    if (PE_current_console(&video) || video.v_depth != 32 ||
        video.v_baseAddr != bootMapping->getPhysicalAddress()) return;
    nabuProbeStages |= 1U << (stage - 1);
    if (!nabuCpuStageBars(reinterpret_cast<volatile uint8_t *>(bootMapping->getVirtualAddress()),
        bootMapping->getLength(), video.v_rowBytes, video.v_width, video.v_height, nabuProbeStages)) return;
    __asm__ volatile("dsb sy" ::: "memory");
    IOLog("NABU27-CPU: probe_stage=%u phys=%llx stride=%lu cache=write-combine first_rgb=%u\n",
        stage, bootMapping->getPhysicalAddress(), (unsigned long)video.v_rowBytes, nabuSeenRgb);
}

template<class T> static T nabuField(const void *request, unsigned offset) {
    T value{};
    memcpy(&value, static_cast<const uint8_t *>(request) + offset, sizeof value);
    return value;
}
static void nabuDropPlane(NabuCpuKeptPlane &p) {
    OSSafeReleaseNULL(p.mapping);
    if (p.memory) p.memory->complete();
    OSSafeReleaseNULL(p.memory);
    OSSafeReleaseNULL(p.surface);
    p = {};
}
void nabuFreeScene() {
    for (auto &p : nabuPlanes) nabuDropPlane(p);
    if (nabuScene) IOFree(nabuScene, nabuSceneBytes);
    nabuScene = nullptr; nabuSceneBytes = 0;
    if (nabuHalf) IOFree(nabuHalf, 65536);
    nabuHalf = nullptr;
}
bool nabuValidateCpu(const OriginalLegacySwapRequest *request) {
    if (!request || !logicalPM || !primary || !bootMapping) return false;
    const uint32_t updated = nabuField<uint32_t>(request, kRequestMasks);
    const uint32_t disabled = nabuField<uint32_t>(request, kRequestMasks + 4);
    if (((updated | disabled) & ~0x8000000fU) || (disabled & ~updated)) return false;
    // Preserve the upstream mode/no-op admission when no plane is updated.
    if (!(updated & 15)) return false;
    uint32_t width = 0, height = 0; getDisplayDimensions(&width, &height);
    if (!width || !height || nabuField<uint32_t>(request, kRequestDims) != width ||
        nabuField<uint32_t>(request, kRequestDims + 4) != height) return false;
    for (unsigned at = kRequestOptions; at <= kRequestOptions + 8; at += 4)
        if (nabuField<uint32_t>(request, at)) return false;
    for (unsigned i = 0; i != 4; ++i) {
        if (!(updated & (1U << i)) || (disabled & (1U << i))) continue;
        OSObject *surface = nabuField<OSObject *>(request, kRequestSurfaces + i * sizeof(void *));
        if (!surface || !surface->metaCast("IOSurface")) return false;
        IOMemoryDescriptor *memory = native_legacy_surface_descriptor(surface);
        const uint32_t format = native_legacy_surface_format(surface);
        const uint64_t sw = native_legacy_surface_width(surface), sh = native_legacy_surface_height(surface);
        const uint64_t stride = native_legacy_surface_stride(surface), element = format == 0x52476841 ? 8 : 4;
        NabuGopClip clip{};
        if ((format != 0x42475241 && format != 0x52476841) || native_legacy_surface_planes(surface) ||
            !memory || !sw || !sh || sw > 16384 || sh > 16384 || stride < sw * element ||
            stride > UINT64_MAX / sh || stride * sh > memory->getLength() ||
            !nabuGopClip(nabuField<NabuGopRect>(request, kRequestRect0 + 16 * i),
                         nabuField<NabuGopRect>(request, kRequestRect1 + 16 * i), sw, sh, width, height, clip))
            return false;
    }
    return true;
}
bool nabuPresentCpu(const OriginalLegacySwapRequest *request) {
    if (!nabuValidateCpu(request)) return false;
    PE_Video video{};
    uint32_t width = 0, height = 0; getDisplayDimensions(&width, &height);
    if (PE_current_console(&video) || video.v_baseAddr != bootMapping->getPhysicalAddress() ||
        video.v_width != width || video.v_height != height || video.v_depth != 32 || video.v_rotate)
        return false;
    const uint64_t bytes = uint64_t(width) * height * 4;
    if (!nabuScene) {
        nabuScene = static_cast<uint32_t *>(IOMalloc(bytes));
        if (!nabuScene) return false;
        nabuSceneBytes = bytes;
    }
    if (nabuSceneBytes != bytes) return false;
    const uint32_t updated = nabuField<uint32_t>(request, kRequestMasks);
    const uint32_t disabled = nabuField<uint32_t>(request, kRequestMasks + 4);
    NabuCpuKeptPlane candidate[4]{};
    NabuCpuPlaneView views[4]{};
    bool changed[4]{};
    bool ready = true;
    for (unsigned i = 0; i != 4 && ready; ++i) {
        NabuCpuKeptPlane *p = &nabuPlanes[i];
        if (updated & (1U << i)) {
            changed[i] = true; p = &candidate[i];
            if (!(disabled & (1U << i))) {
                p->surface = nabuField<OSObject *>(request, kRequestSurfaces + i * sizeof(void *));
                p->surface->retain();
                p->memory = native_legacy_surface_descriptor(p->surface); p->memory->retain();
                if (p->memory->prepare() != kIOReturnSuccess) {
                    OSSafeReleaseNULL(p->memory); ready = false; break;
                }
                p->mapping = p->memory->createMappingInTask(kernel_task, 0, kIOMapAnywhere | kIOMapReadOnly | kIOMapCopybackCache);
                p->source = nabuField<NabuGopRect>(request, kRequestRect0 + 16 * i);
                p->destination = nabuField<NabuGopRect>(request, kRequestRect1 + 16 * i);
                if (!p->mapping || !p->mapping->getVirtualAddress() ||
                    p->mapping->getLength() < p->memory->getLength()) { ready = false; break; }
                IOLog("NABU27-CPU: submitted_plane=%u surface=%p fmt=%x width=%llu height=%llu stride=%llu descriptor_bytes=%llu mapped_bytes=%llu full_mapping=1\n",
                    i, p->surface, native_legacy_surface_format(p->surface),
                    native_legacy_surface_width(p->surface), native_legacy_surface_height(p->surface),
                    native_legacy_surface_stride(p->surface), p->memory->getLength(), p->mapping->getLength());
            }
        }
        if (!p->surface) continue;
        const bool half = native_legacy_surface_format(p->surface) == 0x52476841;
        if (half && !nabuHalf) {
            const uint8_t *positive = halfTable();
            nabuHalf = positive ? static_cast<uint8_t *>(IOMalloc(65536)) : nullptr;
            if (!nabuHalf) { ready = false; break; }
            memcpy(nabuHalf, positive, 32768); bzero(nabuHalf + 32768, 32768);
        }
        flush_dcache64(p->mapping->getVirtualAddress(), unsigned(p->mapping->getLength()), false);
        if (!half) {
            uint64_t rgb = 0, alpha = 0;
            const uint64_t sw = native_legacy_surface_width(p->surface), sh = native_legacy_surface_height(p->surface);
            const uint64_t pitch = native_legacy_surface_stride(p->surface);
            for (uint64_t y = 0; y < sh; ++y) {
                const uint32_t *row = reinterpret_cast<const uint32_t *>(p->mapping->getVirtualAddress() + y * pitch);
                auto stats = nabuCpuPixelStats(row, sw); rgb += stats.rgb; alpha += stats.alpha;
            }
            IOLog("NABU27-CPU: source_plane=%u surface=%p rgb_nonzero=%llu alpha_nonzero=%llu\n", i, p->surface, rgb, alpha);
        }
        views[i] = {reinterpret_cast<const uint8_t *>(p->mapping->getVirtualAddress()),
                    p->mapping->getLength(), native_legacy_surface_stride(p->surface),
                    native_legacy_surface_width(p->surface), native_legacy_surface_height(p->surface),
                    p->source, p->destination, half ? nabuHalf : nullptr, true};
    }
    const uint32_t background = updated & 0x80000000U ?
        nabuField<uint32_t>(request, kRequestMasks + 8) | 0xff000000U : nabuBackground;
    if (ready) ready = nabuCpuCompose(nabuScene, bytes, width, height, background, views);
    if (ready) ready = native_legacy_prepare_scanout() &&
        nabuCpuScanout(reinterpret_cast<volatile uint8_t *>(bootMapping->getVirtualAddress()),
                       bootMapping->getLength(), video.v_rowBytes, nabuScene, width, height);
    if (ready) {
        __asm__ volatile("dsb sy" ::: "memory");
        for (unsigned i = 0; i != 4; ++i) if (changed[i]) {
            nabuDropPlane(nabuPlanes[i]); nabuPlanes[i] = candidate[i]; candidate[i] = {};
        }
        nabuBackground = background;
        ++nabuFrames;
        NabuCpuPixelStats pixels = nabuCpuPixelStats(nabuScene, uint64_t(width) * height);
        const bool firstRgb = pixels.rgb && !nabuSeenRgb;
        if (pixels.rgb) nabuSeenRgb = true;
        IOLog("NABU27-CPU: copied=%llu update=%x rgb_nonzero=%llu alpha_nonzero=%llu first_rgb=%u centre=%08x destination_bytes=%llu\n",
            nabuFrames, updated, pixels.rgb, pixels.alpha, firstRgb,
            nabuScene[uint64_t(height / 2) * width + width / 2], bootMapping->getLength());
        if (!nabuSeenRgb) nabuStageProbe(3);
    }
    for (auto &p : candidate) nabuDropPlane(p);
    return ready;
}
