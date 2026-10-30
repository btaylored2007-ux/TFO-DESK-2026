#!/bin/zsh
# thfreview 20261030 (HT): com.tfo.pit-daily-topup - full-universe Yahoo top-up of pit.sqlite, then OANDA FX tick-volume fill. PAPER, data only.
cd /Users/test/Applications/MultiVenueDesk || exit 1
ulimit -n 4096 2>/dev/null || ulimit -n 2048  # healthfix 20261030: launchd soft maxfiles 256 starved yfinance threads (curl 6/27/77) + sqlite "unable to open database file"
export PATH=/Library/Frameworks/Python.framework/Versions/3.14/bin:/usr/bin:/bin; export PYTHONPATH=.
L=logs/pit_daily_topup.log
echo "$(date '+%F %T WITA') START topup" >> $L
python3 -W ignore -m veteran_desk.pit_daily_topup 2>&1 | grep -v -E "possibly delisted|Deprecation|Timedelta|^\s*$" >> $L; rc=${pipestatus[1]}
python3 -W ignore -m veteran_desk.fx_tickvol_fill >> $L 2>&1 || true
echo "$(date '+%F %T WITA') END topup rc=$rc" >> $L
exit $rc
