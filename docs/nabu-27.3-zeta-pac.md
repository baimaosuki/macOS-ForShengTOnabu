# NABU macOS 27.3 zeta ordinary-pointer ABI

The source is pinned to UniXNU-zeta `eb489285a1d9153872f00c93ebb519a269b249b5`. Original file hashes and the BSD-2-Clause-Patent license are retained under `devices/nabu/scripts/zeta_pac/`.

Proven code is lowered offline. PAC/AUT/XPAC preserve ordinary unsigned pointers; PACGA returns zero. Authenticated control flow becomes BR/BLR/RET and admitted authenticated loads become LDR/LDUR. Unproven words remain unchanged and use an identically defined kernel exception fallback if executed. No hardware PAC keys or authentication security are provided. Non-PAC SHA3/DIT/SB compatibility remains enabled.

`nabu_zeta_pac.py` selects the arm64e slice of universal macOS files, verifies original code pages and trust-cache membership, then uses the pinned upstream code proof and lowering. It replaces CMS with an ad-hoc SHA-256/4 KiB signature, preserving entitlement/constraint blobs and the original platform identifier. New cdhashes must enter the delivery trust cache.

`rewrite-nabu-zeta-cache.py` verifies an exact input manifest before applying the same lowering. Kernel v3/v5 and partial-page fixups publish plain pointers with ASLR. Verified original corecrypto integrity fields are resealed after code edits. `nabu_zeta_fileset.py` lowers proven retained-kext code after branch relocation and before component FIPS sealing. The previous XOR hotspot patches are removed from the board recipe.

The ordinary-pointer kernel cannot be paired with the old root image. Prelink requires `NABU_ZETA_RUNTIME_REPORT`, declaring the matching ABI, complete conversion and a matching root-image SHA-256. Persistent Data, CPU display and original user logs are retained. Host/CI success does not establish a working desktop on the tablet.

`test_zeta_pac27.py --xnu PREPARED --output OUTPUT` compares actual kernel C with Unicorn execution of upstream replacement words. `test_zeta_macos_sign27.py` covers slice selection, signature rebuilding, entitlement preservation and rejection of tampering. Intel CI runs the ABI equivalence check.
