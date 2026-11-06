# THF half-bank at net +0.7R

Status: **code and unit tests in this commit. Not deployed. Not observed on the Singapore runtime.**

## What the live snapshot actually contains

The 2026-11-06 sanitized baseline (`docs/live_baseline_20261106/`) includes `veteran_desk/seat_cli.py`, which can record a manual partial close by calling `book.mark_half_banked`. It does **not** decide the +0.7R threshold.

The automatic decision runs in `veteran_desk/book.py` `tick`. **That file is not in the live snapshot.** This commit does not replace it and does not claim the running tick was inspected.

## What the stale GitHub `book.py` does (not verified as live)

Read only as the old mirror, not as production proof:

- `RAIL_HALF_R = 2.0` banks on **gross** `peak_fav_R` (price versus the initial stop, fees ignored).
- `bank_trigger` banks grade A/A+ when **net** R >= `1.0` (`trend` >= `1.5`).
- `tfo_bank_rail_scanner.py` half-banks a different sleeve at **>= +2% of deploy basis**. That percent rule is not the THF R rule.

Those three must not be mixed. Gross R, net R, and percent of notional are different.

## Rule implemented here

`veteran_desk/thf_half_bank.py`

- Trigger: **net R >= 0.7**, long and short, exact boundary included.
- Net R = signed price P&L on **filled** units, minus booked entry cost, minus exit cost at this quote, minus borrow accrued, divided by `abs(fill - initial_stop) * filled units`.
- Initial stop is immutable. A trailed stop does not shrink the risk basis and is not loosened.
- Half is 50% of **filled** units, floored, once only. Requested size is recorded and not used.
- A second call, a retry, or a process restart that reloads the marker does not bank again.
- Missing quote, stale quote (older than 15 minutes), closed session, or unknown cost does not bank and does not invent a number.
- Gross R and percent-of-notional are reported and do not trigger.
- Stop and target are unchanged. T2, giveback, full-flat and trailing constants are not edited.
- No ledger write, no live order, no backdated fill.

## Exact file still required before this can run in the engine

From `/opt/tfo/live/thf` (the droplet copy, not GitHub):

- `veteran_desk/book.py` (the tick that currently half-banks)
- any local helper that tick uses for `net_R`, `partial_close`, and quote age, if they are not inside that file

Do not deploy this repository's `veteran_desk/book.py` over the droplet. It is the stale mirror.

## Hook to apply only after the live file hash is checked

Inside the live tick, before the existing half-bank block, call `thf_half_bank.decide` with the position, a quote that includes `price`, `asof`, `stale`, and `session_open`, and the entry/exit costs the tick already computed. If `decision["bank"]` is true, close `decision["shares_to_bank"]` through the existing `partial_close` and then `apply_decision` so the marker is stored. If the decision reason is `stale_quote`, `cost_unknown`, or `gross_reached_0.7_but_net_did_not`, do not fall through to the +2% scanner or to gross `peak_fav_R >= 2`.

Until that live file is patched and restarted under its own base-hash check, the running engine is unchanged.

## Tests

`veteran_desk/tests/test_thf_half_bank_20261106.py` (8 passed in this workspace: boundaries, long/short, gross-versus-net, +2% ignored, filled-versus-requested, once-only restart, odd lot, stale quote, trailed stop, reimport).

## Rollback

Remove `veteran_desk/thf_half_bank.py`. Nothing else in the running tick imports it until the live `book.py` hook exists. If that hook was added, restore the backed-up `book.py` whose sha256 was recorded at deploy time.
