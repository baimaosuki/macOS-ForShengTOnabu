/* SPDX-License-Identifier: MIT
 * Raw diagnostics in the existing signed owner boot shim.
 * No target suspension, symbolication, memory writes, or fabricated frames.
 */
#ifndef NABU_RAW_STACKS_H
#define NABU_RAW_STACKS_H
#include <errno.h>
#include <inttypes.h>
#include <limits.h>
#include <signal.h>
#include <stdarg.h>
#include <stddef.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <unistd.h>
#ifndef NABU_RAW_STACKS_HOST_TEST
#include <mach/mach.h>
#include <mach/mach_vm.h>
#include <mach/arm/thread_status.h>
#include <mach-o/dyld_images.h>
#include <mach-o/loader.h>
#include <sys/sysctl.h>
#include <sys/stat.h>
#include <fcntl.h>
#include <sys/wait.h>
/* Exported by the original libsystem_kernel and used by original sample.
 * Public SDKs do not all expose the BSD read-port declaration. This exact
 * prototype is the matching XNU bsd/sys/proc_private.h declaration. */
extern int task_read_for_pid(unsigned int, int, unsigned int *);
#endif

#define NABU_RAW_MAX_FRAMES 32u
#define NABU_RAW_STACK_WINDOW (UINT64_C(8) * 1024 * 1024)
#define NABU_RAW_USER_LIMIT (UINT64_C(1) << 39)
static long nabu_raw_pid;
static unsigned nabu_raw_round;
static uint64_t nabu_raw_timestamp_ns;
static int nabu_raw_output_fd = 2;
static int nabu_raw_console_fd = -1;
static unsigned nabu_raw_io_errors;

/* Save the entire record before mirroring it to the ESP-captured console.
 * A mirror failure never discards the persistent copy. */
static int
nabu_raw_emit(const void *record, size_t length)
{
    const char *bytes = record;
    size_t at = 0;
    while (at < length) {
        ssize_t written = write(nabu_raw_output_fd, bytes + at, length - at);
        if (written < 0 && errno == EINTR) continue;
        if (written <= 0) { ++nabu_raw_io_errors; return -1; }
        at += (size_t)written;
    }
    if (nabu_raw_console_fd >= 0 && nabu_raw_console_fd != nabu_raw_output_fd &&
        write(nabu_raw_console_fd, record, length) != (ssize_t)length)
        ++nabu_raw_io_errors;
    return (int)length;
}

/* One complete, prefixed fd2 write per record. The kernel's opt-in bounded
 * capture sees the bytes even when the console device stops displaying them.
 * fd2 is also redirected to a retained diagnostic file by the probe script. */
static int
nabu_raw_printf(const char *format, ...)
{
    char line[512];
    va_list arguments;
    va_start(arguments, format);
    static const char prefix[] = "NABU-RAWSTACK ";
    int length = snprintf(line, sizeof(line),
        "NABU-RAWSTACK target_pid=%ld round=%u t_ns=%" PRIu64 " clock=mach-uptime ",
        nabu_raw_pid, nabu_raw_round, nabu_raw_timestamp_ns);
    if (!strncmp(format, prefix, sizeof(prefix) - 1))
        format += sizeof(prefix) - 1;
    int body = vsnprintf(line + length, sizeof(line) - (size_t)length, format, arguments);
    if (body >= 0) length += body;
    va_end(arguments);
    if (body < 0 || length <= 0 || (size_t)length >= sizeof(line)) {
        static const char overflow[] = "NABU-RAWSTACK error=line-too-long truncated=1\n";
        return nabu_raw_emit(overflow, sizeof(overflow) - 1);
    }
    /* The disk sink handles short writes; console gets one prefixed record. */
    return nabu_raw_emit(line, (size_t)length);
}
#define printf nabu_raw_printf

static int
nabu_raw_read(task_t task, uint64_t address, void *destination, size_t length)
{
    mach_vm_size_t actual = 0;
    if (!length || !address || address >= NABU_RAW_USER_LIMIT ||
        length > NABU_RAW_USER_LIMIT - address) {
        printf("NABU-RAWSTACK read address=0x%" PRIx64 " bytes=%zu invalid-range\n", address, length);
        return 0;
    }
    kern_return_t kr = mach_vm_read_overwrite(task, address, length,
        (mach_vm_address_t)(uintptr_t)destination, &actual);
    if (kr != KERN_SUCCESS || actual != length) {
        printf("NABU-RAWSTACK read address=0x%" PRIx64 " requested=%zu actual=%" PRIu64 " kr=%d\n",
            address, length, (uint64_t)actual, kr);
        return 0;
    }
    return 1;
}

