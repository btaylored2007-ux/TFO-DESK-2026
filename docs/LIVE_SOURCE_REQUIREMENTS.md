# Live source still required — 2026-11-06

The 21:15 WITA closure pack supplied the four files that were blocking the bank, fill, and half-risk checks. Those tests now run against that source. `veteran_desk/book.py` is still sha256 `b4453b503f33e34110519bf4d7a9bf391ecc2802edbd87acca3e77dc96b7e891`.

`veteran_desk/desk.py` in this branch is that live file plus the research-mandate calls. The installer replaces it only when the destination hash is `c5d24291f0418a7e64bccc749b73e6e21ebd930147b6b0fae16a2d81eba4b677`. Any other desk hash blocks the apply.

The sanitized learning-evidence test from the earlier learning pack stays. The live copy of that test was omitted because it contained a synthetic secret-shaped fixture.

| Path | Why it is still named |
|---|---|
| `queue_answerer_sgp.py` | `veteran_desk/tests/test_intent_routing_20261104.py` parses `adv_work` from this file. It was not in the closure pack. The test stays red. No substitute was written. |

`staged_fixes/*/book.py` with `RAIL_BANK_R = 1.0` is not the live tick. Do not copy it.
