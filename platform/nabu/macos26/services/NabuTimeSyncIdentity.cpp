/* SPDX-License-Identifier: GPL-3.0-or-later */
// Stable local identity for the original 25F71 TimeSync clock. No radio or NIC.
#include "NabuTimeSyncIdentity.h"
#include <IOKit/IOLib.h>
#include <IOKit/IODeviceTreeSupport.h>
#include <kern/thread.h>
#include <libkern/c++/OSData.h>
#pragma clang diagnostic push
#pragma clang diagnostic ignored "-Wdeprecated-declarations"
#pragma clang diagnostic ignored "-Wdeprecated-anon-enum-enum-conversion"
#define __PRIVATE_SPI__
#include <IOKit/network/IOEthernetController.h>
#include <IOKit/network/IONetworkMedium.h>
#include <IOKit/network/IONetworkInterface.h>
#include <IOKit/network/IOOutputQueue.h>
#pragma clang diagnostic pop

// OSMetaClass constructor in the paired 25F71 collection passes 0x118.
// Size is necessary but not sufficient: the stager must also compare inherited
// vtable slots/PAC contracts against this exact collection before admission.
static_assert(sizeof(IOEthernetController) == 0x118, "25F71 Ethernet class ABI");

class NabuTimeSyncIdentity : public IOEthernetController {
    OSDeclareDefaultStructors(NabuTimeSyncIdentity)
    IONetworkInterface *interface = nullptr;
    uint8_t address[6] = {};
public:
    // Avoid an IOResources -> IOBSD wait cycle; BSD attaches asynchronously.
    UInt32 getFeatures() const override { return kIONetworkFeatureNoBSDWait; }
    bool start(IOService *provider) override;
    void stop(IOService *provider) override {
        setLinkStatus(kIONetworkLinkValid, nullptr, 0);
        if (interface) { detachInterface(interface); OSSafeReleaseNULL(interface); }
        IOEthernetController::stop(provider);
    }
    IOReturn getHardwareAddress(IOEthernetAddress *out) override {
        if (!out) return kIOReturnBadArgument;
        memcpy(out->bytes, address, sizeof address);
        return kIOReturnSuccess;
    }
    IOReturn enable(IONetworkInterface *which) override {
        if (which != interface) return kIOReturnBadArgument;
        setLinkStatus(kIONetworkLinkValid, nullptr, 0);
        return kIOReturnSuccess;
    }
    IOReturn disable(IONetworkInterface *which) override { return enable(which); }
    UInt32 outputPacket(mbuf_t packet, void *) override {
        if (packet) freePacket(packet);
        return kIOReturnOutputDropped;
    }
};
OSDefineMetaClassAndStructors(NabuTimeSyncIdentity, IOEthernetController)

bool NabuTimeSyncIdentity::start(IOService *provider)
{
    IORegistryEntry *chosen = IORegistryEntry::fromPath("/options", gIODTPlane);
    OSData *identity = chosen ? OSDynamicCast(OSData, chosen->getProperty("platform-uuid")) : nullptr;
    uint64_t hash = 0xcbf29ce484222325ULL;
    // No global fallback MAC: missing identity must be diagnosed rather than
    // silently assigning the same clock identifier to every tablet.
    bool valid = identity && identity->getLength() == 16;
    bool nonzero = false;
    if (valid) {
        const uint8_t *bytes = static_cast<const uint8_t *>(identity->getBytesNoCopy());
        for (unsigned i = 0; i < identity->getLength(); ++i) {
            nonzero |= bytes[i] != 0;
            hash = (hash ^ bytes[i]) * 0x100000001b3ULL;
        }
    }
    OSSafeReleaseNULL(chosen);
    if (!valid || !nonzero) return false;
    for (unsigned i = 0; i < 6; ++i) address[i] = static_cast<uint8_t>(hash >> (8 * i));
    address[0] = (address[0] & 0xfc) | 0x02; // local unicast
    if (!IOEthernetController::start(provider)) return false;
    setProperty("NetworkConnectivityImplemented", false);
    setProperty("NabuTimeSyncIdentityOnly", true);
    setProperty("IOBuiltin", false);
    OSDictionary *media = OSDictionary::withCapacity(1);
    IONetworkMedium *none = IONetworkMedium::medium(kIOMediumEthernetNone, 0);
    bool ready = media && none && IONetworkMedium::addMedium(media, none) &&
        publishMediumDictionary(media) && setCurrentMedium(none) &&
        setLinkStatus(kIONetworkLinkValid, none, 0);
    OSSafeReleaseNULL(none);
    OSSafeReleaseNULL(media);
    if (!ready || !attachInterface(&interface, false)) {
        IOEthernetController::stop(provider);
        return false;
    }
    interface->setProperty("NetworkConnectivityImplemented", false);
    interface->setProperty("NabuTimeSyncIdentityOnly", true);
    interface->setProperty("IOBuiltin", false);
    interface->registerService(); // native IONetworkStack/configd assigns enN
    registerService();
    IOLog("NabuTimeSyncIdentity: stable local Ethernet identity published; carrier absent\n");
    return true;
}

extern "C" { volatile int nabu_timesync_identity_state = 0; }
static void nabuTimeSyncWorker(void *, wait_result_t)
{
    IOService *parent = IOService::getResourceService();
    NabuTimeSyncIdentity *controller = OSTypeAlloc(NabuTimeSyncIdentity);
    bool attached = false;
    if (!controller || !controller->init(nullptr) || !parent) nabu_timesync_identity_state = -1;
    else if (!(attached = controller->attach(parent))) nabu_timesync_identity_state = -2;
    else if (!controller->start(parent)) nabu_timesync_identity_state = -3;
    else nabu_timesync_identity_state = 1; // boot-lifetime reference retained
    if (nabu_timesync_identity_state != 1) {
        if (attached) controller->detach(parent);
        OSSafeReleaseNULL(controller);
        IOLog("NabuTimeSyncIdentity: startup failed (%d)\n", nabu_timesync_identity_state);
    }
    thread_terminate(current_thread());
}
extern "C" void nabu_timesync_identity_start(void)
{
    if (!__sync_bool_compare_and_swap(&nabu_timesync_identity_state, 0, 2)) return;
    thread_t thread = THREAD_NULL;
    if (kernel_thread_start(nabuTimeSyncWorker, nullptr, &thread) == KERN_SUCCESS)
        thread_deallocate(thread);
    else nabu_timesync_identity_state = -4;
}
