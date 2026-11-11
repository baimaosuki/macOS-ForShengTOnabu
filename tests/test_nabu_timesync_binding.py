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
