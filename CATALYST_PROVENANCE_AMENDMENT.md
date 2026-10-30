# CM-069 — catalyst provenance gate (Speculative Sleeve)

**Date:** 2026-09-03 · **Status:** LIVE · **Authority:** funder-directed ("FIX AND RESUME ALL")

The sealed `SPECULATIVE_SLEEVE_PREREGISTRATION.md` is NOT edited by this
amendment. It is append-only; this file records a change in force alongside it.

## The defect

`fundamental_spec_book.py` gate 4 was **presence-only**:

```python
if not catalyst_date:
    raise SystemExit("REFUSED: no dated catalyst. ...")
```

A date that traced to nothing passed it, and the position record had **no source
field at all** — the function was structurally incapable of recording provenance.

Found by a 632-position adversarial audit. It flagged `2105.HK`, whose stored
`catalyst_date` was `2026-12-31` while its own thesis states in capitals that no
decision date exists and *"the desk refused to invent one."* The PR Newswire
source (302849031, 2026-08-11) says only "as soon as possible."

But 2105.HK is not the defect. `--unsourced` reports **11 of 11** open positions
with no provenance, and the date distribution is the tell:

| catalyst_date | positions |
|---|---|
| 2026-12-31 (year end) | 6 |
| 2026-09-30 (quarter end) | 2 |
| specific dates | 3 |

Eight of eleven are period-end placeholders. Singling out one name would have
been discretion dressed as rule-following.

## The rule now in force — NEW ENTRIES ONLY

`catalyst_source` is a required argument. An entry is REFUSED when it is absent,
is a placeholder (`TBD`, `internal`, `unknown`, `estimate`, …), is under 12
characters, or carries neither a URL nor a dated citation — so the date can
always be traced back to something.

Registration §4: *"Still dated. Still sourced."* and *"Missing data is a REFUSAL,
not a pass. 'we could not check' must never read as 'fine.'"*

## What this does NOT do

It does **not** re-gate or force-close the 11 open positions.
`TOURNAMENT_PREREGISTRATION.md` forbids force-liquidation, and applying a rule
written today to a position opened under the old one is exactly the retroactive
re-keying this desk refuses. They exit on their registered rules;
`--unsourced` keeps the gap visible instead of silently closed.

This is a TIGHTENING. No control was loosened.

## Verification

| case | result |
|---|---|
| `catalyst_source=None` | REFUSED (provenance) |
| `"TBD"` / `"internal"` | REFUSED (placeholder) |
| `"company investor relations page"` | REFUSED (no URL, no date) |
| PR Newswire URL + `2026-08-11` | passes provenance → falls through to normal hard gates |
| ledger after all five | **11 positions, unchanged** |

Backup: `/tmp/cy_backup/fundamental_spec_book.py.pre_cm069`