static void
nabu_raw_walk(task_t task, uint64_t tid, uint64_t sp, uint64_t fp)
{
    for (unsigned depth = 0; depth < NABU_RAW_MAX_FRAMES; ++depth) {
        uint64_t frame[2];
        if (!fp) {
            printf("NABU-RAWSTACK stop tid=%" PRIu64 " depth=%u reason=end\n", tid, depth);
            return;
        }
        if ((fp & 15) || fp >= NABU_RAW_USER_LIMIT || fp < sp ||
            fp - sp > NABU_RAW_STACK_WINDOW ||
            NABU_RAW_USER_LIMIT - fp < sizeof(frame)) {
            printf("NABU-RAWSTACK stop tid=%" PRIu64 " depth=%u fp=0x%" PRIx64 " reason=invalid-fp truncated=1\n", tid, depth, fp);
            return;
        }
        if (!nabu_raw_read(task, fp, frame, sizeof(frame))) {
            printf("NABU-RAWSTACK stop tid=%" PRIu64 " depth=%u reason=read-failed truncated=1\n", tid, depth);
            return;
        }
        /* Keep authenticated/tagged return addresses verbatim for offline
         * interpretation; do not guess a PAC mask or invent symbol names. */
        printf("NABU-RAWSTACK frame tid=%" PRIu64 " depth=%u fp=0x%" PRIx64
               " next_fp=0x%" PRIx64 " return_raw=0x%" PRIx64 "\n",
               tid, depth, fp, frame[0], frame[1]);
        if (frame[0] && frame[0] <= fp) {
            printf("NABU-RAWSTACK stop tid=%" PRIu64 " depth=%u reason=nonascending-fp truncated=1\n", tid, depth);
            return;
        }
        fp = frame[0];
    }
    printf("NABU-RAWSTACK stop tid=%" PRIu64 " reason=frame-limit truncated=1\n", tid);
}

static void
nabu_raw_uuid(const unsigned char *uuid, char text[33])
{
    static const char hex[] = "0123456789abcdef";
    for (unsigned i = 0; i < 16; ++i) {
        text[2 * i] = hex[uuid[i] >> 4];
        text[2 * i + 1] = hex[uuid[i] & 15];
    }
    text[32] = 0;
}

