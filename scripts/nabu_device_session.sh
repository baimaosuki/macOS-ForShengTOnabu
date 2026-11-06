#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-2.0-or-later
# nabu macOS 26.5 first-boot session runbook.
#
# Everything here is read-only with respect to the tablet's partitions except the
# ESP copy, which is byte-backed-up before anything is written. The kernel is only
# ever started from RAM with `fastboot boot`; `fastboot flash` is never invoked.
#
#   ./scripts/nabu_device_session.sh check     # slot + device state, changes nothing
#   ./scripts/nabu_device_session.sh backup    # byte backup of /dev/block/by-name/esp
#   ./scripts/nabu_device_session.sh boot      # RAM boot the staged image
#   ./scripts/nabu_device_session.sh collect   # pull whatever the boot left behind
#   ./scripts/nabu_device_session.sh all       # check -> backup -> boot
#
# Add --dry-run to print the exact commands without executing them.
set -euo pipefail

ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
IMAGE=${NABU_IMAGE:-$ROOT/out/nabu/image/nabu-macos26-kernel.img}
BACKUP_DIR=${NABU_BACKUP_DIR:-$ROOT/private/nabu-backup}
DRY_RUN=0
FASTBOOT=${FASTBOOT:-fastboot}
ADB=${ADB:-adb}

say() { printf '%s\n' "$*"; }
die() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }

run() {
  if [[ $DRY_RUN -eq 1 ]]; then
    printf '  [dry-run] %s\n' "$*"
  else
    printf '  $ %s\n' "$*"
    "$@"
  fi
}

need() {
  command -v "$1" >/dev/null 2>&1 || die "missing tool: $1"
}

cmd_check() {
  need "$FASTBOOT"
  [[ -f $IMAGE ]] || die "boot image not found: $IMAGE"
  say "image: $IMAGE ($(stat -c%s "$IMAGE") bytes)"
  say "sha256: $(sha256sum "$IMAGE" | cut -d' ' -f1)"
  say
  say "== fastboot devices =="
  run "$FASTBOOT" devices
  say "== slot state (must stay unbootable:no / retry-count:7) =="
  run "$FASTBOOT" getvar slot-unbootable:b || true
  run "$FASTBOOT" getvar slot-retry-count:b || true
  run "$FASTBOOT" getvar current-slot || true
  say
  say "If the device is not in fastboot yet:"
  say "  adb devices && adb reboot bootloader"
}

cmd_backup() {
  need "$ADB"
  mkdir -p "$BACKUP_DIR"
  local stamp backup
  stamp=$(date +%Y%m%d-%H%M%S)
  backup=$BACKUP_DIR/esp-$stamp.img
  say "== ESP byte backup -> $backup =="
  run "$ADB" shell su -c "'dd if=/dev/block/by-name/esp of=/sdcard/esp-backup.img bs=4M'"
  run "$ADB" pull /sdcard/esp-backup.img "$backup"
  run "$ADB" shell rm -f /sdcard/esp-backup.img
  if [[ $DRY_RUN -eq 0 ]]; then
    sha256sum "$backup" > "$backup.sha256"
    say "  backup sha256: $(cut -d' ' -f1 "$backup.sha256")"
    say "  keep this file: it is the only way back if the ESP ever changes"
  fi
}

cmd_boot() {
  need "$FASTBOOT"
  [[ -f $IMAGE ]] || die "boot image not found: $IMAGE"
  say "== RAM boot (no partition is written) =="
  run "$FASTBOOT" boot "$IMAGE"
  say
  say "Expected on screen, in order:"
  say "  1. DARWINBOOT NABU SM8150 banner, then NRAM PA / KERNEL BYTES / HANDOFF -> ENTRY PA"
  say "  2. XNU verbose output (-v debug=0x14e keepsyms=1 cpus=1)"
  say "  3. either a panic backtrace, or a hang after 'Darwin Kernel Version'"
  say
  say "If nothing appears for 60 s, hold Power+VolDown to return to fastboot."
  say "Nothing on the device was modified; re-run 'check' to confirm slot state."
}

cmd_collect() {
  need "$ADB"
  mkdir -p "$BACKUP_DIR"
  say "== collect post-boot evidence =="
  run "$ADB" devices
  run "$ADB" shell 'ls -l /sdcard' || true
  run "$ADB" shell 'dmesg | tail -200' > "$BACKUP_DIR/dmesg.txt" 2>/dev/null || true
  say "  dmesg saved to $BACKUP_DIR/dmesg.txt (if the device is in Ubuntu)"
  say "  please also send: a photo of the panic screen, and the output of"
  say "    fastboot getvar all 2>&1 | grep -E 'slot-|current-slot'"
}

case ${1:-} in
  check|backup|boot|collect) action=$1; shift ;;
  all) action=all; shift ;;
  *) say "usage: $0 {check|backup|boot|collect|all} [--dry-run]"; exit 2 ;;
esac
for arg in "$@"; do
  case $arg in
    --dry-run) DRY_RUN=1 ;;
    *) die "unknown option: $arg" ;;
  esac
done

case $action in
  check) cmd_check ;;
  backup) cmd_backup ;;
  boot) cmd_boot ;;
  collect) cmd_collect ;;
  all) cmd_check; say; cmd_backup; say; cmd_boot ;;
esac

