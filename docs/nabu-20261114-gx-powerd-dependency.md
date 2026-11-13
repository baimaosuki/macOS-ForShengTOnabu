# GX: restore powerd's real PowerExperience dependency

## Paired evidence

The original `out/nabu-logs-20261113/XNU2LOG-gx.BIN` is 8,400,896 bytes,
MD5 `41eae6a4eeee9a0b47da35dc9b38cdf9`. Both CRC-validated slots are valid;
the selected sequence is 17595, boot ID `8633123e000000008c43000000000000`.
The decoded body is 663,093 bytes, SHA256
`0a69e94fe27e9b1676d16d9ad4d85474f1cab020408ae52b7a6c33d8a5cc8e85`.
It is a committed snapshot, not a complete boot: 476 bytes were lost on
contention and the last timestamp is about 277 seconds. The provided text
contains a reader preamble; line counts depend on CR/LF handling.

The kernel was built on November 13 at 19:15:01 UTC. The running observer
prints `powerd_after_discovery=0,60,120`, and three successful powerd snapshots
are present. This proves that the newly delivered observer ran.

At 199.649613177 and 259.525317656 seconds, powerd PID 46 / TID 414 has the
same complete main-thread chain. The original shared cache UUID is
`46e0097ff38536c884a9a40d315a32d1`, slide `0xa628000`. Unslid addresses:

| Address | Original symbol |
| --- | --- |
| `0x1804adc34` | `mach_msg2_trap + 8` |
| `0x1804c0574` | `mach_msg2_internal + 0x4c` |
| `0x1804b69c0` | `mach_msg_overwrite + 0x1e0` |
| `0x1804adfc0` | `mach_msg + 0x18` |
| `0x180351c64` | `_dispatch_mach_send_and_wait_for_reply + 0x224` |
| `0x180352004` | `dispatch_mach_send_with_result_and_wait_for_reply + 0x3c` |
| `0x1801caeb0` | `xpc_connection_send_message_with_reply_sync + 0x11c` |
| `0x181dbb5fc` | `__NSXPCCONNECTION_IS_WAITING_FOR_A_SYNCHRONOUS_REPLY__ + 0x10` |
| `0x181db9634` | `-[NSXPCConnection _sendInvocation:orArguments:count:methodSignature:selector:withProxy:] + 0x83c` |
| `0x181dbf848` | `-[NSXPCConnection _sendSelector:withProxy:arg1:arg2:] + 0x80` |
| `0x181dbf770` | `__NSXPCDistantObjectSimpleMessageSend2 + 0x44` |
| `0x262c4cd3c` | `-[ResourceConnection createResourceHint:] + 0x148` |
| `0x262c4bac0` | `-[ResourceHint initWithResourceType:andState:] + 0x7c` |

The callers are in the recorded powerd executable, then a dispatch barrier
synchronous invocation. The only other thread is in `workq_kernreturn`.
This is live unsuspended evidence; the sampling code never pauses powerd.

## Exact service and gate error

`-[ResourceConnection init]` at `0x262c4d4d4` constructs an NSXPCConnection:

```asm
0x262c4d550: adrp x2, #0x29d40e000
0x262c4d554: add  x2, x2, #0xf38
0x262c4d558: mov  w3, #0x1000
0x262c4d55c: bl   #0x262c51a00 ; objc_msgSend$initWithMachServiceName:options:
```

CFString `0x29d40ef38` has length 40; its original shared-cache rebase points
to `0x262c50201`: **`com.apple.powerexperienced.resourceusage`**.
`createResourceHint:` obtains `synchronousRemoteObjectProxyWithErrorHandler:`
at `0x262c4ccc8`, then invokes `createResourceHint:withReply:` at
`0x262c4cd38`; the recorded return PC is the immediately following instruction.

The paired 25F71 launchd job is
`/System/Library/LaunchDaemons/com.apple.powerexperienced.plist`, whose Label
is `com.apple.powerexperienced`, Program is `/usr/libexec/powerexperienced`,
and MachServices includes the exact service above. The same capture says:

```text
NABU-MINIMAL: parked pid=110 ppid=1 label=com.apple.powerexperienced mode=kernel-hold seq=56
```

At 209.699976770 seconds loginwindow PID 109 / TID 481 enters call 2272,
message ID 73023, with a receiver snapshot naming powerd PID 46. No return is
captured before the snapshot ends. The paired IOKit disassembly previously
proved this is `IOHIDPostEvent`'s 36-byte input-activity report, not a large
TCC/IconServices request. Earlier non-minimal captures return from this RPC
in under 1 ms. GX now identifies the real initialization dependency held by
our minimal-boot policy. This is a specific functional defect; it does not
prove that no later blocker exists after it is removed.

## Reference implementations checked again

Downloaded fresh, pinned evidence is in `.work/nabu-gx-references-20261114/`:

* UniXNU `14236be44fd56fb94d11594864ffb70780352150`, latest queried revision:
  `docs/porting/macos.md`, `display.md`, `bring-up.md`.
* SHENG board `1108068537d5396edb2c63b2f8c6f5b97b0a4bbc`, latest queried revision:
  `board.toml`, including the `macos-27.3-26C5035r` override.
* User-provided iOS16 NABU source revision
  `a7148554052f42159207245e2070b96492a046da`: `DISPLAY-CLOCK-BRINGUP.md` and
  `DESKTOP-SERVICES-BRINGUP.md`; the paired HakiOS framebuffer remains
  `96ffbc532e970f3540885755d457e401e04d4707`.

SHENG's macOS path preserves the native macOS service infrastructure, Data
seed, TimeSync and input/display ABI. Its board configuration does not apply
our blanket xpcproxy hold policy. The iOS16 desktop audit preserves RunAtLoad
and KeepAlive for SpringBoard/backboardd/runningboardd, and ordinary lsd is
on demand. Its early CA timer branch was verified against 20F66, not 25F71.
The portable lesson is to restore actual service dependencies while retaining
the paired native graphics protocol. Copying an iOS selector, injecting an
unverified timer switch, or making a synchronous RPC falsely succeed would
not address GX's blocked PowerExperience service.

## Change and validation

Add only the exact `com.apple.powerexperienced` label: policy becomes 41 exact
labels / 12 existing prefixes. Other held cloud/noncore jobs stay held.
Admission and parking now have independent 64-record diagnostic budgets.
An allow record means an admission decision, not service readiness.

The previous code never emitted allow records at all. Its 64-entry cap was
only on parked records, so `allow=0` was not proof that the compiled allowlist
was inactive. Likewise `peer=no-send` is initialized when MACH64_SEND_MSG is
absent: it means a receive-only call, not a missing send right. The observed
receive-only 16 ms timeouts and native sizing replies are not changed.

ASan/UBSan tests exercise the production header with the observed dependency,
keep unrelated jobs held, reject similarly named jobs, and verify independent
admission logging after the park quota is exhausted. The original native
kernel hold and paired IPC tests also run. A complete source prepare precedes
the Intel CI, and final delivery must verify the emitted policy, eight CPUs,
native display bindings, signatures/trustcache and original logger token.
Hardware login/desktop success remains unverified until the new boot.
