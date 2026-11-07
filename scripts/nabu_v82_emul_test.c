/*
 * Host conformance test for osfmk/arm64/nabu_v82_emul.c.
 *
 * core.c is extracted verbatim from the patched kernel source (the region
 * between the BEGIN/END NABU_V82_CORE markers), so the code under test is
 * the code that ships.  Every vector carries a real instruction word, so the
 * decoder (masks, field positions) is exercised together with the
 * semantics.
 */
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "nabu_v82_emul_core.c"

#define MAX_VECTORS 8192

struct vector {
	char            op[24];
	uint32_t        insn;
	nabu_v82_vec_t  vd, vn, vm, va;
	nabu_v82_vec_t  expected;
};

static void
print_vec(const char *label, nabu_v82_vec_t v)
{
	printf("%s=%016llx:%016llx", label,
	    (unsigned long long)v.hi, (unsigned long long)v.lo);
}

/*
 * Pointer-authentication decoder check.  The instruction words are the ones
 * llvm-mc 18 emits for -triple=arm64e, and the expected result is the
 * identity mapping that a core without FEAT_PAuth must produce.
 */
static unsigned int
pac_selfcheck(void)
{
	static const struct {
		uint32_t    insn;
		const char *name;
		int         kind;               /* 0 = Xd = Xn, 1 = Xd = Xd, 2 = PACGA */
	} cases[] = {
		{ 0xdac100a3, "pacia  x3, x5", 0 },
		{ 0xdac104a3, "pacib  x3, x5", 0 },
		{ 0xdac108a3, "pacda  x3, x5", 0 },
		{ 0xdac10ca3, "pacdb  x3, x5", 0 },
		{ 0xdac110a3, "autia  x3, x5", 0 },
		{ 0xdac114a3, "autib  x3, x5", 0 },
		{ 0xdac118a3, "autda  x3, x5", 0 },
		{ 0xdac11ca3, "autdb  x3, x5", 0 },
		{ 0xdac123e3, "paciza x3", 1 },
		{ 0xdac127e3, "pacizb x3", 1 },
		{ 0xdac12be3, "pacdza x3", 1 },
		{ 0xdac12fe3, "pacdzb x3", 1 },
		{ 0xdac133e3, "autiza x3", 1 },
		{ 0xdac137e3, "autizb x3", 1 },
		{ 0xdac13be3, "autdza x3", 1 },
		{ 0xdac13fe3, "autdzb x3", 1 },
		{ 0xdac143e3, "xpaci  x3", 1 },
		{ 0xdac147e3, "xpacd  x3", 1 },
		{ 0x9ac730a3, "pacga  x3, x5, x7", 2 },
	};
	const unsigned int total = (unsigned int)(sizeof(cases) / sizeof(cases[0])) + 2U;
	uint64_t gpr[32];
	unsigned int i;
	unsigned int failures = 0;

	for (i = 0; i < 32; i++) {
		gpr[i] = 0x1111000000000000ULL + i;
	}
	for (i = 0; i < sizeof(cases) / sizeof(cases[0]); i++) {
		uint64_t expected;
		uint64_t result = 0;
		unsigned int dst = 0xffffffffU;
		bool handled;

		switch (cases[i].kind) {
		case 0:
			expected = gpr[5];
			break;
		case 1:
			expected = gpr[3];
			break;
		default:
			expected = gpr[5] ^ gpr[7];
			break;
		}
		handled = nabu_v82_pac_core(cases[i].insn, gpr, &dst, &result);
		if (!handled || dst != 3 || result != expected) {
			printf("FAIL pac %-18s handled=%d dst=%u\n", cases[i].name,
			    (int)handled, dst);
			failures++;
		}
	}
	/* UDIV/SDIV/LSLV and friends share 0x9AC0xxxx and must stay untouched. */
	{
		uint64_t result = 0;
		unsigned int dst = 0;

		if (nabu_v82_pac_core(0x9ac00862U /* udiv x2, x3, x0 */, gpr, &dst,
		    &result)) {
			printf("FAIL pac udiv misclassified as PAC\n");
			failures++;
		}
	}
	/* eor3 must not be claimed by the PAC decoder either. */
	{
		uint64_t result = 0;
		unsigned int dst = 0;

		if (nabu_v82_pac_core(0xce020c20U, gpr, &dst, &result)) {
			printf("FAIL pac eor3 misclassified as PAC\n");
			failures++;
		}
	}
	printf("pac decoder %u/%u passed\n", total - failures, total);
	return failures;
}

