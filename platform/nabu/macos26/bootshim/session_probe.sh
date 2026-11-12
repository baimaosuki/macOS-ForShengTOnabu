#!/bin/sh
# Direct child of the owner boot tool. Do not depend on launchd job discovery.
PATH=/usr/bin:/bin:/usr/sbin:/sbin
export PATH
umask 077
echo "NABU-SESSION-PROBE started pid=$$ version=3"
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
    process_list="/private/var/tmp/nabu-probe-processes-$$.txt"
    sampled=" "
    # Mount phase 2 precedes Early boot complete. A fixed 15-second delay is
    # not evidence that loginwindow or WindowServer exists. Sample each real
    # PID as soon as it appears, without waiting on launchctl first.
    attempt=0
    while [ "$attempt" -lt 60 ]; do
        attempt=$((attempt + 1))
        run_bounded 15 "$process_list" /bin/ps -axo pid=,comm=
        listing_status=$?
        if [ "$listing_status" -ne 0 ]; then
            echo "NABU-SESSION-PROBE process-list status=$listing_status attempt=$attempt"
            /usr/bin/head -c 16384 "$process_list"
        else
            for process in opendirectoryd loginwindow lsd WindowServer; do
                case "$sampled" in *" $process "*) continue ;; esac
                target=$(find_pid "$process") || continue
                report="/private/var/tmp/nabu-sample-$process.txt"
                diagnostic="/private/var/tmp/nabu-sample-$process.stderr"
                /bin/rm -f "$report"
                echo "NABU-SESSION-PROBE sample process=$process pid=$target round=$1"
                run_bounded 15 "$diagnostic" /usr/bin/sample "$target" 2 10 -mayDie -file "$report"
                result=$?
                echo "NABU-SESSION-PROBE sample-result process=$process pid=$target status=$result"
                /usr/bin/head -c 16384 "$diagnostic"
                if [ -f "$report" ]; then
                    /usr/bin/head -c 262144 "$report"
                    /bin/rm -f "$report"
                fi
                sampled="$sampled$process "
            done
        fi
        if [ "$sampled" = " opendirectoryd loginwindow lsd WindowServer " ]; then
            break
        fi
        # Targets can appear in any order; do not require launch order.
        remaining=0
        for process in opendirectoryd loginwindow lsd WindowServer; do
            case "$sampled" in *" $process "*) ;; *) remaining=$((remaining + 1)) ;; esac
        done
        [ "$remaining" -eq 0 ] && break
        if [ "$attempt" = 1 ] || [ $((attempt % 12)) -eq 0 ]; then
            echo "NABU-SESSION-PROBE awaiting-processes remaining=$remaining attempt=$attempt"
        fi
        /bin/sleep 5
    done
    for process in opendirectoryd loginwindow lsd WindowServer; do
        case "$sampled" in *" $process "*) ;; *) echo "NABU-SESSION-PROBE target-not-observed process=$process round=$1" ;; esac
    done
    # These supporting commands previously blocked round 2 before sampling.
    for job in com.apple.opendirectoryd com.apple.loginwindow com.apple.WindowServer; do
        diagnostic="/private/var/tmp/nabu-launchctl-$$.txt"
        echo "NABU-SESSION-PROBE launchd job=$job"
        run_bounded 15 "$diagnostic" /bin/launchctl print "system/$job"
        result=$?
        echo "NABU-SESSION-PROBE launchd-result job=$job status=$result"
        /usr/bin/head -c 16384 "$diagnostic"
        /bin/rm -f "$diagnostic"
    done
    /bin/rm -f "$process_list"
    echo "NABU-SESSION-PROBE end round=$1"
}

sleep 15
snapshot 1
sleep 180
snapshot 2
sleep 180
snapshot 3
echo "NABU-SESSION-PROBE finished"