static void
nabu_raw_images(task_t task)
{
    struct task_dyld_info info;
    mach_msg_type_number_t count = TASK_DYLD_INFO_COUNT;
    memset(&info, 0, sizeof(info));
    kern_return_t kr = task_info(task, TASK_DYLD_INFO, (task_info_t)&info, &count);
    printf("NABU-RAWSTACK dyld kr=%d count=%u address=0x%" PRIx64 " size=%" PRIu64 " format=%d\n",
        kr, count, (uint64_t)info.all_image_info_addr,
        (uint64_t)info.all_image_info_size, info.all_image_info_format);
    if (kr != KERN_SUCCESS || count < TASK_DYLD_INFO_COUNT ||
        info.all_image_info_format != TASK_DYLD_ALL_IMAGE_INFO_64)
        return;
    struct dyld_all_image_infos images;
    const size_t prefix = offsetof(struct dyld_all_image_infos, sharedCacheBaseAddress) + sizeof(uintptr_t);
    memset(&images, 0, sizeof(images));
    if (info.all_image_info_size < prefix ||
        !nabu_raw_read(task, info.all_image_info_addr, &images, prefix))
        return;
    printf("NABU-RAWSTACK images version=%u count=%u array=0x%" PRIxPTR " dyld=0x%" PRIxPTR "\n",
        images.version, images.infoArrayCount, (uintptr_t)images.infoArray,
        (uintptr_t)images.dyldImageLoadAddress);
    if (images.version >= 15) {
        char uuid[33];
        nabu_raw_uuid(images.sharedCacheUUID, uuid);
        printf("NABU-RAWSTACK cache slide=0x%" PRIxPTR " base=0x%" PRIxPTR " uuid=%s\n",
            images.sharedCacheSlide, images.sharedCacheBaseAddress, uuid);
    }
    /* Read only bounded load commands for at most 16 actual images, after
     * all raw thread output. No filesystem paths are opened for symbols. */
    unsigned limit = images.infoArrayCount < 16 ? images.infoArrayCount : 16;
    for (unsigned i = 0; i < limit; ++i) {
        struct dyld_image_info entry;
        uint64_t array = (uint64_t)(uintptr_t)images.infoArray;
        if (!array || array > UINT64_MAX - i * sizeof(entry) ||
            !nabu_raw_read(task, array + i * sizeof(entry), &entry, sizeof(entry)))
            break;
        uint64_t load = (uint64_t)(uintptr_t)entry.imageLoadAddress;
        struct mach_header_64 header;
        printf("NABU-RAWSTACK image index=%u load=0x%" PRIx64 "\n", i, load);
        if (!nabu_raw_read(task, load, &header, sizeof(header)) ||
            header.magic != MH_MAGIC_64 || header.cputype != CPU_TYPE_ARM64 ||
            header.sizeofcmds > 32768 || header.ncmds > 1024)
            continue;
        uint32_t offset = 0;
        for (uint32_t j = 0; j < header.ncmds; ++j) {
            struct load_command command;
            if (offset > header.sizeofcmds || header.sizeofcmds - offset < sizeof(command) ||
                load > UINT64_MAX - sizeof(header) - offset)
                break;
            uint64_t address = load + sizeof(header) + offset;
            if (!nabu_raw_read(task, address, &command, sizeof(command)) ||
                command.cmdsize < sizeof(command) || (command.cmdsize & 7) ||
                command.cmdsize > header.sizeofcmds - offset)
                break;
            if (command.cmd == LC_UUID && command.cmdsize >= sizeof(struct uuid_command)) {
                struct uuid_command uuid;
                if (nabu_raw_read(task, address, &uuid, sizeof(uuid))) {
                    char text[33];
                    nabu_raw_uuid(uuid.uuid, text);
                    printf("NABU-RAWSTACK image-uuid index=%u uuid=%s\n", i, text);
                }
            } else if (command.cmd == LC_SEGMENT_64 && command.cmdsize >= sizeof(struct segment_command_64)) {
                struct segment_command_64 segment;
                if (nabu_raw_read(task, address, &segment, sizeof(segment)) &&
                    !memcmp(segment.segname, "__TEXT\0", 7) && segment.vmsize) {
                    printf("NABU-RAWSTACK image-text index=%u preferred=0x%" PRIx64
                        " bytes=0x%" PRIx64 " slide_signed=%" PRId64 "\n",
                        i, segment.vmaddr, segment.vmsize,
                        (int64_t)(load - segment.vmaddr));
                }
            }
            offset += command.cmdsize;
        }
    }
    struct dyld_all_image_infos check;
    if (nabu_raw_read(task, info.all_image_info_addr, &check, prefix))
        printf("NABU-RAWSTACK images-consistent=%u\n",
            check.infoArray == images.infoArray && check.infoArrayCount == images.infoArrayCount);
}

