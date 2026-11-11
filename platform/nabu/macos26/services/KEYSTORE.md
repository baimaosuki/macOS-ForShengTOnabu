# NABU software AppleKeyStore

`NabuKeyStoreComponent.cpp` is an exact source snapshot of AppleKeyStoreState
revision `8c70daf814aafdf4a3d35e40dde0322655fd0a83`, original filename
`NativeKeyStoreEvents.cpp`, SHA-256
`9a25c253658195c1dfe2c9fa0aa4481929ec84a0d96c39c7f1dde9f3ac55460a`.
It retains the original AppleKeyStore and AppleKeyStoreUserClient runtime classes
and exported symbols. This is software wrapping without SEP or hardware data
protection. Keybag, passcode and reference-key operations remain unsupported.

Compile both C++ files into the existing UFS companion. Use the ordinary OSKext
C++ constructor lifecycle. After the module is fully loaded and outside its
load lock, call `nabu_keystore_start(IOService::getResourceService())` during
early service startup. Do not invoke global constructors manually and do not
also load a second AppleKeyStore provider.

The package must persist one board-owned UUID and put its 16 raw bytes in
`/options/platform-uuid` in the ADT. Reuse that identity across package rebuilds;
it is public software key material, not a protected device secret. Missing or
zero identity leaves state queries available but fails wrapping and bag UUID.
This does not introduce persistent Data, a keybag or hardware encryption.

The macOS 26.5 / 25F71 AppleKeyStore framework in the original dyld cache was
disassembled locally, independently of the iPad ABI. The selectors observed
are lock state 7, wrap 10, unwrap 11, system bag 14, device state 17, bag UUID
23, extended device state 35, generation 40, cache flow 160. Wrap passes two
scalar inputs, one scalar output, structured key input and structured blob
output. Unwrap passes two scalar inputs and structured input/output. Generation
passes two scalar inputs and one scalar output; bag UUID requests 16 bytes.
Evidence and the inspection script are in
`out/nabu-service-startup/aks-abi/`. This is static ABI evidence, not a device
boot or security validation. Compilation is performed by the parent integration.
