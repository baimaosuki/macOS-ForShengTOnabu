# NABU macOS input integration

P019 includes Apple's IOUSBHostFamily, AppleUSBXHCI, IOUSBHostHIDDevice and IOHIDFamily. It does not select a Qualcomm USB controller component or start a board touch transport. Starting hidd/usbmuxd is not evidence of hardware enumeration.

The board-owned `package/NabuTouch.cpp` reuses the NT36523 SE18 SPI, clock and firmware protocol from UniXNU-nabu `54b4f98457f8e9e232cc0a077c20d08d0b2f0ac9`. `build-nabu-touch27.py` binds the retained macOS 26C5035r IOHIDDevice: 334 virtual entries, IOService base 272 entries, 0xd0 instance size. The old 21H16 layout (207/168 entries) and its IOHIDEvent edge hook must not be used.

The first desktop integration presents a single-primary absolute mouse through the native HID family, supporting contact press, movement and release. It is not a claim of macOS multitouch gestures. Losing the primary contact releases the mouse; other held contacts cannot steal a drag. A private ADT `/nabu-touch` supplies firmware, partition triples and CRC bank. Missing input stops the worker before MMIO. `nabu-touch=1` starts the late worker. Firmware is never committed into source Git. The known local firmware is NT36523 K82 CSOT, 196608 bytes, SHA256 `d66d15f3dff35292e42e0d301bf53a7fcea6b6fc1b93bca90bb66d765bcfba9b`; the release assembler supplies it from owner inputs.

The QcomDwc3 reference targets SM8550 and iPadOS 24A446; its host mode is explicitly only partially verified upstream. NABU requires SM8150 Synopsys FEMTO 7nm HS PHY/clock/reset/IRQ/SID, Host VBUS/role ownership and this macOS AppleUSBXHCI ABI. Its existing CDC transport is USB device mode, not a USB keyboard/mouse Host controller. Do not enable the SM8550 hardware sequences on NABU.

Host report encoding and original-class ABI checks qualify software only. Hardware acceptance still requires NT36523 identification/firmware boot, native HID registration and desktop pointer input. USB acceptance requires native xHCI/root-hub/device/HID enumeration and reconnect; neither is inferred from hidd/usbmuxd startup.

The vendor NABU DTS (`uefi-sheng/Resources/DTBs/nabu.dts`) sets USB0 to high-speed, core 0x0a600000, PHY 0x088e2000 (`qcom,usb-hsphy-snps-femto`), SPI 133 (GIC ID 165) and SMMU stream 0x140. Start with USB2 Host; SM8550 eUSB2 repeater/retimer sequences are not applicable. VBUS follows the board PMIC/Type-C path, not a hardcoded online flag.

## P020 local source integration (2026-11-19)

`build-nabu-display27.py` now invokes `build-nabu-input27.py`, which compiles
NT36523, PM8150 power and USB2 Host into the production late archive. The board
args select all three. `LegacyDisplayProbe.cpp` calls `nabu_input_start`; the
workers are independent. Family/metaclass registration is serialized. This is
source/build integration, not a claim that a new bootable kernel has been built.
No CI, packaging or hardware writes were performed for this integration.

USB hardware: GCC USB30_PRIM GDSCR +0xf004/BCR +0xf000, RCGs +0xf01c/+0xf034,
branches +0xf078/+0xf010/+0xf07c/+0xf014/+0xf018, HS PHY reset +0x12000.
`NabuUsb2Core.h` implements clock readbacks/timeouts, FEMTO programming and
QSCRATCH +8 PIPE-to-UTMI selection, DWC3 Host mode, 8-bit UTMI and HCRST.
`NabuUsbHost.cpp::Hardware::rails` uses the stock command-DB pointer at
0x0c3f000c, apps RSC at 0x18220000/TCS offset0xd00, and completed RPMh votes
for ldoa5/ldoa12/ldoa2 and xo.lvl. No completion is inferred from a fixed sleep.
PHY tuning uses stock overrides (0x70=4,0x74=0x2c), RCAL mask0x1e00000 and
external-resistor selection when unprogrammed. The USB3 clock/PHY is not enabled.

USB DMA is **coherent stage-1 identity**, not the UFS bypass bank: AppleUSBXHCI
performs no explicit cache maintenance. `NabuUsbDma.h` selects a free bank while
preserving every other SID. `Hardware::dma` builds Normal WB inner-shareable
identity pages for DRAM 2..8GiB, cleans the page table, synchronizes the TLB,
and binds only SID0x140. Controller IRQ is SPI133/GIC165, level. Native
AppleUSBXHCI has 427 slots and instance size0xf88 in 26C5035r; the owned
power-state and split64 MMIO hooks are slots292/419/421. No 24A446/iOS table
layout is substituted.

`NabuSpmiV5.h` uses v5 APID map core+0x900, owner **cnfg+0x700**, observer
stride0x80/APID (EE0), write stride0x10000/APID. It bounds transactions and
propagates owner-denied, transaction fault, timeout and failed readback.
PM8150B SID2 controls VBUS via0x1140 bit0 and0x1153 bit1; DRP is0x1544 low3bits.
CC0x150b bit0 attached/bit6 DFP selects sourcing. USBIN0x1310 bit4 detects
external power. Own VBUS sense does not oscillate enable/disable. Disconnect
and transaction failure remove an owned vote. Logs read actual CC, USBIN,
OTG command and RT status. Board electrical/5V acceptance still needs hardware.