static int
nabu_raw_target(pid_t pid)
{
    nabu_raw_pid = (long)pid;
    task_t task = MACH_PORT_NULL;
    thread_act_array_t threads = NULL;
    mach_msg_type_number_t number = 0;
    setvbuf(stdout, NULL, _IONBF, 0);
    printf("NABU-RAWSTACK begin pid=%ld version=1 mode=live-unsuspended\n", (long)pid);
    errno = 0;
    kern_return_t kr = task_read_for_pid(mach_task_self(), pid, &task);
    printf("NABU-RAWSTACK task pid=%ld api=task_read_for_pid result=%d errno=%d port=%u\n",
        (long)pid, kr, errno, task);
    if (kr != KERN_SUCCESS || task == MACH_PORT_NULL)
        return 1;
    kr = task_threads(task, &threads, &number);
    printf("NABU-RAWSTACK threads kr=%d count=%u\n", kr, number);
    int result = kr != KERN_SUCCESS;
    if (kr == KERN_SUCCESS) {
        for (unsigned i = 0; i < number; ++i) {
            thread_identifier_info_data_t identifier = {0};
            mach_msg_type_number_t identifier_count = THREAD_IDENTIFIER_INFO_COUNT;
            kern_return_t id_kr = thread_info(threads[i], THREAD_IDENTIFIER_INFO,
                (thread_info_t)&identifier, &identifier_count);
            uint64_t tid = id_kr == KERN_SUCCESS && identifier_count >= THREAD_IDENTIFIER_INFO_COUNT ? identifier.thread_id : 0;
            thread_basic_info_data_t basic = {0};
            mach_msg_type_number_t basic_count = THREAD_BASIC_INFO_COUNT;
            kern_return_t basic_kr = thread_info(threads[i], THREAD_BASIC_INFO,
                (thread_info_t)&basic, &basic_count);
            if (basic_kr == KERN_SUCCESS && basic_count >= THREAD_BASIC_INFO_COUNT)
                printf("NABU-RAWSTACK activity tid=%" PRIu64
                    " run_state=%d flags=0x%x suspend=%d cpu_usage=%d user=%d.%06d system=%d.%06d\n",
                    tid, basic.run_state, basic.flags, basic.suspend_count, basic.cpu_usage,
                    basic.user_time.seconds, basic.user_time.microseconds,
                    basic.system_time.seconds, basic.system_time.microseconds);
            else printf("NABU-RAWSTACK activity tid=%" PRIu64 " kr=%d count=%u\n", tid, basic_kr, basic_count);
            arm_thread_state64_t state;
            memset(&state, 0, sizeof(state));
            mach_msg_type_number_t count = ARM_THREAD_STATE64_COUNT;
            kr = thread_get_state(threads[i], ARM_THREAD_STATE64, (thread_state_t)&state, &count);
            printf("NABU-RAWSTACK thread index=%u port=%u tid=%" PRIu64 " id_kr=%d state_kr=%d count=%u\n",
                i, threads[i], tid, id_kr, kr, count);
            if (kr != KERN_SUCCESS || count < ARM_THREAD_STATE64_COUNT) {
                result = 1;
                continue;
            }
            uint64_t raw[33];
            _Static_assert(sizeof(state) >= sizeof(raw), "ARM64 register layout");
            memcpy(raw, &state, sizeof(raw));
            uint64_t pc = arm_thread_state64_get_pc(state), lr = arm_thread_state64_get_lr(state);
            uint64_t fp = arm_thread_state64_get_fp(state), sp = arm_thread_state64_get_sp(state);
            printf("NABU-RAWSTACK registers tid=%" PRIu64 " pc=0x%" PRIx64 " lr=0x%" PRIx64
                " fp=0x%" PRIx64 " sp=0x%" PRIx64 " pc_raw=0x%" PRIx64 " lr_raw=0x%" PRIx64
                " fp_raw=0x%" PRIx64 " sp_raw=0x%" PRIx64 "\n",
                tid, pc, lr, fp, sp, raw[32], raw[30], raw[29], raw[31]);
            /* Raw GPR values only: never dereference potential arguments or
             * infer lock ownership from an unsuspended register snapshot. */
            for (unsigned base = 0; base < 28; base += 4) {
                printf("NABU-RAWSTACK gpr tid=%" PRIu64 " basex=%u"
                    " x%u=0x%" PRIx64 " x%u=0x%" PRIx64
                    " x%u=0x%" PRIx64 " x%u=0x%" PRIx64 "\n",
                    tid, base, base, raw[base], base + 1, raw[base + 1],
                    base + 2, raw[base + 2], base + 3, raw[base + 3]);
            }
            printf("NABU-RAWSTACK gpr tid=%" PRIu64 " basex=28 x28=0x%" PRIx64 "\n", tid, raw[28]);
            nabu_raw_walk(task, tid, sp, fp);
        }
        /* Release every returned thread right. */
        for (unsigned i = 0; i < number; ++i)
            mach_port_deallocate(mach_task_self(), threads[i]);
        if (threads)
            mach_vm_deallocate(mach_task_self(), (mach_vm_address_t)(uintptr_t)threads,
                (mach_vm_size_t)number * sizeof(*threads));
        nabu_raw_images(task);
    }
    mach_port_deallocate(mach_task_self(), task);
    printf("NABU-RAWSTACK end pid=%ld status=%d io_errors=%u\n", (long)pid, result, nabu_raw_io_errors);
    return result;
}

#undef printf
#endif
