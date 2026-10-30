#!/bin/bash
# Brett 28 Oct 14:57 WITA: ONE-OFF Trend18 second-half deploy, Fri 30 Oct 2026 near the US close. PAPER ONLY.
# Guards: ET date must be 2026-10-30, ET time 15:45-15:57, done-marker absent. Live Kraken BTC (fail closed in trend18).
# UPRO/TQQQ no-add via state/trend18_no_add.json. --fit trims the BTC buy to available powder (never breaches the $50k reserve).
cd /Users/test/Applications/MultiVenueDesk || exit 1
PY=/Library/Frameworks/Python.framework/Versions/3.14/bin/python3
MARK=state/trend18_30oct_once.done
D=$(TZ=America/New_York date +%Y-%m-%d); HM=$(TZ=America/New_York date +%H%M)
echo "$(date '+%F %T %Z') trend18 30oct once: ET $D $HM"
[ "$D" = "2026-10-30" ] || { echo "REFUSED: not 30 Oct ET"; exit 0; }
[ "$HM" -ge 1545 ] && [ "$HM" -le 1557 ] || { echo "REFUSED: outside 15:45-15:57 ET window"; exit 0; }
[ -e "$MARK" ] && { echo "REFUSED: already ran ($MARK)"; exit 0; }
date -u +%FT%TZ > "$MARK.started"
$PY trend18.py --execute --reason manual --scale 1.0 --fit --allow-yahoo; rc=$?
echo "rc=$rc $(date -u +%FT%TZ)" > "$MARK"
exit $rc
