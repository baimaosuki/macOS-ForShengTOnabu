# WindowServer exits through its no-client idle timer

Device journal: `/home/baimao/nabu-boot-log-20261112-193111/boot.txt`, SHA-256
`9f5afe108d0a43fea8821ae7381184aa0b2fb34469a24e8c15c6126118b29e32`.
Its installed TOML matches the WS logging package. Both slots validate, with
no counted contention loss or ring overwrite through the selected snapshot.
This does not imply bytes after the snapshot were captured.

## Proven exit cause

PID 100 calls the exit syscall with argument zero at 640648542291 ns
(line 1314). PID 162 repeats the same call stack at 1772872571093 ns
(line 1512). Neither capture records a fork-parent or user exception event.
The observed shared-cache slide is `0x57e4000` in both contexts.

Subtracting this slide maps the syscall PC to `__exit+8` at `0x1804b5584`.
The saved LR `0x1803a760c` is the instruction following the final call in
libsystem_c's `_exit` (C exit), although a nearest-symbol lookup labels this
boundary `___cxa_finalize+0`. Frame 0 similarly lands at `0x186dcedec`, the
instruction following `_idle_timeout`'s final exit call, and must be
attributed using return-address minus four, rather than `_notify_handler`
which begins at that address.

The effective original call chain is:

```
SLXServer -> server_loop -> CGXRunOneServicesPass -> run_timer_pass
          -> idle_timeout -> exit(0) -> _exit syscall
```

Original 25F71 SkyLight instructions in the pinned shared cache establish:

```
0x186dcedbc cmp w8, w9                 # saved/current activity generation
0x186dcedc0 b.ne 0x186dcedcc           # changed: reschedule
0x186dcedc4 bl 0x186f37664             # CGXConnectionsExist
0x186dcedc8 tbz w0, #0, 0x186dcede4    # none: exit
...
0x186dcede4 mov w0, #0
0x186dcede8 bl 0x1871a2630             # indirect-symbol table: _exit (C exit)
```

`schedule_idle_timeout` at `0x186dcecf4` schedules this callback with a
five-second deadline and records the activity generation. These are timer
seconds; the journal's overall elapsed boot time is much longer.

The earlier task-terminate observations at 1112/1113 and 1362/1363 resolve to
`posix_spawn+0x1018` and `proc_exit+0x88`. They occur at image replacement,
before the matching WindowServer runs, so they do not show launchd killing
the later live renderer. Final process retirement still reports wait zero.

## What this changes

The current repeated WindowServer exit is an intentional no-client idle
exit, not the earlier Metal SIGABRT or p3 undefined-instruction event.
Three compositor pages apply and display-on is recorded, but no GOP swap
is recorded. Applying the pages alone proves neither software rendering
success nor that rendering is the reason clients failed to connect.

loginwindow PID 103 execs at 1130, but that does not prove its connection
to WindowServer succeeds. OD's 60-second kauth timeout at 1237 remains a
concrete failure that can prevent login/session clients from becoming ready;
the journal does not prove it is the sole cause. The next implementation
focus is the loginwindow/OD/session connection path, plus first-frame
submission if a client connects. Disabling idle_timeout would only keep a
server alive without establishing that missing connection.

The captured stdout `Didn't find ADM service` at 1368 is another dependency
message. Its text alone does not prove which service or exit branch caused
the failure; the exit stack identifies the idle branch independently.

SHENG reference revision 81f43ebaaa3317a5917b76093f7bc44c4949bc02 and the
locally reviewed macOS profile contain no located idle_timeout fix. Its
Data-seed guide supplies real service-owned template data and local account/
login state, while its display path uses A740/Metal. Neither establishes a
drop-in CPU renderer or an OD fix for this 25F71 trace.

Analysis artifacts in `out/nabu-service-startup/ws-193111/` include exact
frame mappings, symbol boundaries, callback disassembly and exit stub names.
The symbolication tool now counts LF physical lines to match rg/nl despite
the console's bare CR bytes. No device writes or reboot were performed for
this analysis.
