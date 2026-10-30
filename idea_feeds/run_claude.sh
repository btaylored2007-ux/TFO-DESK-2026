#!/bin/zsh
# Idea feed (claude) - WATCH-LIST IDEAS ONLY. Called by launchd com.tfo.ideafeed.claude
export PATH=/Library/Frameworks/Python.framework/Versions/3.14/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin
cd /Users/test/Applications/MultiVenueDesk/idea_feeds || exit 1
exec python3 idea_feed_runner.py --tool claude --market "${1:-auto}"
