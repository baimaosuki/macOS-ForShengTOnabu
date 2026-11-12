// SPDX-License-Identifier: GPL-3.0-or-later
#ifndef NABU_STORAGE_CHARACTERISTICS_H
#define NABU_STORAGE_CHARACTERISTICS_H
#ifndef NABU_STORAGE_CHARACTERISTICS_HOST_TEST
#include <IOKit/IOService.h>
#include <IOKit/storage/IOStorageDeviceCharacteristics.h>
#include <libkern/c++/OSDictionary.h>
#include <libkern/c++/OSString.h>
#endif

// Publish on the IOBlockStorageDevice before registerService: both
// IOBlockStorageDriver::handleStart and IOMediaBSDClient's DKIOCISSOLIDSTATE
// read this dictionary from that provider (the latter walks the service plane).
// UFS flash and sparse RAM have no rotational seek latency. Do not infer TRIM,
// write-cache, FUA or flush support from this physical medium classification.
static inline bool nabuSetSolidStateMedium(IOService *device)
{
    if (!device) return false;
    OSDictionary *old = OSDynamicCast(OSDictionary,
        device->getProperty(kIOPropertyDeviceCharacteristicsKey));
    OSDictionary *properties = old ? OSDictionary::withDictionary(old) : OSDictionary::withCapacity(1);
    if (!properties) return false;
    OSString *medium = OSString::withCString(kIOPropertyMediumTypeSolidStateKey);
    bool valid = medium && properties->setObject(kIOPropertyMediumTypeKey, medium) &&
        device->setProperty(kIOPropertyDeviceCharacteristicsKey, properties);
    if (medium) medium->release();
    properties->release();
    return valid;
}
#endif
