#!/bin/zsh
# tfo_seatpipe_once2135_20261030: one-off extra full pipeline (TFO approved 18:20 WITA). Respects run_pipeline.sh lock; removes itself after running.
L=/Users/test/Applications/MultiVenueDesk/logs/veteran_desk.log
P=/Users/test/Library/LaunchAgents/com.tfo.veteran-desk-once-20261030.plist
if [ "$(date +%Y%m%d)" = "20261030" ]; then
  M=$(date +%M); while [ "$M" -ge 30 ] && [ "$M" -le 33 ]; do sleep 20; M=$(date +%M); done   # never inside :30-:33
  echo "$(date '+%F %T WITA') ONE-OFF 21:35 pipeline (tfo_seatpipe_once2135_20261030)" >> $L
  /bin/zsh /Users/test/Applications/MultiVenueDesk/veteran_desk/run_pipeline.sh
fi
mv "$P" "$P.done" 2>/dev/null
launchctl bootout gui/$(id -u)/com.tfo.veteran-desk-once-20261030 2>/dev/null
exit 0
