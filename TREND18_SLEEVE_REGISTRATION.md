# TREND18 sleeve registration (DEEP18 levered risk-parity trend, R2 design). LEVERED. PAPER ONLY.

- **Authority:** Brett Taylor, 3 Oct 2026 17:37 AEST: run DEEP18 as a paper sleeve inside TFO firm equity ("do what is best for the book"). Brett also authorised liquidating existing positions to make room (sell only what is needed, at Monday's real prices; no backdated fills).
- **R2 upgrade:** 3 Oct 2026 18:05 AEST, a TFO decision under the same authority. Spec: `research/deep18/DEEP18_ROUND2_20261003.md` §4. Code: `research/deep18/r2/box_results/code` (`final_r2.py`, `drift.py`, `fam_final.py`). The pre-R2 files are backed up in `backups/trend18_r2_20261003_180503/`.
- **Spec (round 1):** `research/deep18/DEEP18_REPORT_20261003.md` §5.
- **Files:**
  - Code: `trend18.py`.
  - Ledger: `state/trend18.json`.
  - History: `state/trend18_history.csv`.
  - Fills: `state/trend18_fills.csv`.
  - Alerts: `state/trend18_alerts.jsonl`.
  - Log: `state/trend18_log.txt`.
- **Firm integration:**
  - Registered in `book_coverage.registry()` as `trend18` (kind: sleeve). It counts in firm equity, `firm_risk`, `risk_engine` and the funder-fee base (`firm_equity.csv`).
  - Funded only by paired `capital_transfer` receipts with the powder book (`gap_close_levered_book`). Firm capital accounting nets them to zero.
  - `desk_tick.sh` runs `trend18.py --mark` every hour. The mark never trades.

## Signals (frozen)
- **Trend:** 0.5 × [P > SMA200] + 0.5 × [12m return > 12m T-bill return].
- **Weights:** raw = trend / 60-day vol, scaled to 20% portfolio vol on the 60-day covariance.
- **Notional:** ETF notional = exposure / leverage (UPRO 3, TQQQ 3, TMF 3, UGL 2, BTC 1), capped at 97% in total. Target = notional × firm equity × scale.
- **Underlyings:** ^SP500TR, QQQ, VUSTX (TLT fallback), GLD and BTC-USD. The T-bill rate is ^IRX.
- **R2 duration gate:** TMF's raw weight is multiplied by [^TNX < ^TNX 252 trading days earlier], on top of its trend signal and before the vol scaling (as `r2lib.rp2(gates=…)` does it).
  - When the gate is closed, the risk budget goes to the other legs through the vol scaling.
  - The gate is **closed today** (2 Oct close): ^TNX 5.277% vs 4.106% on 1 Oct 2025. TMF is also trend-off.

## Managed-futures basket (R2)
- Idle sleeve cash goes into DBMF, KMLM and CTA in equal thirds. This replaces T-bill accrual.
- **Cap:** scale × (firm equity × (1 − sum of trend notional) − $50k reserve). This is R2's "idle cash above the reserve".
- **Funding:**
  - The basket uses only cash already inside the sleeve. At monthly and refresh rebalances it may also use free powder above the $50k reserve (room freed by exits, such as the 28–30 Oct max-hold exits). `--no-mf-powder` turns that off; `--mf-powder` turns it on for other runs.
  - The basket is **never** a reason to sell another desk position. `--sell-staged` and `--fit` are sized on the trend legs only, and `--fit` drops MF buys first.
- **Shrinks first:** when the trend legs need cash, MF is sold before powder is drawn.
- **Missing prices:** if any of DBMF, KMLM or CTA has no price, the basket is not traded that run. The trend legs still trade.
- **Residual cash:** only the residual sleeve cash ($2k buffer plus rounding) still accrues T-bill interest.

## Execution (R2 next-close semantics: R2's official lag-2 figure)
- **Signal timing:** signals always come from the **prior completed US close**.
- **Monthly rebalance:** `--reason monthly` runs only on the **first US session of the month**. It uses the signal from the last close of the previous month and is run at 15:50 ET.
- **Mid-month refresh:** `--reason refresh` runs only on the **middle US session of the month**: that month's US trading days, element `len // 2` (0-indexed), which is `fam_final.mids`.
  - It trades back to the last stored target weights × current firm equity × scale. It computes no new signal; it only removes drift.
  - The MF basket is re-sized at the same time.
- **Prices:**
  - ETFs: latest IEX trade or quote from Alpaca market data (feed=iex), or the IEX daily close after 16:00 ET.
  - BTC: Kraken (buy at the ask, sell at the bid).
  - `--fetch-prices` (box helper) covers UPRO, TQQQ, TMF, UGL, DBMF, KMLM and CTA, plus the ^TNX gate inputs (yfinance, falling back to Yahoo's chart API).
- **Kill rule:** the only other intra-month trade.

## TFO Risk review fixes (3 Oct 2026 19:10 AEST)
- **Mark sanity (was a blocker):**
  - A mark between 0.6x and 1.6x of the last mark is accepted.
  - A larger move on UPRO, TQQQ, TMF or UGL is accepted when it matches leverage × the move of its 1x underlying. SPY, QQQ, TLT and GLD are fetched with every mark, and the reference is stored at each fill and mark.
  - Without that confirmation (BTC, MF), a drop down to last/10 is accepted with an alert. An unconfirmed jump above 1.6x, or a drop below last/10, is refused.
  - Every large move, refusal or missing price writes `state/trend18_alerts.jsonl`. The dashboard panel shows refused (stale) marks in red.
  - Synthetic test (`research/deep18/trend18_crash_test.py`): on a −20% S&P day the old filter refused TQQQ silently and showed −28.1% with no kill. The new code marks −44.7% and triggers the kill.
- **Kill check:** runs on every mark and at the start of every `--execute`, which re-marks first. A crash since the last hourly mark therefore puts that same rebalance at sigma 10%.
- **Economic exposure:** shown next to notional, for the sleeve and the firm. After the half entry, projected: sleeve $512k notional is about $1.27M economic (2.49x). Firm notional is $1.62M (91% of equity) and **economic about $2.38M (133% of equity)**.
- **Gap risk:** the panel shows the sleeve and firm loss on a −20% S&P / −22% NDX / −15% BTC day at current size.
  - After the half entry: sleeve about −$229k (−45%), firm about −$451k (−25% of equity).
  - Assumptions: levered ETFs move leverage × the index; other books' equities fall 20% (beta 1) and crypto 15%; TMF, UGL and MF are taken as 0.
- **Honest label:** until the MF basket is at least 80% of its target, the live book is shown as **"R1 + bond gate", about 21.0% backtest and P(5y below start) about 43%**. The R2 figures (23.2% / 35%) appear only from 80%.
- **Funding-sale costs:** planning uses the 12.5bp/side model. META costs $97.68 at 12.5bp, against the $2.61 commission that `desk_costs` books. `tfo_ticket` still books `desk_costs` in the fundamental book; trend18 books the difference in the sleeve's `costs_paid_usd` at the execute. That's about $95 for META.
- **Ledger:** the `rule`, `registration` and `label` fields are rewritten to the current text on every save.

## Costs, limits and kill rule
- **Costs (hard rule):** 12.5bp per side on ETFs (MF included) and 25bp per side on BTC, charged in cash on every fill.
- **Limits:**
  - Powder never goes below the $50k reserve.
  - Firm long notional (MF included) never goes above firm equity less the reserve, so there is no leverage.
  - `--fit` scales buys down rather than breach either limit.
- **Kill rule (pre-commitment):**
  - Trigger: NAV/unit 30% below its high-water mark. Sigma drops to 10%, and an intra-month `--reason kill` rebalance becomes pending.
  - Clear: NAV/unit makes a new 6-month high.
  - The kill reference re-arms at the clear (high-water mark since the last clear).
  - A review with Brett is flagged at the trigger, and again when 20% is restored.
  - R2 backtest: neutral (23.7% vs 23.6%).

## Entry and calendar (all times AEDT)
| When (US close) | Run at (AEDT) | Command |
|---|---|---|
| Mon 5 Oct: entry, half size | 06:50 Tue 6 Oct | `python3 trend18.py --execute --reason entry --scale 0.5 --sell-staged --fit` (unchanged; META is the only staged sale; MF ≈ $0 because there is no spare cash) |
| Fri 16 Oct: mid-month refresh | 06:50 Sat 17 Oct | `python3 trend18.py --execute --reason refresh` |
| Mon 2 Nov: rest of entry plus first R2 monthly (signal 30 Oct) | 07:50 Tue 3 Nov (US on EST) | `python3 trend18.py --execute --reason monthly --scale 1.0 --fit` |
| Mon 16 Nov: refresh | 07:50 Tue 17 Nov | `python3 trend18.py --execute --reason refresh` |
| Then: monthly on the first US session, refresh on the middle session | 15:50 ET | as above, without `--scale` |

Refresh dates ahead:

| Month | Refresh | Next monthly |
|---|---|---|
| Dec 2026 | 16 Dec | 1 Dec |
| Jan 2027 | 15 Jan | 4 Jan |
| Feb 2027 | 12 Feb | 1 Feb |
| Mar 2027 | 16 Mar | 1 Mar |

## Honest label and risk
- **What it is:** beta plus published trend premia, not alpha.
- **R2 backtest** (drift-aware, trading at the next close; official): 23.2% CAGR pre-fee over 1988–2026, max DD −34.4%, worst year −23%.
- **Reviewer haircut:** both reviewers expect about 16–17.5% pre-fee going forward, with about a 40% chance of beating the 18.99% hurdle over 5 years. P(below start after 5 years, after fee) is about 34–35%. (Both figures are at the old 0.047619%/day fee.)
- **Funder fee change (Brett, 3 Oct 2026 19:17 AEST):** flat 0.047619%/day → flat **0.0274%/day** (~10%/yr), still daily and scaling with equity, from the Sun 4 Oct 2026 09:00 Sydney charge (3 Oct charge not restated; rate history in `funder_fee.RATE_SCHEDULE`). Annual drag 9.52%; **pre-fee break-even 10.52% CAGR** (was 18.99%). R2 after fee = (1 + gross) × (1 − 0.000274)^365 − 1: holdout 20.7% → **9.2%**, full 22.4% → **10.8%**, 1988–2026 official 23.2% → **11.5%**, reviewers' 16–17.5% → **5.0–6.3%**. The 40% / 34–35% probabilities above were not rerun at the new fee (they will be better).
- **Realistic worst fall: about −50%, not −36%.** Starting the backtest in 1986 puts October 1987 in the sample. R2 then loses −41.6% in October 1987 alone, and max DD becomes −49.4%. A −20% index day costs this book about 20% of NAV or more before any monthly signal can react. The −30% kill rule cannot prevent a one-day gap of that size.
