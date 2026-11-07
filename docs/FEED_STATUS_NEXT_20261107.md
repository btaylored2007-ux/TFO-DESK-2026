# Next feed-status repair

Source-only. `veteran_desk/feed_status_facts.py` separates the four facts. It is not deployed and it does not change a paid feed or a credential.

The 2026-11-07 01:08 WITA scrubbed pack shows four different facts that `feed_status` currently folds together:

- The process is running.
- The cash session is expected to be open. On that Saturday, EU, LSE, Asia, and US cash were not expected open. Crypto was.
- The vendor entitlement succeeded. EODHD reported `authorized` true while `last_msg_utc` was Friday 16:30 UTC, and an older stream status was HTTP 422, symbols limit reached.
- The data is fresh enough to use. Kraken tape was active with `fresh_usable` false. Asia intraday was inactive and `session_closed`.

`reports/feed_health.json` was last written 2026-11-01. The live files in the pack are `feed_status.json` and `feed_keeper_state.json`.

The repair is to store those four facts separately, to keep a weekend cash session closed, and to point acceptance at `feed_status.json` or to refresh `feed_health.json`. It does not add a paid feed or change a credential. It is not part of the lesson-trace change.
