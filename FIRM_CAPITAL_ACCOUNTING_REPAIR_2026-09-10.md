# Firm capital accounting repair — 2026-09-10

Authorized in-session by the funder for the PAPER firm. This is a measurement
repair plus fail-closed handling of unknown capital, not a trading rule change.
The firm warning remains −10%; halt-new-entry remains −20%; no force liquidation.
Existing book freezes, positions, cash, fill records and registrations are untouched.

## Verified defect and measurement scope

The dated-event book received $75,000 from convexity on 2026-09-05 at 19:38:11 UTC.
Both ledgers record opposite transfers. At the next firm observation, equity rose
only $200.99 but the old monitor raised the peak from $1,692,532.35 to $1,767,532.35:
it classified a newly readable book as external capital and ignored the donor.
The dated-event book later returned $67,503.96; both legs are recorded. The two
books' combined net contributed capital remains $150,000.

The original CSV is authoritative evidence of observations and will not be rewritten.
The repair replays complete observations from 2026-09-05 18:50:55 UTC, initialized
with the recorded pre-transfer peak $1,692,532.35, achieved September 4 10:21:51 UTC.
The highest subsequent complete observation is $1,694,524.90, September 7 13:20:14 UTC.
The Value Book's September 9 unreadable observations are retained but excluded from
peak formation. They are not a withdrawal followed by a contribution.

At the pre-repair September 10 05:08:55 UTC observation, $1,676,643.96 equity was
reported as −5.14% / $90,798.20 off the artificial peak. The verified interval gives
−1.055218% / $17,880.94. This does not earn a dollar, erase a fill, or establish
inception return. Historical marks are used as recorded; a full inception,
cash-flow-adjusted NAV audit is outside this narrow repair.

## Future behavior

- Initial funding of a newly registered book uses its recorded funding basis,
  never its marked equity as an assumed deposit.
- Changes to existing funding require capital-flow receipts; resetting a seed
  without a receipt cannot erase a loss. Internal transfers require paired,
  equal and opposite receipts and net to zero firm-wide.
- Native non-USD funding is stable across FX changes. FX mark changes are returns.
- An unreadable book remains in known coverage. It cannot shift a peak, create a
  withdrawal, or print a new economic drawdown. The last complete drawdown remains
  visible with `drawdown_valid=false`, `level=UNMEASURED`, and new entries halted.
- A removed book requires an explicit external withdrawal. Unexplained removal,
  malformed flow, conflicting funding amount or unpaired transfer fails closed.
- A process lock serializes each valuation/reconciliation/publication. Funding
  fingerprints detect transfer metadata changing during valuation. State and report
  publish via unique temporary files, fsync and atomic replacement.

`firm_capital_flows.py` is read-only against source ledgers. `firm_risk.py` writes
only consolidated risk outputs, history, lock and repair evidence. It cannot trade.

## Reproduction and receipts

Preview: `python3 firm_risk.py --repair-capital-20260910`

Authorized application: `python3 firm_risk.py --repair-capital-20260910 --apply`

The application verifies the exact paired events and faulty historical step,
refuses unreviewed later capital events or incomplete coverage, and refuses to
clear a pre-existing firm halt. It creates a unique directory under
`reports/repairs/firm-capital-20260910-*` containing original state, report and CSV,
paired source-ledger evidence, hashes, `verified_interval_replay.json` and
`receipt.json`. It appends a fresh corrected observation; previous CSV rows stay
byte-for-byte intact. Application is idempotent by repair ID.

Validation: `python3 -m unittest discover -s tests -p 'test_firm_capital_flows.py' -v`
and `python3 -m unittest discover -s tests -p 'test_desk_risk_health.py' -v`.
Tests cover transfers/reversals, external contributions/withdrawals, missing-book
recovery, silent seed reset, FX changes, real highs, genuine halt, atomic writes
and interprocess writer exclusion. Tests use only synthetic files and no network.

## Applied result

Applied 2026-09-10 05:44:39 UTC. Refreshed consolidated equity $1,676,497.85;
verified-interval peak $1,694,524.90; drawdown −1.06%; 35/35 books readable;
`drawdown_valid=true`, `capital_accounting_valid=true`, level `OK`.
This $146.11 difference from the earlier equity snapshot is refreshed marking,
not the accounting correction or a fabricated profit.

Receipt: `reports/repairs/firm-capital-20260910-84kbq2tm/receipt.json`.
All backup hashes verified; original history remains a byte-identical prefix of
the current CSV; the two paired source ledgers remain byte-identical to their
pre-application copies; state and report are identical. A repeated repair call
returns `already_applied` without another replay or history append.
Final tests: 21 new accounting tests and 6 existing risk-health tests passed;
Python compilation and whitespace checks passed.