int
main(int argc, char **argv)
{
	static struct vector vectors[MAX_VECTORS];
	const char *path = (argc > 1) ? argv[1] : "vectors.txt";
	char op_names[64][32];
	unsigned int op_pass[64];
	unsigned int op_count[64];
	unsigned int op_seen = 0;
	unsigned int total = 0;
	unsigned int passed = 0;
	unsigned int pac_failures;
	char line[512];
	FILE *fh;
	unsigned int i;

	fh = fopen(path, "r");
	if (fh == NULL) {
		perror(path);
		return 1;
	}
	while (fgets(line, sizeof(line), fh) != NULL) {
		unsigned long long vals[10];
		struct vector *v;
		char *tok;
		int n = 0;

		if (line[0] == '#' || line[0] == '\n') {
			continue;
		}
		if (total >= MAX_VECTORS) {
			fprintf(stderr, "too many vectors\n");
			return 1;
		}
		v = &vectors[total];
		tok = strtok(line, " \t\n");
		if (tok == NULL) {
			continue;
		}
		{
			size_t k = 0;

			while (tok[k] != '\0' && k + 1 < sizeof(v->op)) {
				v->op[k] = tok[k];
				k++;
			}
			v->op[k] = '\0';
		}
		tok = strtok(NULL, " \t\n");
		if (tok == NULL) {
			fprintf(stderr, "truncated vector line\n");
			return 1;
		}
		v->insn = (uint32_t)strtoul(tok, NULL, 16);
		while (n < 10 && (tok = strtok(NULL, " \t\n")) != NULL) {
			vals[n++] = strtoull(tok, NULL, 16);
		}
		if (n != 10) {
			fprintf(stderr, "bad vector line for %s\n", v->op);
			return 1;
		}
		v->vd.lo = vals[0];
		v->vd.hi = vals[1];
		v->vn.lo = vals[2];
		v->vn.hi = vals[3];
		v->vm.lo = vals[4];
		v->vm.hi = vals[5];
		v->va.lo = vals[6];
		v->va.hi = vals[7];
		v->expected.lo = vals[8];
		v->expected.hi = vals[9];
		total++;
	}
	fclose(fh);

	for (i = 0; i < total; i++) {
		struct vector *v = &vectors[i];
		nabu_v82_vec_t regs[32];
		nabu_v82_op_t op;
		nabu_v82_vec_t result = { 0, 0 };
		nabu_v82_status_t status;
		unsigned int dst = 0;
		unsigned int slot = 0;
		unsigned int j;
		bool ok;

		for (j = 0; j < 32; j++) {
			regs[j].lo = 0;
			regs[j].hi = 0;
		}
		/*
		 * Operands first, destination last.  Only eor3/bcax take a third
		 * source register: for every other encoding the "Ra" bit field is
		 * part of the opcode or of the immediate, so it must not be
		 * treated as an operand here.
		 */
		if (strcmp(v->op, "eor3") == 0 || strcmp(v->op, "bcax") == 0) {
			regs[NABU_V82_RA(v->insn)] = v->va;
		}
		regs[NABU_V82_RN(v->insn)] = v->vn;
		if (strcmp(v->op, "sha512su0") != 0) {
			regs[NABU_V82_RM(v->insn)] = v->vm;
		}
		regs[NABU_V82_RD(v->insn)] = v->vd;

		op.insn = v->insn;
		op.vd = regs[NABU_V82_RD(v->insn)];
		op.vn = regs[NABU_V82_RN(v->insn)];
		op.vm = regs[NABU_V82_RM(v->insn)];
		op.va = regs[NABU_V82_RA(v->insn)];

		status = nabu_v82_core(&op, &dst, &result);
		ok = (status == NABU_V82_EMULATED) &&
		    (dst == NABU_V82_RD(v->insn)) &&
		    (result.lo == v->expected.lo) &&
		    (result.hi == v->expected.hi);

		/* The diagnostic decoder must name the instruction correctly. */
		{
			char want[24];
			const char *mnemonic = nabu_v82_mnemonic(v->insn);
			size_t k = 0;

			while (v->op[k] != '\0' && v->op[k] != '-' &&
			    k + 1 < sizeof(want)) {
				want[k] = v->op[k];
				k++;
			}
			want[k] = '\0';
			if (mnemonic == NULL || strcmp(want, mnemonic) != 0) {
				printf("FAIL mnemonic %s insn=%08x got=%s want=%s\n",
				    v->op, v->insn, mnemonic ? mnemonic : "(null)", want);
				ok = false;
			}
		}

		for (j = 0; j < op_seen; j++) {
			if (strcmp(op_names[j], v->op) == 0) {
				slot = j;
				break;
			}
		}
		if (j == op_seen) {
			size_t n = 0;

			while (v->op[n] != '\0' && n + 1 < sizeof(op_names[0])) {
				op_names[op_seen][n] = v->op[n];
				n++;
			}
			op_names[op_seen][n] = '\0';
			op_pass[op_seen] = 0;
			op_count[op_seen] = 0;
			slot = op_seen++;
		}
		op_count[slot]++;
		if (ok) {
			op_pass[slot]++;
			passed++;
		} else {
			printf("FAIL %s insn=%08x ", v->op, v->insn);
			print_vec(" got", result);
			print_vec(" want", v->expected);
			printf(" status=%d\n", (int)status);
		}
	}

	printf("\n-- per-op results (%u vectors) --\n", total);
	for (i = 0; i < op_seen; i++) {
		printf("%-16s %3u/%3u passed%s\n", op_names[i], op_pass[i],
		    op_count[i], (op_pass[i] == op_count[i]) ? "" : "  <-- FAILURE");
	}
	printf("\nTOTAL %u/%u instruction vectors passed\n", passed, total);
	pac_failures = pac_selfcheck();
	return (passed == total && pac_failures == 0) ? 0 : 1;
}
