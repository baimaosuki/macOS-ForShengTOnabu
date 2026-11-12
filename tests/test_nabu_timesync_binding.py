"""The Ethernet fallback must never authorize a call-site or altered PAC tuple."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from nabu_timesync_binding import validate_ethernet_slot


def test_exact_inherited_slot_only():
    original = dict(authenticated=True, key=0, diversity=0x1234, address_diversity=True)
    slot = dict(slot=17, new_site=0x1000088, new_target=0x2000000, original=original)
    item = dict(new_site=slot['new_site'], new_target=slot['new_target'], **original)
    assert validate_ethernet_slot(item, [slot]) is slot
    for key in item:
        changed = dict(item)
        changed[key] = int(changed[key]) + 1
        with pytest.raises(ValueError, match='exact inherited vtable slot'):
            validate_ethernet_slot(changed, [slot])
    with pytest.raises(ValueError):
        validate_ethernet_slot(item, [slot, slot])


def test_reserved_sdk_names_only_bind_reviewed_actual_vtable():
    from nabu_timesync_binding import ethernet_reserved_slots, macho
    from native_macho import symbols
    root = Path(__file__).resolve().parents[1]
    kcpath = root / 'out/nabu-bootkc/kernelcache.release.mac13g.nabu.kc'
    companion = root / 'out/nabu-service-startup/nabu-25F71-services-43115207926/linked-ufs/NativeUfs.macho'
    if not kcpath.exists() or not companion.exists():
        pytest.skip('paired 25F71 collection and built service companion unavailable')
    data = kcpath.read_bytes()
    own = symbols(companion)
    result = ethernet_reserved_slots(data, macho(data), own, 0x10000000, 0, companion.read_bytes())
    assert sorted(x['slot'] for values in result.values() for x in values) == [331, 332, 333, 334]
    # Shifting the subclass table turns otherwise valid imports into incorrect
    # inherited slots. A generic "any reserved slot" fallback would accept it.
    shifted = dict(own)
    shifted['__ZTV20NabuTimeSyncIdentity'] += 8
    with pytest.raises(ValueError, match='reviewed 25F71 replacement'):
        ethernet_reserved_slots(data, macho(data), shifted, 0x10000000, 0, companion.read_bytes())


def test_native_bringup_option_preserves_real_clock_lookup():
    from inspect_nabu_timesync_identity import native_bringup_policy
    from inspect_macos26_security import macho, backed_offset
    root = Path(__file__).resolve().parents[1]
    kcpath = root / 'out/nabu-bootkc/kernelcache.release.mac13g.nabu.kc'
    if not kcpath.exists():
        pytest.skip('paired 25F71 collection unavailable')
    data = kcpath.read_bytes()
    kc = macho(data)
    family = macho(data, kc['entries']['com.apple.iokit.IOTimeSyncFamily'][1])
    policy = native_bringup_policy(data, kc, family)
    assert policy['option'] == '-timesync_bringup'
    assert policy['presence_flag'] is True
    assert policy['bringup_wait_ms'] == 5000
    assert policy['bypasses_lookup'] is False
    assert policy['resource'] == 'IOTimeSyncClockManager'
    # Refuse a changed wait choice, lookup call, success guard, translation
    # update or publication. Finding the option string alone is insufficient.
    for offset in (0x40, 0x44, 0x48, 0x4c, 0x5c, 0x94, 0x9c, 0xcc, 0xdc):
        changed = bytearray(data)
        at = backed_offset(family['segments'], policy['delayed_lookup'] + offset, 4, len(data))
        changed[at] ^= 1
        with pytest.raises(ValueError, match='TimeSync native bringup policy'):
            native_bringup_policy(bytes(changed), kc, family)
