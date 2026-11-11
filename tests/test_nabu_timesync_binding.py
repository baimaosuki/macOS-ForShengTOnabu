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