Touch is GENI SE18 0x00c84000 (SPI583/GIC615, polled FIFO; no DMA), GCC
wrap2 votes0x52014 bits5/2/1, GPIO23/24/25/26 QUP18 function2/3/3/2,6mA no pull.
GPIO39 north tile0x03927000 is input/pulldown16mA, rising raw-status latch0x16,
polled every8ms with periodic SPI recovery reads. No physical reset GPIO exists
in stock DTS: reset0x3f0fe and fast-read0x3f310 use real SPI. FIFO depth/width
are read from hardware; failed clocks, reset, CRC configuration, transfer or
firmware boot stop publication. Lost transport releases a held pointer.
`NabuTouchHid -> IOHIDInterface -> IOHIDEventDriver` carries report1 left button
and absolute X/Y0..32767. Portrait1600x2560 defaults have no swap/inversion;
physical orientation must still be confirmed on device.

Power is PM8150 SID0/PON0x0800 subtype4, RT_STS0x0810 bit0, polled10ms,
software debounce30ms, long threshold2000ms. `NabuPowerHid` supplies Consumer
Control report2/Power usage0x0c:0x30 to IOHIDEventDriver. Press/release are
reported once; short/long are distinguished without altering PMIC hardware
reset timers. Bus loss releases the HID key. No forced software reboot action.

Production DTS exports arm-io usb-host0/usb-hsphy/spmi plus root nabu-input
(low-address GCC/SPI resources cannot enter arm-io's nonzero range) and
nabu-touch. `NABU_TOUCH_FIRMWARE_DIR` is required when generating production
ADT; missing or unqualified private CSOT firmware fails generation. No firmware
is embedded in Git. Root/chosen persistent_data remains enabled.

`check-input-local27.py` uses real Apple MacOSX26.5 SDK headers plus the prepared
XNU headers, validates 26C5035r kernelcache slots, compiles all five translation
units with warnings as errors, runs the production archive builder, and links
against actual current native-kernel symbols plus original family addresses.
A common address rebase is used only because Linux LLD rejects high unsigned
Mach-O layout options; relative relocation distances stay unchanged. This
closure executable is not bootable. The SDK version differs from the target;
full macOS27.3 kernel build and physical USB/HID operation remain separate
acceptance steps. The owner approved CI and SDK26.5 on 2026-11-19.

Release preflight reserves UFS SMR slot2 and CB1 even when their valid bits
look clear. USB programs only its selected slot/CB, snapshots the UFS registers
and verifies they are unchanged. It never resets the whole SMMU. All USB
VBUS and power-button SPMIv5 transactions, including read-modify-write and
readback, hold the single shared PmicBus IOLock. SPI fault logs are limited;
eight consecutive event-read failures release a held pointer and stop the
worker. Firmware watchdog reloads are limited to three. CI explicitly selects
Xcode26.6 and checks iPhoneOS/macOS SDK26.5 before the native build. Target
family ABI remains the original macOS27.3 build26C5035r.


P021 hardware log confirms native PM8150 HID matched IOHIDEventDriver,
press/release reports returned success, and short/long detection ran. USB
RPMh votes completed; controller stopped at clocks before PHY/HCRST/SMMU.
Correct the DWC3-QCOM sequence to assert then release BCR before clocks.
Qualcomm branch2 ready accepts either CLK_OFF clear or NOC_FSM_STATUS=2.
Touch wrap2 slave-AHB has HWCG bit1; captured status0x80000002 is idle
hardware gating, so Linux skips its halt poll while HWCG is enabled.
P021 already fixes Apple interrupt handles. WindowServer SIGSEGV exited
without a fault PC because prior capture only selected SIGABRT; select
SEGV/BUS/ILL as well without changing process signal/restart behavior.
No claim of USB enumeration or touch SPI success before new hardware logs.

P025 hardware evidence confirms the NT36523 firmware download and native
IOHIDEventDriver open. Its duplicate-coordinate traffic exposed a polling bug:
unchanged valid packets did not refresh the transport timestamp, so a stationary
press could be released after 250 ms. The loop now validates every read, refreshes
the timestamp for valid stationary packets and reports only pointer transitions.
The non-pen event read is the stock 65-byte payload (64 data plus checksum).
Bad checksums remain rejected; a truly stale transport releases the button and
the next valid contact can acquire it again. Host tests execute the actual loop
with scripted IRQ/time/SPI peers and reproduce the prior failure.

The same log reaches USB HCRST success and then reports no-free-context-bank.
The USB planner previously required all candidate CB registers to be zero.
It now also accepts an unreferenced, disabled AArch64 S1 bank with the known
firmware CBAR=0x1f000/CBA2R=1 form. Enabled banks, banks referenced by other
streams, stage-2 banks, reserved UFS CB1/slot2 and unknown configurations remain
excluded. The candidate is rechecked immediately before programming. Host tests
cover these states; P025 does not contain every candidate CB value, so actual
USB enumeration and the selected candidate still require a new device log.


P026 still stops USB after successful HCRST at no-free-context-bank. The planner
now tests actual ownership of S1-capable banks: SCTLR.M must be clear and no valid
stream may reference the bank. Requiring reset values or one UFS CBAR/CBA2R
pattern incorrectly rejected other disabled contexts. Context registers can
retain firmware state or UNKNOWN reset bits; they are completely reprogrammed
while the controller is halted. CB0/CB1, UFS slot2, stage-2-only banks, live routes
and enabled banks remain excluded. SCTLR disable is explicitly written and read
back before installing the table. The candidate is still rechecked before writes;
there is no global SMMU reset. Actual candidate selection and native USB/HID
enumeration remain unverified on hardware. P026 touch data includes valid HID
reports but many idle checksum drops; no blanket acceptance of invalid packets
has been introduced.
