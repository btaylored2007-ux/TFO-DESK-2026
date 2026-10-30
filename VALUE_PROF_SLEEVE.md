# Value + profitability sleeve, 20% of firm equity (Brett approved 2 Oct 2026). PAPER ONLY. STAGED

**Approved:** Brett, 2 Oct 2026: crypto stack cap $500k -> $1M plus a 20% value+profitability sleeve, NO leverage.
Research basis: `research/engine60/REPORT_strategy_sweep_20261002.md`, proposal A0 "Crypto cap $500k -> $1M, plus value 20%"
(12.3% CAGR median of seeds, p10-p90 8.8-16.3%, maxDD -27%, 2019-02..2026-09; out-of-sample 2023-26 9.5%; rests on the
crypto bull; long history 2011-26 without crypto was 4-7%). Same-day addition (Brett): desk invested cap 80% -> 100%.

**Status:** STAGED TARGET. `value_prof_sleeve.py target --write` computes and logs the target. Nothing executes: no cash
moves, no positions open. The 12 existing fundamental_book positions and SOL/HBAR are untouched.

## Definition (same as the backtest; `research/engine60/code/equity_factor.py`, run_valprof.py weights)
- Universe: current S&P 500 + S&P 400 members (`research/engine60/data/universe.csv`).
- Eligible: price >= $5, 60-day median dollar volume >= $20M, 252 sessions of history.
- Score: mean of within-sector percentile ranks of gross profit / assets (financials excluded) and book / market (equity > 0).
- Buy the top quintile; keep a held name while it stays in the top 40%.
- Weights: inverse 60-day volatility, per-name cap max(2%, 1/N).
- Cadence: signal at the US month-end close, trade at the next US session close. Monthly.
- Size: 20% of firm equity, topped up or trimmed monthly from cash. Buys limited to the room under the 100% invested cap,
  firm cash minus the $50k reserve, and unreserved powder minus the $50k reserve. Never leverage.
- Desk overlays (remove names only): never re-enter list (tfo_ticket.CLOSED_NAMES, e.g. RCL), names already held in
  fundamental_book (no twin, no averaging down), tfo_buy.SEMIS while the semis theme is full.
- No -5% stop / V7 / C3 rail on sleeve names: the backtest has none; exits are the monthly rebalance.

## Deployment
- Next signal: Fri 30 Oct 2026 US close. Next trade: Mon 2 Nov 2026 US close (Tue 3 Nov ~08:00 AEDT).
- Until execution is built and approved, each month only writes `reports/value_prof_sleeve/target_latest.json` and
  appends `target_log.jsonl`.

## What remains before it trades
1. A registered paper ledger `value_prof_sleeve` in book_coverage (hash-sealed like the other books), funded by paired
   capital transfers from powder (`gap_close_levered_book`), as tfo_crypto.xfer does.
2. Execution through the desk quote layer and desk_costs (regular-session executable prints only).
3. A monthly launchd job (or desk_tick hook) for target + rebalance.
4. Brett's go to switch from STAGED to EXECUTE.

## Revert
Delete `value_prof_sleeve.py`, `reports/value_prof_sleeve/`, this file and `tests/test_crypto_cap_value_sleeve.py`,
or restore `backups/crypto_cap_value_<timestamp>/`.
