# NABU 25F71: Objective-C data pointer top-byte compatibility

Device photo: /tmp/codex-clipboard-b403ae41-c3e3-4b78-8f0e-13855ee5c5fb.png.
Kernel UUID 76324BCE-F410-369A-BEBB-BD4C46C87F59; user PC 0x182dd5e1c,
LR 0x182dd5dac, instruction 0x29402354 = ldp w20,w8,[x26]. x26 and FAR
are 0x80080000, VMERR=1, ESR=0x92000006 (EL0 data read, level2 translation).

The matching original SystemOS shared cache has one instruction match at a
16 KiB-aligned slide: unslid PC 0x180091e1c, slide 0x2d44000. LR matches the
BL return at 0x180091dac as well. libobjc symbol table identifies the function
as realizeClassWithoutSwift (start 0x180091624), and its helper 0x1800afb14 as
class_rw_t::ro(). Real instructions loaded a field, authenticated with AUTDA,
stripped with XPACD, cleared low tag1, then read the relative-list header.
The field contained 0x80080001, producing the fault address 0x80080000.

The no-PAuth compatibility routine previously cleared all bits63:48 during
every AUT/XPAC. This destroys semantic top-byte data flags. Apple objc4's
class_data_bits_t stores FAST_IS_RW_POINTER=0x8000000000000000, and the real
cache safe_ro helper tests bit63 before choosing the RW path. Losing that
flag causes class_rw_t to be interpreted as class_ro_t. The RW flag word at
offset0x20 is then interpreted as the RO baseMethods pointer.

Correction: EL0 data AUTDA/AUTDB/AUTDZA/AUTDZB/XPACD preserves bits63:56,
clears the PAC field bits55:48, and retains the low pointer bits. Instruction
AUT/XPACI, branch destinations and LDRAA/LDRAB effective addresses keep their
existing address normalization. PAC preserves its original source operand.
No extra memory is mapped at 0x80080000 and no arbitrary register is repaired.

Eight compiled-source tests pass, including all256 top-byte values across
five data opcodes, the real Objective-C marker case, instruction opcode
separation, branch/load behavior and actual exception-adapter state checks.
The full100-patch isolated source prepare and both source audits pass.
The original 25F71 trust bundle and paired2GiB root-match ADT stay in the
resulting package. Successful exec and desktop boot require device evidence.
