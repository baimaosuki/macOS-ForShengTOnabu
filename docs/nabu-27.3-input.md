# NABU macOS input integration

P019 includes Apple's IOUSBHostFamily, AppleUSBXHCI, IOUSBHostHIDDevice and IOHIDFamily. It does not select a Qualcomm USB controller component or start a board touch transport. Starting hidd/usbmuxd is not evidence of hardware enumeration.

The board-owned `package/NabuTouch.cpp` reuses the NT36523 SE18 SPI, clock and firmware protocol from UniXNU-nabu `54b4f98457f8e9e232cc0a077c20d08d0b2f0ac9`. `build-nabu-touch27.py` binds the retained macOS 26C5035r IOHIDDevice: 334 virtual entries, IOService base 272 entries, 0xd0 instance size. The old 21H16 layout (207/168 entries) and its IOHIDEvent edge hook must not be used.

The first desktop integration presents a single-primary absolute mouse through the native HID family, supporting contact press, movement and release. It is not a claim of macOS multitouch gestures. Losing the primary contact releases the mouse; other held contacts cannot steal a drag. A private ADT `/nabu-touch` supplies firmware, partition triples and CRC bank. Missing input stops the worker before MMIO. `nabu-touch=1` starts the late worker. Firmware is never committed into source Git. The known local firmware is NT36523 K82 CSOT, 196608 bytes, SHA256 `d66d15f3dff35292e42e0d301bf53a7fcea6b6fc1b93bca90bb66d765bcfba9b`; the release assembler supplies it from owner inputs.

The QcomDwc3 reference targets SM8550 and iPadOS 24A446; its host mode is explicitly only partially verified upstream. NABU requires SM8150 QUSB2/clock/reset/IRQ/SID, Host VBUS/role ownership and this macOS AppleUSBXHCI ABI. Its existing CDC transport is USB device mode, not a USB keyboard/mouse Host controller. Do not enable the SM8550 hardware sequences on NABU.

Host report encoding and original-class ABI checks qualify software only. Hardware acceptance still requires NT36523 identification/firmware boot, native HID registration and desktop pointer input. USB acceptance requires native xHCI/root-hub/device/HID enumeration and reconnect; neither is inferred from hidd/usbmuxd startup.
