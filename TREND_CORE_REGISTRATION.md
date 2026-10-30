# TREND-CORE — A$1m AUD paper slow-trend book (DEEP18 / Trend18 R2 spirit). PAPER ONLY.

- **Authority:** Brett Taylor, 28 Oct 2026 ~09:18 WITA (`pt1568u`): run an A$1 million paper book in the slow trend-following style, keep A$50k AUD spare cash, deploy the remaining A$950k, make it visible on the War Room dashboard.
- **Accounting path: B (chosen).** New sleeve **alongside** the firm Trend18 book. Own A$1,000,000 capital. **Not** in `book_coverage`, firm equity, `firm_risk`, or the funder-fee base. Trend18 keeps its US$ firm sleeve and its Nov-2 second-half calendar. This avoids double-booking the same UPRO/TQQQ/… positions as two firm books.
- **Why not A (scale/rebadge Trend18):** Trend18 is mid-flight at scale 0.5 with a planned Nov-2 scale-up and a second-half guard. Rebadge/scale mid-month would disturb paired capital transfers and firm rails. Path B keeps firm accounting honest.

## Spec
- **Design:** same spirit as Trend18 / DEEP18 R2 — multi-asset levered risk-parity trend (UPRO/TQQQ/TMF/UGL/BTC), 20% vol target, monthly rebalance + mid-month refresh, TMF duration gate, idle cash above the A$50k spare into DBMF/KMLM/CTA thirds, −30% NAV/unit kill → 10% vol + review.
- **Signals:** `trend18.compute_signals()` (shared frozen law).
- **Capital:** NAV A$1,000,000; **spare A$50,000 AUD never deployed into risk**; deploy up to A$950,000 (trend legs + MF). Marks and P&L in A$ (USD prices ÷ AUDUSD).
- **Costs:** 12.5bp/side ETF (MF included), 25bp/side BTC, charged in A$ cash on every fill.
- **PAPER ONLY.** No live broker, no KDESK live-order, no Betfair.

## Files
| What | Path |
|---|---|
| Code | `trend_core.py` |
| Ledger | `state/trend_core.json` |
| History | `state/trend_core_history.csv` |
| Fills | `state/trend_core_fills.csv` |
| Log | `state/trend_core_log.txt` |
| War Room feed | `static/trend_core_status.json` |
| Registration | this file |

## Commands
```
python3 trend_core.py status
python3 trend_core.py mark          # refresh marks + status JSON
python3 trend_core.py enter         # one-shot inception (no-op if already entered)
python3 trend_core.py write-status
```

## Inception (28 Oct 2026 ~09:23 WITA)
- Signal asof **2026-10-26** (last completed US close at entry). TMF duration gate **closed**. UGL trend **off**.
- Fills (paper): UPRO / TQQQ / XBT/USD + MF thirds DBMF/KMLM/CTA. Spare cash A$50,000 left in cash.
- Next monthly rebalance: **2026-11-02** (first US session of November; signal from 30 Oct close).
- Alongside: firm Trend18 (`state/trend18.json`) continues unchanged at scale 0.5 until its own Nov calendar.

## Dashboard
- War Room (`:18812`): **TREND-CORE · A$1m paper** card (reads `/static/trend_core_status.json`).
- Strategy Tracker row: `TREND-CORE A$1m`.
- `/report` picks up the tracker row via `strategy_tracker`.

## Firm risk note
TREND-CORE is a **separate paper experiment**. Its A$1m is not claimed against firm powder and does not add to firm long notional. Do not sum TREND-CORE NAV into firm equity. Trend18 remains the firm-integrated trend sleeve.
