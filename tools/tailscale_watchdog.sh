#!/bin/bash
# tfo_fastllm_20261030: keep Tailscale up (no sudo). Every 60s via launchd.
TS=/usr/local/bin/tailscale; L=~/Applications/MultiVenueDesk/logs/tailscale_watchdog.log
if $TS status >/dev/null 2>&1 && $TS ping -c 1 --timeout 5s "$($TS status --json 2>/dev/null | /usr/bin/python3 -c 'import json,sys;print(json.load(sys.stdin)["Self"]["DNSName"])' 2>/dev/null)" >/dev/null 2>&1 || /sbin/ping -c1 -t5 100.100.100.100 >/dev/null 2>&1; then exit 0; fi
echo "$(date '+%F %T') tailscale down -> restarting" >> $L
pgrep -x Tailscale >/dev/null || open -a Tailscale
$TS up >/dev/null 2>&1 || true
