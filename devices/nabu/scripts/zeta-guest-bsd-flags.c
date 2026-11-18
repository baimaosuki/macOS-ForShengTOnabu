/* SPDX-License-Identifier: BSD-2-Clause-Patent
 * Native BSD flags round-trip for the isolated APFS image-builder guest. */
#include <errno.h>
#include <fcntl.h>
#include <inttypes.h>
#include <stdio.h>
#include <stdlib.h>
#include <sys/ioctl.h>
#include <unistd.h>
#define NABU_SET_BSD_FLAGS _IOW('N', 0xe1, uint32_t)
#define NABU_GET_BSD_FLAGS _IOR('N', 0xe2, uint32_t)
int main(int argc, char **argv) {
    if (argc != 3) return 2;
    char *end = 0;
    errno = 0;
    unsigned long input = strtoul(argv[2], &end, 0);
    if (errno || !end || *end || input > UINT32_MAX || (input & 0x20)) return 2;
    uint32_t wanted = (uint32_t)input, got = 0;
    int fd = open(argv[1], O_RDONLY | O_CLOEXEC);
    if (fd < 0) { perror(argv[1]); return 1; }
    if (ioctl(fd, NABU_SET_BSD_FLAGS, &wanted) || ioctl(fd, NABU_GET_BSD_FLAGS, &got)) {
        perror("APFS native BSD flags"); close(fd); return 1;
    }
    close(fd);
    if (got != wanted) return 1;
    printf("%08" PRIx32 "\n", got);
    return 0;
}
