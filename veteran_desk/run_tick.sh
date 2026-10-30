#!/bin/zsh
# com.tfo.veteran-desk-tick: US-session book tick every 5 min (21:25-04:05 WITA). PAPER only.
cd "/Users/test/Applications/MultiVenueDesk" || exit 1
export PATH=/Library/Frameworks/Python.framework/Versions/3.14/bin:$HOME/.local/bin:/usr/bin:/bin:/usr/sbin
L=logs/veteran_desk_tick.log; LK=state/veteran_desk/.tick.lock
mkdir -p state/veteran_desk
ANY=0; SRC=session; [ "$1" = "--any-session" ] && { ANY=1; SRC="${2:-other}"; }   # --any-session <src>: off-session/crypto marking tick from another job - same lock, no NYSE gate (book.py still blocks fills while a venue is closed)
if [ $ANY = 0 ] && ! python3 veteran_desk/tick_window.py >/dev/null 2>&1; then echo "$(date '+%F %T WITA') SKIP tick (outside NY session +/-5 min)" >> $L; exit 0; fi   # DST-aware gate (follow-up 2)
if [ $ANY = 1 ]; then   # wait up to 30s for a session tick to finish, then take the same lock
  got=0; for n in 1 2 3 4 5 6 7 8 9 10; do if mkdir "$LK" 2>/dev/null; then got=1; break; fi; sleep 3; done
  if [ $got = 0 ]; then
    if [ -n "$(find "$LK" -mmin +10 2>/dev/null)" ]; then rmdir "$LK"; mkdir "$LK"; else echo "$(date '+%F %T WITA') SKIP tick [$SRC] (lock held 30s)" >> $L; exit 0; fi
  fi
fi
if [ $ANY = 0 ] && ! mkdir "$LK" 2>/dev/null; then
  if [ -n "$(find "$LK" -mmin +10 2>/dev/null)" ]; then rmdir "$LK"; mkdir "$LK"; else echo "$(date '+%F %T WITA') SKIP tick (lock held)" >> $L; exit 0; fi
fi
trap 'rmdir "$LK" 2>/dev/null' EXIT
echo "$(date '+%F %T WITA') START book tick [$SRC]" >> $L
python3 -m veteran_desk.book tick >> $L 2>&1; rc=$?
echo "$(date '+%F %T WITA') END book tick [$SRC] rc=$rc" >> $L
exit $rc
