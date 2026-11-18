// SPDX-License-Identifier: GPL-3.0-or-later
// Included inside NativeLegacyDisplay. 26C5035r request +0x478 is the native
// layer/update mask; +0x47c accumulates completed state, not disabled layers.
// A selected nonnull surface replaces the plane; a selected null clears it.
struct NabuCpuKeptPlane {
    OSObject *surface = nullptr;
    IOMemoryDescriptor *memory = nullptr;
    IOMemoryMap *mapping = nullptr;
    NabuGopRect source{}, destination{};
};
NabuCpuKeptPlane nabuPlanes[4]{};
uint32_t *nabuScene = nullptr, *nabuLastScene = nullptr;
bool nabuLastSceneValid = false, nabuConsoleClaimed = false;
uint64_t nabuWrittenBytes = 0;
uint64_t nabuSceneBytes = 0, nabuFrames = 0;
uint32_t nabuBackground = 0xff000000U;
uint8_t *nabuHalf = nullptr;
bool nabuSeenRgb = false;
unsigned nabuProbeStages = 0;
uint64_t nabuLastTraceState = 0, nabuRejectCount = 0, nabuLastRejectState = 0;
bool nabuTraceSeen = false, nabuLastBlack = true;
void nabuStageProbe(unsigned stage) {
    if (nabuFrames || nabuSeenRgb || !bootMapping || !primary || stage < 1 || stage > 3) return;
    PE_Video video{};
    if (PE_current_console(&video) || video.v_depth != 32 ||
        video.v_baseAddr != bootMapping->getPhysicalAddress()) return;
    const bool firstStage = !(nabuProbeStages & (1U << (stage - 1)));
    nabuProbeStages |= 1U << (stage - 1);
    if (!nabuCpuStageBars(reinterpret_cast<volatile uint8_t *>(bootMapping->getVirtualAddress()),
        bootMapping->getLength(), video.v_rowBytes, video.v_width, video.v_height, nabuProbeStages)) return;
    __asm__ volatile("dsb sy" ::: "memory");
    if (firstStage) IOLog("NABU27-CPU: probe_stage=%u phys=%llx stride=%lu cache=write-combine first_rgb=%u\n",
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
    if (nabuLastScene) IOFree(nabuLastScene, nabuSceneBytes);
    nabuLastScene = nullptr; nabuLastSceneValid = false;
    nabuScene = nullptr; nabuSceneBytes = 0;
    if (nabuHalf) IOFree(nabuHalf, 65536);
    nabuHalf = nullptr;
}
uint64_t nabuCpuRequestState(const OriginalLegacySwapRequest *request) {
    uint64_t state = UINT64_C(1469598103934665603);
    if (!request) return state;
    for (unsigned at = kRequestMasks; at <= kRequestDims + 4; at += 4)
        state = nabuCpuTraceMix(state, nabuField<uint32_t>(request, at));
    for (unsigned i = 0; i < 4; ++i) {
        OSObject *p = nabuField<OSObject *>(request, kRequestSurfaces + i * sizeof(void *));
        state = nabuCpuTraceMix(state, reinterpret_cast<uintptr_t>(p));
        if (p && p->metaCast("IOSurface")) {
            state = nabuCpuTraceMix(state, native_legacy_surface_format(p));
            state = nabuCpuTraceMix(state, native_legacy_surface_width(p));
            state = nabuCpuTraceMix(state, native_legacy_surface_height(p));
            state = nabuCpuTraceMix(state, native_legacy_surface_stride(p));
        }
        for (unsigned rect = 0; rect < 2; ++rect)
            for (unsigned word = 0; word < 16; word += 4)
                state = nabuCpuTraceMix(state, nabuField<uint32_t>(request, (rect ? kRequestRect1 : kRequestRect0) + i * 16 + word));
    }
    return state;
}
bool nabuRejectCpu(const OriginalLegacySwapRequest *request, unsigned reason, int plane = -1) {
    const uint64_t state = nabuCpuTraceMix(nabuCpuRequestState(request), reason);
    ++nabuRejectCount;
    if (nabuRejectCount <= 10 || nabuRejectCount % 50 == 0 || state != nabuLastRejectState) {
        IOLog("NABU27-CPU: rejected=%llu reason=%u plane=%d updated=%x completed=%x\n",
            nabuRejectCount, reason, plane, request ? nabuField<uint32_t>(request,kRequestMasks) : 0,
            request ? nabuField<uint32_t>(request,kRequestCompleted) : 0);
        if (request && plane >= 0) {
            OSObject *p = nabuField<OSObject *>(request,kRequestSurfaces+plane*sizeof(void *));
            if (p && p->metaCast("IOSurface"))
                IOLog("NABU27-CPU: rejected_surface=%p fmt=%08x width=%llu height=%llu stride=%llu\n",
                    p,native_legacy_surface_format(p),native_legacy_surface_width(p),
                    native_legacy_surface_height(p),native_legacy_surface_stride(p));
        }
    }
    nabuLastRejectState = state;
    return false;
}
bool nabuValidateCpu(const OriginalLegacySwapRequest *request) {
    if (!request || !logicalPM || !primary || !bootMapping) return nabuRejectCpu(request,1);
    const uint32_t updated = nabuField<uint32_t>(request, kRequestMasks);
    if (updated & ~0x8000000fU) return nabuRejectCpu(request,2);
    if (!(updated & 15)) return nabuRejectCpu(request,3);
    uint32_t width = 0, height = 0; getDisplayDimensions(&width, &height);
    if (!width || !height || nabuField<uint32_t>(request,kRequestDims) != width ||
        nabuField<uint32_t>(request,kRequestDims+4) != height) return nabuRejectCpu(request,4);
    for (unsigned at = kRequestOptions; at <= kRequestOptions+8; at += 4)
        if (nabuField<uint32_t>(request,at)) return nabuRejectCpu(request,5);
    for (unsigned i = 0; i != 4; ++i) {
        if (!(updated & (1U << i))) continue;
        OSObject *surface = nabuField<OSObject *>(request,kRequestSurfaces+i*sizeof(void *));
        if (!surface) continue; // Native enabled bit may carry an explicit null/blank layer.
        if (!surface->metaCast("IOSurface")) return nabuRejectCpu(request,6,i);
        const uint32_t format = native_legacy_surface_format(surface);
        if (format != 0x42475241 && format != 0x52476841) return nabuRejectCpu(request,7,i);
        if (native_legacy_surface_planes(surface)) return nabuRejectCpu(request,8,i);
        IOMemoryDescriptor *memory = native_legacy_surface_descriptor(surface);
        const uint64_t sw = native_legacy_surface_width(surface), sh = native_legacy_surface_height(surface);
        const uint64_t stride = native_legacy_surface_stride(surface), element = format == 0x52476841 ? 8 : 4;
        if (!memory || !sw || !sh || sw > 16384 || sh > 16384 || stride < sw*element ||
            stride > UINT64_MAX/sh || stride*sh > memory->getLength()) return nabuRejectCpu(request,9,i);
        NabuGopClip clip{};
        if (!nabuGopClip(nabuField<NabuGopRect>(request,kRequestRect0+16*i),
            nabuField<NabuGopRect>(request,kRequestRect1+16*i),sw,sh,width,height,clip)) return nabuRejectCpu(request,10,i);
    }
    return true;
}
bool nabuSampleIncoming(const OriginalLegacySwapRequest *request,unsigned slot,OSObject *surface) {
    if (!surface || !surface->metaCast("IOSurface")) return false;
    const uint32_t format = native_legacy_surface_format(surface);
    if ((format != 0x42475241 && format != 0x52476841) || native_legacy_surface_planes(surface)) return false;
    IOMemoryDescriptor *memory = native_legacy_surface_descriptor(surface);
    if (!memory) return false;
    memory->retain();
    if (memory->prepare() != kIOReturnSuccess) { memory->release(); return false; }
    IOMemoryMap *map = memory->createMappingInTask(kernel_task,0,kIOMapAnywhere|kIOMapReadOnly|kIOMapCopybackCache);
    if (format == 0x52476841 && !nabuHalf) {
        const uint8_t *positive = halfTable();
        nabuHalf = positive ? static_cast<uint8_t *>(IOMalloc(65536)) : nullptr;
        if (nabuHalf) { memcpy(nabuHalf,positive,32768); bzero(nabuHalf+32768,32768); }
    }
    bool sampled = false;
    if (map && map->getVirtualAddress() && map->getLength() >= memory->getLength() &&
        (format != 0x52476841 || nabuHalf)) {
        flush_dcache64(map->getVirtualAddress(),unsigned(map->getLength()),false);
        const auto sample = nabuCpuSampleSource(reinterpret_cast<const uint8_t *>(map->getVirtualAddress()),
            map->getLength(),native_legacy_surface_stride(surface),native_legacy_surface_width(surface),
            native_legacy_surface_height(surface),nabuField<NabuGopRect>(request,kRequestRect0+slot*16),
            format == 0x52476841 ? nabuHalf : nullptr);
        sampled = true;
        IOLog("NABU27-CPU: source_plane=%u active=0 incoming_only=1 request_surface=%p source_va=%llx fmt=%08x samples=%llu sampled_rgb_nonzero=%llu sampled_alpha_nonzero=%llu raw_rgb_nonzero=%llu first_raw=%016llx\n",
            slot,surface,map->getVirtualAddress(),format,sample.count,sample.rgb,sample.alpha,sample.rawRgb,sample.firstRaw);
    }
    OSSafeReleaseNULL(map); memory->complete(); memory->release();
    return sampled;
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
    if (!nabuLastScene) nabuLastScene = static_cast<uint32_t *>(IOMalloc(bytes));
    if (!nabuLastScene || nabuSceneBytes != bytes) return false;
    const uint32_t updated = nabuField<uint32_t>(request, kRequestMasks);
    const uint32_t completed = nabuField<uint32_t>(request, kRequestCompleted);
    NabuCpuKeptPlane candidate[4]{};
    NabuCpuPlaneView views[4]{};
    bool changed[4]{};
    bool ready = true;
    uint32_t incomingMask = 0;
    for (unsigned slot = 0; slot < 4; ++slot)
        if (nabuField<OSObject *>(request,kRequestSurfaces+slot*sizeof(void *))) incomingMask |= 1U<<slot;
    const bool heldBlank = nabuSleepRejected && !incomingMask && nabuSeenRgb;
    for (unsigned i = 0; i != 4 && ready; ++i) {
        NabuCpuKeptPlane *p = &nabuPlanes[i];
        if ((updated & (1U << i)) && !heldBlank) {
            changed[i] = true; p = &candidate[i];
            if (nabuField<OSObject *>(request,kRequestSurfaces+i*sizeof(void *))) {
                p->surface = nabuField<OSObject *>(request, kRequestSurfaces + i * sizeof(void *));
                p->surface->retain();
                p->memory = native_legacy_surface_descriptor(p->surface); p->memory->retain();
                if (p->memory->prepare() != kIOReturnSuccess) {
                    OSSafeReleaseNULL(p->memory); nabuRejectCpu(request,11,i); ready = false; break;
                }
                p->mapping = p->memory->createMappingInTask(kernel_task, 0, kIOMapAnywhere | kIOMapReadOnly | kIOMapCopybackCache);
                p->source = nabuField<NabuGopRect>(request, kRequestRect0 + 16 * i);
                p->destination = nabuField<NabuGopRect>(request, kRequestRect1 + 16 * i);
                if (!p->mapping || !p->mapping->getVirtualAddress() ||
                    p->mapping->getLength() < p->memory->getLength()) { nabuRejectCpu(request,12,i); ready = false; break; }

            }
        }
        if (!p->surface) continue;
        const bool half = native_legacy_surface_format(p->surface) == 0x52476841;
        if (half && !nabuHalf) {
            const uint8_t *positive = halfTable();
            nabuHalf = positive ? static_cast<uint8_t *>(IOMalloc(65536)) : nullptr;
            if (!nabuHalf) { nabuRejectCpu(request,13,i); ready = false; break; }
            memcpy(nabuHalf, positive, 32768); bzero(nabuHalf + 32768, 32768);
        }
        flush_dcache64(p->mapping->getVirtualAddress(), unsigned(p->mapping->getLength()), false);
        views[i] = {reinterpret_cast<const uint8_t *>(p->mapping->getVirtualAddress()),
                    p->mapping->getLength(), native_legacy_surface_stride(p->surface),
                    native_legacy_surface_width(p->surface), native_legacy_surface_height(p->surface),
                    p->source, p->destination, half ? nabuHalf : nullptr, true};
    }
    const uint32_t background = (updated & 0x80000000U) && !heldBlank ?
        nabuField<uint32_t>(request, kRequestMasks + 8) | 0xff000000U : nabuBackground;
    if (ready) ready = nabuCpuCompose(nabuScene, bytes, width, height, background, views);
    if (ready && !nabuConsoleClaimed) {
        nabu_console_claim_framebuffer();
        nabuConsoleClaimed = true;
        IOLog("NABU27-CPU: console_owner=WindowServer text=off ram_esp_log=on panic_takeover=on\n");
    }
    const bool hadRgb = nabuSeenRgb;
    if (ready) ready = native_legacy_prepare_scanout() &&
        nabuCpuScanout(reinterpret_cast<volatile uint8_t *>(bootMapping->getVirtualAddress()),
                       bootMapping->getLength(), video.v_rowBytes, nabuScene, width, height,
                       nabuLastScene, nabuLastSceneValid, &nabuWrittenBytes, &nabuSeenRgb);
    if (ready) {
        __asm__ volatile("dsb sy" ::: "memory");
        for (unsigned i = 0; i != 4; ++i) if (changed[i]) {
            nabuDropPlane(nabuPlanes[i]); nabuPlanes[i] = candidate[i]; candidate[i] = {};
        }
        nabuBackground = background;
        ++nabuFrames;
        nabuLastSceneValid = true;
        const bool trace = nabuCpuTraceDue(nabuFrames,false,false,false);
        NabuCpuSourceSample pixels{};
        if (trace) pixels = nabuCpuSampleSource(reinterpret_cast<const uint8_t *>(nabuScene),
            bytes, uint64_t(width)*4, width, height, {0,0,int(width),int(height)}, nullptr);
        const bool firstRgb = nabuSeenRgb && !hadRgb;
        if (pixels.rgb) nabuSeenRgb = true;
        uint32_t active = 0, nonnull = 0, layers = 0;
        uint64_t state = nabuCpuRequestState(request);
        for (unsigned i = 0; i < 4; ++i) {
            if (nabuField<OSObject *>(request,kRequestSurfaces+i*sizeof(void *))) nonnull |= 1U<<i;
            const auto &p = nabuPlanes[i];
            state = nabuCpuTraceMix(state,reinterpret_cast<uintptr_t>(p.surface));
            if (p.surface) { active |= 1U<<i; ++layers; }
        }
        const bool changed = !nabuTraceSeen || state != nabuLastTraceState;
        const bool black = !pixels.rgb;
        if (nabuCpuTraceDue(nabuFrames,changed,firstRgb,nabuTraceSeen && black != nabuLastBlack)) {
            IOLog("NABU27-CPU: copied=%llu update=%x completed=%x active_mask=%x layer_count=%u slots_nonnull=%x written_bytes=%llu\n",
                nabuFrames,updated,completed,active,layers,nonnull,nabuWrittenBytes);
            IOLog("NABU27-CPU: frame_sample=%llu sampled_rgb_nonzero=%llu sampled_alpha_nonzero=%llu first_rgb=%u centre=%08x held_sleep_blank=%u\n",
                pixels.count,pixels.rgb,pixels.alpha,firstRgb,nabuScene[uint64_t(height/2)*width+width/2],heldBlank);
            for (unsigned i = 0; i < 4; ++i) {
                const auto &p = nabuPlanes[i];
                OSObject *incoming = nabuField<OSObject *>(request,kRequestSurfaces+i*sizeof(void *));
                if (!p.surface) {
                    if (nabuSampleIncoming(request,i,incoming)) continue;
                    const bool typed = incoming && incoming->metaCast("IOSurface");
                    IOLog("NABU27-CPU: source_plane=%u active=0 request_surface=%p request_fmt=%08x request_width=%llu request_height=%llu samples=0\n",
                        i,incoming,typed ? native_legacy_surface_format(incoming) : 0,
                        typed ? native_legacy_surface_width(incoming) : 0,typed ? native_legacy_surface_height(incoming) : 0);
                    continue;
                }
                if (incoming && incoming != p.surface) nabuSampleIncoming(request,i,incoming);
                const uint32_t format = native_legacy_surface_format(p.surface);
                const uint64_t sw = native_legacy_surface_width(p.surface),sh = native_legacy_surface_height(p.surface);
                const auto sample = nabuCpuSampleSource(reinterpret_cast<const uint8_t *>(p.mapping->getVirtualAddress()),
                    p.mapping->getLength(),native_legacy_surface_stride(p.surface),sw,sh,p.source,
                    format == 0x52476841 ? nabuHalf : nullptr);
                IOLog("NABU27-CPU: source_plane=%u active=1 request_surface=%p surface=%p source_va=%llx fmt=%08x width=%llu height=%llu\n",
                    i,incoming,p.surface,p.mapping->getVirtualAddress(),format,sw,sh);
                IOLog("NABU27-CPU: plane_geometry=%u stride=%llu descriptor_bytes=%llu mapped_bytes=%llu src=%d,%d,%d,%d dst=%d,%d,%d,%d\n",
                    i,native_legacy_surface_stride(p.surface),p.memory->getLength(),p.mapping->getLength(),
                    p.source.x,p.source.y,p.source.width,p.source.height,p.destination.x,p.destination.y,p.destination.width,p.destination.height);
                IOLog("NABU27-CPU: plane_sample=%u samples=%llu sampled_rgb_nonzero=%llu sampled_alpha_nonzero=%llu raw_rgb_nonzero=%llu first_raw=%016llx full_mapping=1\n",
                    i,sample.count,sample.rgb,sample.alpha,sample.rawRgb,sample.firstRaw);
            }
        }
        nabuTraceSeen = true; nabuLastTraceState = state; nabuLastBlack = black;
        // Probe bars never write over a frame owned by WindowServer.
    }
    for (auto &p : candidate) nabuDropPlane(p);
    return ready;
}
