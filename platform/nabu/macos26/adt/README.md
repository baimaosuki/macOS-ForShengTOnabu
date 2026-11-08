# nabu ADT artefact

`kernel.macho.adt` here is the **generated** device tree, not a hand-edited
copy: `scripts/native_devtree.py` builds it and
`configure_native_text_state()` adds the text/framebuffer state.

    python3 - <<'PY'
    from pathlib import Path
    from scripts.native_devtree import make_nabu_adt, configure_native_text_state
    p = Path("out/nabu/image/kernel.macho.adt")
    make_nabu_adt(p)
    configure_native_text_state(p, development_root=True, native_gop_display=True)
    PY
    python3 scripts/audit_adt.py out/nabu/image/kernel.macho.adt   # ALL PASS (19/19)

11824 bytes, md5 0ac51a29ce3b6220ae26934b71ca7ed9.

It carries the two properties AppleImage4 dereferences during `start()`:

* `/chosen/crypto-hash-method` = `sha2-384`
* `/chosen/manifest-properties` (empty node)

plus the properties the audit used to flag as missing (`/arm-io device_type`,
`cpu@0 state/timebase-frequency/bus-frequency/clock-frequency`).  With this
artefact and a kernel that leaves `AppleImage4` alone, the boot gets past the
`panic: non-sensical crypto hash method:` wall and stays on the console/QR path
instead of falling into the early `XNU=3` halt screen.
