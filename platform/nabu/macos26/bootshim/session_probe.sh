#!/bin/sh
# One boot, three snapshots. Original signed macOS tools; no service restarts.
PATH=/usr/bin:/bin:/usr/sbin:/sbin
export PATH
umask 077

snapshot()
{
    echo "NABU-SESSION-PROBE begin round=$1"
    /bin/date -u
    /bin/ps -axo pid,ppid,state,pcpu,time,comm
    for job in com.apple.opendirectoryd com.apple.loginwindow com.apple.WindowServer; do
        echo "NABU-SESSION-PROBE launchd job=$job"
        /bin/launchctl print "system/$job"
    done
    for process in opendirectoryd loginwindow lsd WindowServer; do
        report="/private/var/tmp/nabu-sample-$process.txt"
        echo "NABU-SESSION-PROBE sample process=$process"
        /usr/bin/sample "$process" 2 10 -mayDie -file "$report" &
        sampler=$!
        (
            /bin/sleep 15
            kill -TERM "$sampler" 2>/dev/null
        ) &
        timer=$!
        wait "$sampler"
        result=$?
        kill "$timer" 2>/dev/null
        wait "$timer" 2>/dev/null
        echo "NABU-SESSION-PROBE sample-result process=$process status=$result"
        if [ -f "$report" ]; then
            /usr/bin/head -c 262144 "$report"
            /bin/rm -f "$report"
        fi
    done
    echo "NABU-SESSION-PROBE end round=$1"
}

sleep 90
snapshot 1
sleep 180
snapshot 2
sleep 180
snapshot 3
echo "NABU-SESSION-PROBE finished"
