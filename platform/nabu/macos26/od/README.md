# 25F71 callback-free connection startup candidate

`scripts/prepare_nabu_od_startup.py` prepares an exact-input, experimental
OpenDirectory adaptation. It addresses the cold-start dependency exposed by
the initial PlistFile assertion without pre-setting connection state, replacing
the original worker, suppressing assertions, or changing kernel scheduling.
The first device attempt did not activate the candidate: the userland union
mount returned EPERM and the original shim incorrectly failed required mount
phase 1. The optional-overlay fallback is now corrected; its rebuilt shim and
the OD execution path still require device validation. See
`docs/nabu-20261112-od-overlay-eperm.md`.

The newer 11:03:44 device capture has a registered kauth request timeout and
does not reproduce the old assertion. See `docs/nabu-20261112-od-registered-kauth.md`.
That newer failure is outside this candidate's demonstrated scope.

## Original dependencies

The exact original ARM64E slice has SHA-256
`5e1abdd7d2fd30ee95c8bb07fee1259d5bc7f7cc2b047c1aa415dc0b539a1452`.

1. PlistFile passes a module context and three NULL callbacks to
   `_odconnection_create_ext`. The new connection has a serial state queue
   at `+0x28`, a distinct serial `.operations` queue at `+0x138`, and a
   concurrent overcommit `.requests` queue at `+0xa8`. Both serial queues
   use `dispatch_queue_create(label, NULL)` and independently target the
   default overcommit root in the actual 25F71 libdispatch machine code.
2. `_odconnection_connect` first submits a timer-reset block to the operations
   queue through its dispatch group. For this fresh connection the timeout
   source is NULL, so that original block returns without setting a timer.
3. `dispatch_sync(state, submit)` runs `0x10002366c`. This sets the connecting
   flag, initializes the connection status to `0x834`, retains the objects,
   queues the original worker `0x1000237bc` with `dispatch_barrier_async` on
   operations, retains the semaphore, and queues signal block `0x100023988`
   behind the worker on operations. It then releases the state queue.
4. The worker initializes its log if necessary and checks the connect callback.
   With NULL callback it writes zero to status at `0x100023890`. It then
   **synchronously enters the state queue** at `0x1000238ec`. The original
   completion block at `0x100023938` publishes the connected flag when status
   is zero (or invokes the original disconnect path on error), and clears the
   connecting flag. The worker releases its original retained objects.
5. The next operations block signals and releases the semaphore. Original
   `_odconnection_connect` waits until a five-second wall-clock deadline, then
   returns **connection status**, not the wait return value. A delayed signal
   after the worker has already written zero alone cannot cause this assertion.

The original connection-status stores are `0x1000236bc` (initial status),
`0x100023888` (callback result), and `0x100023890` (NULL callback success).
The default `0x834` is not itself the libdispatch wait return value. Original
libdispatch semaphore waiting normally returns success or `49` (timeout).
Mach error 14 retries; invalid/error cases enter the library's crash path.
There is no demonstrated ordinary non-timeout error-return path into the
observed PlistFile assertion.

Changing the worker's `dispatch_barrier_async` directly to synchronous execution
would hold the state queue while the worker synchronously re-enters it. That
creates a deadlock and is deliberately not the candidate.

## Candidate and boundary

Only the first connection call inside `_odconnection_create_ext`, originally
`BL 0x1000218f8` at `0x1000215e0`, is redirected. The private entry guards NULL
connection and non-NULL callback by tail-branching to the unchanged original
routine. It otherwise executes a relocated copy of `_odconnection_connect`.
The copy adds a capture-free `dispatch_barrier_sync(operations, noop)` immediately
after the original state-queue `dispatch_sync` returns. The original worker,
state completion, signal, status and error handling therefore run before this
fence returns. The semaphore wait/deadline code then executes unchanged.

This fence is outside the state-queue critical section. The operations queue
belongs to the newly allocated connection and is distinct from the caller's
existing module queues; the fresh-create caller cannot already be executing on
that queue. Existing general connection callers and reconnects still call the
original entry. The patch makes no direct writes to connected flags or status.

The **fence has no timeout**. The original five-second deadline remains after
the fence and no longer bounds total fresh callback-free initialization; its
signal should already have completed. An operations queue that never progresses
can therefore hang this initial connection rather than cause the original
five-second failure. The adaptation is not proof of a kernel scheduler repair.

The private code occupies 504 of the original 688 zero bytes at
`0x10007bd50..0x10007c000`, inside the existing RX `__TEXT` mapping. It changes no
file/segment sizes, load commands, chained pointers or other slices in a universal
wrapper. All original PC-relative instructions are relocated; the report records
each reference. The clone preserves frame pointers and original PAC return
instructions, but has **no compact-unwind entry**. Exceptional unwinding through
the clone may therefore be limited. No unwind metadata extension is attempted.

## Signature and mount

`scripts/sign_nabu_od_startup.py` admits only the exact derivative generated
from the original input. It preserves the original XML and DER entitlement
blobs byte for byte and their special-slot hashes, removes the Apple CMS and
Apple signer requirements, marks the CodeDirectory ADHOC, and recomputes all
263 SHA-256 code-page hashes. This is an owner ad-hoc signature and requires its
new CDHash in the paired owner trust cache. It is not an Apple platform identity.

The owner boot shim mounts `/private/var/ngpu/od` over `/usr/libexec` using
`bindfs` with **both `MNT_RDONLY` and `MNT_UNION`**, during APFS mount phase 1
and idempotently phase 2. These phases run inside launchd's early initialization,
before normal OD service launch; they do not precede launchd execution itself.
The signed shim has `com.apple.private.bindfs-allow`. It checks root:wheel
ownership and permissions, verifies that visible OD bytes equal the source,
verifies the original lower `xpcproxy` still resolves to the same device/inode,
and checks mount source/type/flags. This describes the attempted mechanism;
the first device mount was refused with EPERM despite the entitlement being
present in the signed blob. Host mount models do not establish real mount
authorization or success.

The overlay is optional. Missing/invalid candidate input skips activation and
continues boot with an explicit inactive log. A refused mount must leave the
original OD/xpcproxy device/inode and mount source/type/flags unchanged before
boot continues. A failed newly mounted overlay is unmounted and the original
namespace verified before continuing. Rollback failure or unexpected namespace
change remains fatal. Only successful complete validation prints `OD readonly
union ready`. A plain bind mount is never used for this overlay.

## Verification and device acceptance

Run `python3 -m unittest discover -s tests -p test_nabu_od_startup.py -v`.
Tests independently decode all relocated targets with Capstone, verify only the
single original call and RX padding change, preserve universal wrappers, verify
the complete ad-hoc page signature/entitlements, and reject tampering. A Unicorn
replay executes actual original worker/completion/signal machine code using a
deterministic serial queue model. It demonstrates the original delayed-worker
status `0x834` versus the fenced original completion/status zero. PAC and three
LSE flag operations are modeled; it does not verify hardware PAC, real Darwin
admission, APFS mount behavior or real startup latency.

Device acceptance still requires the shim's successful overlay log, no initial
PlistFile assertion, successful directory lookup/login behavior, and observation
that early initialization completes without hanging. A surviving replacement
OD process alone is insufficient evidence.
