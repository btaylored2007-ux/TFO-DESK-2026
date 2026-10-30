# Shark exit rule: V7 paper forward test (Brett approved 2 Oct 2026, 17:16 AEST). PAPER ONLY

**Start:** 2 Oct 2026 (AEST).
**Applies to:** every NEW shark clip opened through `tfo_buy.py` from 2 Oct 2026. These are equity clips in `fundamental_book`. Each new position and its BUY ticket are tagged `exit_variant="V7"` and `exit_variant_start="2026-10-02"`.
**Does not apply to:**
- positions open before 2 Oct 2026, which stay on the current rail (V0);
- HT crypto clips (`tfo_crypto.py`), which stay on V0;
- any other book.

**Sizing:** unchanged. See SHARK_SIZING_RULE.md: risk 1% to the -5% stop, 100% invested cap (raised from 80% on 2 Oct 2026; no leverage, $50k reserve kept), max 2 entries per day.

## V7 (live rail for tagged clips)
- **Stop:** -5% from the original fill. Shorts would use -4%, but `tfo_buy` opens longs only.
- **Half-take:** half at **+10%** from the original fill (rule `HALF_10PCT`). There is no +2% half.
- **Residual after the half:** the existing trail, max(3%, 3 x ATR14/price) off the high-water mark, floored at breakeven. The original-fill stop still applies.
- **Max hold:** **40 sessions** after the entry session, for the whole position, whether or not the half has been taken (rule `MAX_HOLD_40S`).
  - Sessions are counted as weekdays in the venue's local calendar. Exchange holidays are not removed, so the max hold can fire one or two sessions early.
- **Execution:** exits fire only through the normal `tfo_ticket.py` rail path, with the same print, bad-tick, auction and 5-minute guards, the same costs, and the same HALF/STOP/FLAT ticket vocabulary.

## V0 (current rail, unchanged; run as a shadow for every V7 trade)
- -5% stop until the +2% half.
- Then the residual trails max(3%, 3 x ATR14) with a breakeven floor.
- 30-calendar-day max hold on the residual after the half. As coded, there is no max hold before the half.

## Evidence (engine60 Phase 1)
Event study of 5,638 US earnings-gap events, 2004–26, at 10bp round-trip costs. Source: `research/engine60/REPORT_phase1.md`.

| Variant | Expectancy/trade | Out-of-sample Sharpe 2016–26 | In-sample Sharpe 2005–15 |
|---|---|---|---|
| V7 | 0.91% | 0.51 | 0.61 |
| V0 | 0.28% | 0.24 | 0.53 |

Caveats:
- Neither variant shows statistically significant alpha vs SPY.
- The universe has survivorship bias.
- Hence this is a forward test, not an adopted rule.

## Shadow A/B tracker
`shark_exit_ab.py` runs inside every `tfo_ticket.py --execute` cycle. It is report-only: it never writes a book and never sends a ticket.

It simulates V0 on the same executable prints for every V7-tagged trade. Shadow returns are gross; the actual V7 result is reported gross and net of fees.

| File | Contents |
|---|---|
| `reports/shark_exit_ab.jsonl` | Events: open, shadow_half, shadow_exit, actual_exit, trade_complete (V7 actual vs V0 shadow % and the difference) |
| `reports/shark_exit_ab_summary.json` | Latest snapshot for each trade, plus averages over completed trades |
| `reports/shark_exit_ab_state.json` | Shadow state |
| `reports/shark_scorecard.jsonl` | One line per completed trade (`source=shark_exit_ab`, `action=exit_ab_complete`) |

## Review
Review with the 4-week shark scorecard, around **31 Oct 2026**. Compare per-trade V7 actual vs V0 shadow (mean, median, hit rate, worst trade) and the count of max-hold exits. With few trades, treat the result as indicative only.

## Revert (minimal)
1. Set `SHARK_EXIT_VARIANT = None` in `tfo_buy.py`. New clips then go back on V0.
2. Already-tagged positions keep V7 until they close. To move them back, remove `exit_variant` from those positions under the ledger lock.
3. Pre-change copies of the code are in `backups/shark_v7_<timestamp>/`.

## Crypto exit variant C3 (2 Oct 2026, PAPER forward test; Brett: "your call, maximise desk profits")
Applies only to NEW HT crypto clips bought via `tfo_crypto.py buy` (tagged `exit_variant="C3"`, `c3_stop_dist`, `c3_atr14_entry`).
Existing clips (SOL/USD, HBAR/USD) are untagged and keep the default crypto rail (V0: -5% / +2% half / 3xATR trail / 30d).
- Stop: 2 x Kraken daily ATR14% at entry below the original fill; fixed for the life of the clip. No ATR at entry: the buy fails closed.
- Half: +15% from the original fill (`HALF_15PCT`).
- Residual: trail max(3%, 3 x ATR14) off the high-water mark, floored at breakeven; original stop kept.
- Max hold: 40 calendar days from fill, whole position (`MAX_HOLD_40D`).
- Sizing: risk 1% of firm equity to the 2xATR stop (gate: usd x stop <= 1% equity; the gate reports the max clip). Heat pre-half = stop distance x notional.
- Evidence: engine60 crypto exit backtest (Binance 1h, 16 majors, 2019-2026, 0.18%/side). Breakout entries OOS 2023-26: V0 CAGR 1.0% / Sharpe 0.14;
  C3 (A3) 15.8% / 0.78; in-sample 2019-22 Sharpe 1.57 vs 0.24. Surge entries OOS: V0 -5.5% / -0.34; C3 9.0% / 0.52.
- A/B: `crypto_exit_ab.py` (called after each live `rails --execute`) runs a V0 shadow on the same live bids:
  `reports/crypto_exit_ab.jsonl`, `_state.json`, `_summary.json`; one `exit_ab_complete` line (source=crypto_exit_ab) in `reports/shark_scorecard.jsonl` per trade.
- Revert: set `CRYPTO_EXIT_VARIANT = None` in tfo_crypto.py (new clips untagged); backups in `backups/crypto_c3_20261002-173757/`.
