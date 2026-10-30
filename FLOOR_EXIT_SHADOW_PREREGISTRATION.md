# FLOOR EXIT SHADOW — pre-registration

**Registered:** 2026-09-05 · **Status:** LIVE · **Capital:** $0 (paired measurement only)
**Authority:** funder-directed — "register the forward test with real stops and longer holds"

## What is being tested

Two defects found by mining the desk's own 33 books, both verified directly on the ledgers:

**1. The stops do not stop.** `kraken_floor.exit_signal(cfg, pos, H, L, C)` receives the
intraday high/low arrays and tests the stop against `C[-1]`, the close. 63 stop-tagged
trades on four floors: configured 8/12/14% stops realised **mean −12.61%, median −10.65%,
worst −62.22%** (APR/USD on a 14% stop). **60 of 63** realised worse than their level.
A code path, not a market state.

**2. Holds are too short to survive costs.** Median hold **5.00 days**. Round-trip cost ×
365 / hold = **21.9–58.4%/yr hurdle** at 5 days, against a best-ever measured net edge
of **3.15%/yr**. The desk's own `holding_period_lab.json` shows net return crossing zero
at 14 days through cost amortisation alone.

## Design — paired shadow, not a patch

The shadow **mirrors every live kraken_floor entry** — same symbol, side, price, units,
timestamp — and diverges **only on exit rules**. Each trade is a paired observation:
live exit vs shadow exit on an identical entry. The difference is the treatment.

| | CONTROL (live floor) | TREATMENT (shadow) |
|---|---|---|
| Stop check | close price at hourly tick | first bar whose **LOW/HIGH touches the level** |
| Stop fill | wherever the tick lands | the level, or the **open if it gapped through** |
| Discretionary exits | immediate | **ignored before 14 days**; honoured after |
| Max hold | 12 / 15 / 40 days | **45 days** |
| Entries | — | **identical, mirrored from live** |

## Hypothesis and decision rule — fixed before any outcome

**H1:** shadow realised return per trade > live realised return per trade.
**H0:** no difference.

At **40 paired closed trades**, collapse to one observation per exit day:
- paired mean difference **> +2pp** with the sign consistent in both halves →
  treatment adopted on the live floors as a registered amendment
- within **±2pp** → keep running
- **< −2pp** → treatment rejected; this file closed

The **stop half** and the **hold half** are recorded and judged **separately**. A win
on one is never credited to the other.

## What cannot happen

No capital is deployed. No live floor is modified. Nothing here can lose a dollar.
The live floor's track record is untouched and remains the control.

## Frozen artefacts

Rules live in `floor_exit_shadow.py` (`MIN_HOLD_DAYS = 14.0`, `MAX_HOLD_DAYS = 45.0`,
`DECISION_N = 40`, `DECISION_BAND_PP = 2.0`). Editing them invalidates this registration.
SHA-256 seal below covers this file and the script at registration.

**SEAL (sha256 of this file + floor_exit_shadow.py at registration):** `0182cc5beb6c26fe3c748964f512616c333c0dda5d1dbff7af06dc094d731175`
