# 25F71 WindowServer: 11:35 capture and actual abort path

Input `/home/baimao/nabu-boot-log-20261112-113538/boot.txt`, 147784 bytes,
SHA-256 `be5045b7df87b713989daeb0866c6a9f4daa0b8e0ec53ce453c1a0fe88ccec6c`.
The same services kernel was used. This work did not modify the device or code.

Six WindowServer instances receive SIGABRT: PIDs 100, 149, 154, 163, 167, 171.
Each has the same six captured frames. The 54 NABU-ABORT lines are nine records
per crash, not 54 crashes; 43 WindowServer keyword matches are not 43 restarts.
The display-type message occurs 18 times, three per observed instance. There
is no GOP swap log. The provider-type warning does not by itself prove failure:
NativeGOPDisplay subsequently reports hardware started and display on.

## Original 25F71 shared-cache symbolication

The captured PC `0x186cc65e8` resolves to `___pthread_kill+8` at unslid
`0x1804b65e8`, giving shared-cache slide **0x6810000**. All other captured
addresses resolve consistently with the same slide:

| Runtime return address | Original symbol |
|---|---|
| 0x186c08644 | abort + 0x94 |
| 0x18d739a9c | __WSSystemCanCompositeWithMetal_block_invoke + 0x1d4 |
| 0x186b604b0 | _dispatch_client_callout + 0x10 |
| 0x186b49630 | _dispatch_once_callout + 0x20 |
| 0x18d68d3c0 | activate_compositor_default block + 0x4c |

Original SkyLight disassembly confirms the wait-failure branch, beyond the
nearest-symbol inference. At unslid `0x186f299a8..0x186f299b0` it constructs
30000000000 nanoseconds, calls dispatch_time and dispatch_semaphore_wait.
A nonzero wait result branches from `0x186f299d4` to `0x186f29a20`. That path
builds the exact string at `0x1871f3f22`:

    No suitable Metal devices present after %llu seconds.

It finally calls `_abort` at `0x186f29a98`, via indirect stub `0x1871a2060`.
The captured return address is precisely the following instruction,
`0x186f29a9c + slide = 0x18d739a9c`. The separate stack-check failure is a
call at `0x186f29a9c` and would return a different address; it is not this stack.
Another original string on the initial-device check is
`Metal device initially absent.` at `0x18723afc9`.

This establishes the immediate fatal path as the Metal-device availability
wait during default compositor activation. The correlated kernel message
`UserClient display type is 0` is not evidence of a fatal invalid enumeration.
Its numerical semantics were not established by this work. Changing it to 1
has no demonstrated relationship to this abort.

The previous shorter 11:03 snapshot had no captured WindowServer crash. Its
absence of an exit did not establish survival for the longer startup interval.
OD's 60-second resolver timeout also appears in this capture, but is distinct
from the six proven WindowServer aborts. The experimental OD fresh-connection
fence does not provide a Metal/compositor backend.

The boot path needs a functioning compositor route: either an actually usable
Metal device/driver, or a verified CPU/software compositor route that provides
the required rendering and presentation behavior. A framebuffer alone supplies
scanout. Suppressing abort or fabricating successful Metal detection would not
supply rendering. The next useful static investigation is SHENG's actual
software compositor/userland adapter and its compatibility with 25F71 SkyLight.

Machine-readable evidence and original disassembly:
`out/nabu-service-startup/windowserver113538/{evidence.json,frames.json,frames-and-metal.disasm}`.
