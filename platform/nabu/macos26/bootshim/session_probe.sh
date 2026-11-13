#!/bin/sh
# Direct child of the owner boot tool. Do not depend on launchd job discovery.
PATH=/usr/bin:/bin:/usr/sbin:/sbin
export PATH
umask 077
echo "NABU-SESSION-PROBE started pid=$$ version=5"
trap 'echo "NABU-SESSION-PROBE shell exit=$?"' 0

# Diagnostics must not block later samples. Keep child output off the console
# until the child has finished or the guard has terminated it.
run_bounded()
{
    limit=$1
    output=$2
    shift 2
    "$@" >"$output" 2>&1 &
    child=$!
    (
        /bin/sleep "$limit"
        echo "NABU-SESSION-PROBE command-timeout pid=$child limit=$limit" >&2
        kill -TERM "$child" 2>/dev/null
        /bin/sleep 1
        kill -KILL "$child" 2>/dev/null
    ) &
    guard=$!
    wait "$child"
    command_status=$?
    kill "$guard" 2>/dev/null
    wait "$guard" 2>/dev/null
    return "$command_status"
}

find_pid()
{
    while read -r candidate executable rest; do
        case "$candidate" in ''|*[!0-9]*) continue ;; esac
        if [ "${executable##*/}" = "$1" ]; then
            echo "$candidate"
            return 0
        fi
    done <"$process_list"
    return 1
}

snapshot()
{
    echo "NABU-SESSION-PROBE begin round=$1"
    /bin/date -u
    reports="/private/var/tmp/nabu-session-probe"
    shim="/System/Library/Filesystems/apfs.fs/Contents/Resources/apfs_boot_util"
    process_list="$reports/nabu-probe-processes-$$.txt"
    # One in-process discovery loop captures all three targets before slow
    # symbol processing. Every actual raw record uses fd2 single-line writes.
    "$shim" --nabu-raw-stacks --watch >"$reports/nabu-raw-stacks.txt" 2>&1 &
    raw_watcher=$!
    sampled=" "
    # Mount phase 2 precedes Early boot complete. A fixed 15-second delay is
    # not evidence that loginwindow or WindowServer exists. Sample each real
    # PID as soon as it appears, without waiting on launchctl first.
    attempt=0
    while [ "$attempt" -lt 30 ]; do
        attempt=$((attempt + 1))
        # ucomm is the kernel's short process name. comm asks ps to resolve
        # executable paths, which can block on the filesystem during boot.
        run_bounded 5 "$process_list" /bin/ps -axo pid=,ucomm=
        listing_status=$?
        if [ "$listing_status" -ne 0 ]; then
            echo "NABU-SESSION-PROBE process-list status=$listing_status attempt=$attempt"
            /usr/bin/head -c 16384 "$process_list"
        else
            for process in opendirectoryd loginwindow lsd WindowServer launchservicesd; do
                case "$sampled" in *" $process "*) continue ;; esac
                target=$(find_pid "$process") || continue
                report="$reports/nabu-sample-$process.txt"
                diagnostic="$reports/nabu-sample-$process.stderr"
                echo "NABU-SESSION-PROBE sample process=$process pid=$target round=$1"
                # Duration zero selects the tool's single-snapshot mode.
                # -mayDie preloads symbols before sampling; boot daemons are
                # stable targets and do not need that costly preload.
                run_bounded 120 "$diagnostic" /usr/bin/sample "$target" 0 100 -file "$report"
                result=$?
                echo "NABU-SESSION-PROBE sample-result process=$process pid=$target status=$result"
                /bin/cat "$diagnostic"
                "$shim" --nabu-probe-relay "nabu-sample-$process.stderr"
                if [ -f "$report" ]; then
                    echo "NABU-SESSION-PROBE report-retained path=$report"
                    "$shim" --nabu-probe-relay "nabu-sample-$process.txt"
                fi
                sampled="$sampled$process "
            done
        fi
        # lsd belongs to the later user session. Capture it or launchservicesd
        # opportunistically, without keeping pre-login polling alive for lsd.
        remaining=0
        for process in opendirectoryd loginwindow WindowServer; do
            case "$sampled" in *" $process "*) ;; *) remaining=$((remaining + 1)) ;; esac
        done
        [ "$remaining" -eq 0 ] && break
        if [ "$attempt" = 1 ] || [ $((attempt % 12)) -eq 0 ]; then
            echo "NABU-SESSION-PROBE awaiting-processes remaining=$remaining attempt=$attempt"
        fi
        /bin/sleep 10
    done
    for process in opendirectoryd loginwindow lsd WindowServer; do
        case "$sampled" in *" $process "*) ;; *) echo "NABU-SESSION-PROBE target-not-observed process=$process round=$1" ;; esac
    done
    # These supporting commands previously blocked round 2 before sampling.
    for job in com.apple.opendirectoryd com.apple.loginwindow com.apple.WindowServer; do
        diagnostic="$reports/nabu-launchctl-$$.txt"
        echo "NABU-SESSION-PROBE launchd job=$job"
        run_bounded 15 "$diagnostic" /bin/launchctl print "system/$job"
        result=$?
        echo "NABU-SESSION-PROBE launchd-result job=$job status=$result"
        /usr/bin/head -c 16384 "$diagnostic"
        /bin/rm -f "$diagnostic"
    done
    wait "$raw_watcher"
    echo "NABU-SESSION-PROBE raw-watcher status=$? report=$reports/nabu-raw-stacks.txt"
    echo "NABU-SESSION-PROBE end round=$1"
}

sleep 15
snapshot 1
echo "NABU-SESSION-PROBE finished"
