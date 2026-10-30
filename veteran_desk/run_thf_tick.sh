#!/bin/zsh
# com.tfo.thf-tick: Taylor Hedge Fund fresh paper book tick, 24/7 every 2 min (book.py blocks fills while a venue is closed). PAPER only. tfo_thffresh_20261030
cd "/Users/test/Applications/MultiVenueDesk" || exit 1
export PATH=/Library/Frameworks/Python.framework/Versions/3.14/bin:$HOME/.local/bin:/usr/bin:/bin:/usr/sbin THF_DESK=1
L=logs/thf_tick.log; LK=state/thf/.tick.lock; mkdir -p state/thf
if ! mkdir "$LK" 2>/dev/null; then   # GPT review: never steal by age; steal only if the owning pid is dead
  op=$(cat "$LK/pid" 2>/dev/null); if [ -n "$op" ] && kill -0 "$op" 2>/dev/null; then echo "$(date '+%F %T WITA') SKIP thf tick (lock held by live pid $op)" >> $L; exit 0; fi
  rm -rf "$LK"; mkdir "$LK" || exit 0
fi
echo $$ > "$LK/pid"
trap 'rm -rf "$LK" 2>/dev/null' EXIT
echo "$(date '+%F %T WITA') START thf book tick" >> $L
python3 -m veteran_desk.book tick >> $L 2>&1; rc=$?
echo "$(date '+%F %T WITA') END thf book tick rc=$rc" >> $L
exit $rc
