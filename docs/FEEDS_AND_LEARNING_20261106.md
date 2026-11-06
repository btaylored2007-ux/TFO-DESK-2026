# Feeds and learning — built here, not deployed

## Built and tested in this workspace

- `veteran_desk/feed_health.py`: Asia bar freshness is not the status-file age. A session with `last_bar` null is `stale_underlying`. A closed session, weekend, or listed holiday is `session_closed`. US regular hours use `America/New_York`; 07:43 New York time is before the open, not a closed-market bug.
- Europe REST pull (`tools/intraday_bars_pull.py`) keeps US names off the EODHD budget and rotates non-US research names inside `EODHD_REST_CAP` (default 12). `eu-quote` is not turned on. Names outside a supplied entitlement list are `not_entitled` and are not requested. Unknown entitlement is labeled unknown, not “subscribed.”
- `veteran_desk/lesson_trace.py`: prompt inclusion, model citation, and validated impact are different rows. “I learned” does not validate. Coverage lists all 18 seats; a seat with no row is `no_evidence`, not a made-up trade.
- `seat_brain.prompt_block` appends an inclusion row outside tests. Reviews stay hypotheses.
- Asia writer sets `data_status` / `healthy` from bars. The feed keeper marks a recent Asia file degraded when the bars are empty during a session.

Tests run here: `test_feed_health_20261106.py`, `test_lesson_trace_20261106.py`, `test_learning_evidence_20261106.py`, `test_live_bank_snapshot_20261106.py` (63 passed with the evidence file’s own cases). Chat fixture: 6 mocked scenarios passed. No network feed call. Not deployed.

## Not run, missing modules

- `test_learning_retry_durable.py` imports `veteran_desk.bridge_maintenance` (not in this tree).
- Autonomy tests import `veteran_desk.thf_http` (not in this tree). 9 autonomy cases passed; 26 failed on that import or on other files this archive did not include (`tools/thf_dash_refresh.sh`, `test_intent_routing_20261104.py`).
- Live `book.py` tick tests need `test_fillfix_20261103.py` and `session_expiry.py`. Not rerun. The droplet’s own 7-test run is not this workspace.

## Left unknown on purpose

- NBBO / spread: no spread state files and no NBBO writer in the supplied pricing modules. Capability is unknown, not proven absent, and no new vendor was added.
- Borrow SG/IN zero rows and DCD 403: observed upstream, not changed.
- Secondary Kraken `exec_speed` refusal with a healthy primary tape: not treated as a primary outage and not “fixed” by weakening the primary.
- Corporate-action and news entitlements: not tested here.
