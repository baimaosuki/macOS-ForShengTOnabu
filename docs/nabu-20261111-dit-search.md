# NABU 25F71: DIT/SB exception compatibility and OpenDirectory failure

Device kernel UUID 8452F068-9912-33F5-9FCB-50E9A2F146D2 reaches userland services, then panics with `Failed to open /Search`. The screen also reports repeated loginwindow illegal instructions: `0xd53b42a8` at `0x183153e80`.

The original signed cache maps that instruction uniquely (16 KiB aligned slide 0x2fc0000) to cached dyld at 0x180193e80, subcache .01 offset 0x10be80. Its sequence is MRS x8,DIT; UBFX x0,x8,#24,#1; MSR DIT,#1; SB; RET. The original image is retained without binary rewriting.

The source patch `zzzz-nabu-el0-dit-sb.patch` handles the exact DIT register/immediate forms and SB only for EL0. Missing DIT hardware remains disabled and MRS reports zero; SB executes DSB SY and ISB. Neighboring system registers and privileged traps remain unhandled. It does not advertise a hardware constant-time guarantee.

Verification: 9 host tests execute patched decoder and exception adapter, including FP/LR/XZR, all DIT immediate encodings, EL1 exclusion, mandatory barrier callback, and actual AArch64 barrier object bytes. The full 101-patch source preparation and existing ABI/pager/top-byte/VM/shim audits pass. The macOS CI and packaged native machine code must also be checked.

/Search is an OpenDirectory node, not a filesystem path. Original opendirectoryd contains the failure producer. DIT is a proven loginwindow crash cause; whether fixing it also resolves OpenDirectory startup remains a device validation question. Do not create a /Search directory, suppress the panic, or claim desktop success without execution.
