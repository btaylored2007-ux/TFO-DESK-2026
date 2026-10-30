#!/bin/zsh
# com.tfo.veteran-desk-leaderboard: daily 06:00 WITA cutoff. PAPER only. Holds state/veteran_desk/.tick.lock (same lock as run_tick.sh)
# for the whole cutoff, because leaderboard.cutoff() calls BK.tick() itself (fresh marks + prize capital). Python 3.14.
cd "/Users/test/Applications/MultiVenueDesk" || exit 1
export PATH=/Library/Frameworks/Python.framework/Versions/3.14/bin:$HOME/.local/bin:/usr/bin:/bin:/usr/sbin
PY=/Library/Frameworks/Python.framework/Versions/3.14/bin/python3
L=logs/veteran_desk_tick.log; LK=state/veteran_desk/.tick.lock
mkdir -p state/veteran_desk
got=0; for n in $(seq 1 40); do if mkdir "$LK" 2>/dev/null; then got=1; break; fi; sleep 3; done
if [ $got = 0 ]; then
  if [ -n "$(find "$LK" -mmin +10 2>/dev/null)" ]; then rmdir "$LK"; mkdir "$LK"; else echo "$(date '+%F %T WITA') SKIP leaderboard cutoff (lock held 120s)" >> $L; exit 75; fi
fi
trap 'rmdir "$LK" 2>/dev/null' EXIT
echo "$(date '+%F %T WITA') START book tick [leaderboard cutoff]" >> $L
$PY -m veteran_desk.leaderboard cutoff; rc=$?
echo "$(date '+%F %T WITA') END book tick [leaderboard cutoff] rc=$rc" >> $L
exit $rc
