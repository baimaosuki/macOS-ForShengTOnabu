# Mesa c823 Darwin backport

`mesa-c823-darwin.patch` combines the 24 recorded SHENG patches selected by
UniXNU 8542980d and ports them from Mesa 85fda3b to the required c823a83.
It preserves that target revision; it does not select the older compiler pin.
Original patch authorship and licensing remain in the preserved source/patches.

The c823 API uses `fdl6_view` for transfer operations, a renamed
`force_render_mode_reason`, explicit BO/VMA alignment and `ir3_blob_write`.
The port preserves these interfaces. IOVA allocation and cached BO selection
honor the requested alignment. Builtin pack reading matches the new one-byte
serialized boolean. The combined patch reverses to the exact c823 source;
`mesa_patch_identity.py` remains enabled in the staged builder.

The local Darwin arm64 dylib has compiled and linked against SDK 26.5 for
macOS 27 with Clang 21. This is a cross build, not a Vulkan/device test.
The protocol remains the mainline wire v2; the A640 kernel's wire v1 adapter
and hardware qualification are still required before deployment.
