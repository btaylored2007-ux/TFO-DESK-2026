# SEED RECOVERY SLEEVE — Pre-Registration

**Sealed 2026-09-16. FROZEN. Never edit this document to fit a result.**

**Authority:** Funder Brett Taylor, full authorisation in Grok Bot chat 2026-09-16 —
*"create a new strategy and deploy the remaining capital/cash to trade out of this.
full authorisation"*

**PAPER ONLY** — no broker is connected anywhere and none may be added.

---

## 1. WHAT IS BEING REGISTERED

A ring-fenced **Seed Recovery Sleeve** that deploys the TFO Fundamental Desk's
**remaining idle cash** (~USD 42,449 at registration) into a **dual-momentum,
top-2 of 15 liquid asset-class ETFs** book (identical mechanism shape to the
already-registered Growth Book), held as ordinary long positions inside
`reports/fundamental_book.json`, tagged `sleeve=seed_recovery`.

**This is NOT new external capital.** It spends cash the fundamental book already
holds. Book equity is unchanged at the open (cash → marked securities at fill).
The AGO unopened reservation ($40,000 provisional) is **released** so that cash is
not double-committed; AGO may be re-queued later only with a fresh in-session
spread measure and a new size decision.

## 2. WHY THIS MECHANISM (honest standing)

The firm's own research verdict (README / TAYLOR_FAMILY_OFFICE_COMPLETE): **no
validated after-cost alpha**; **buying the index beat every clever allocation**;
crash-managed / simple risk-on is the defensible growth expression. This sleeve
does **not** claim a new edge. It puts idle cash to work in the highest-certainty
risk-on expression the desk already understands, instead of earning only T-bill
yield while the book sits below the $600,000 seed.

**Arithmetic disclosed before any result:** gap to seed was ~$14.4k on ~$585.6k
equity. Closing existing losers does not raise MTM equity. This sleeve alone would
need on the order of **+30%+** to close the seed gap by itself. Concurrent healing
of the large long drags (DATA.L, DECK, NU, LDOS, …) is the other path. Either
outcome is judged on the live record — not on hope.

## 3. FROZEN CONSTANTS — `seed_recovery_sleeve.py`

| | |
|---|---|
| `UNIVERSE` | SPY QQQ IWM EFA EEM TLT IEF LQD HYG GLD SLV DBC USO IYR AGG |
| `SAFE` | IEF |
| `LOOKBACK` | 252 trading days |
| `TOP` | 2 |
| `REBALANCE_TD` | 21 trading days |
| `COST_BPS_SIDE` | 10 |
| `SIDE` | long only |
| `MIN_CASH_BUFFER_USD` | 100 (left in book cash) |
| Kill | firm/book kill imported from `book_kill.py` (−18% DD freezes NEW entries; sleeve still rebalances toward SAFE/cash under halt rules) |
| Clock | **182 days** from first deploy to first verdict (no earlier) |
| Benchmark | cash (T-bill accrual the idle cash would have earned) |

Changing any constant above is a RULE CHANGE and must be registered **before** it
runs, never after a result is known.

## 4. EXIT / REBALANCE

- Monthly (21 trading days): rebalance sleeve-tagged positions only to the dual-
  momentum target weights; non-sleeve fundamental positions are untouched.
- No short expression. No leverage. No broker.
- Main-book 5% net scale-out rule may also act on these names if net gain clears
  the gate — that is additive discipline, not a contradiction.

## 5. KILLS IT (sleeve-level)

1. Sleeve drawdown from its own peak ≤ **−30%** → freeze sleeve, convert to cash
   at next tick (risk kill; not a skill verdict).
2. Firm `HALT_NEW_ENTRIES` → no add-tos; reductions allowed.
3. First skill verdict only on/after **day 182**.

## 6. IMPLEMENTATION

- Script: `seed_recovery_sleeve.py` (`deploy`, `tick`, `--status`)
- Ledger: positions in `reports/fundamental_book.json` with
  `"sleeve": "seed_recovery"`
- Log: `reports/seed_recovery_ledger.jsonl`
- State: `reports/seed_recovery_state.json`

## 7. SEAL

SHA-256 of this file is written to `reports/seed_recovery_state.json` at first
deploy. Any edit of this markdown after seal is a broken registration.
