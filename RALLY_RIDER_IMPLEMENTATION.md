# Rally Rider — Skill & Implementation Document
**Taylor Family Office · PAPER-ONLY · portable replication spec**
Written 2026-08-22 (UTC). Covers `rally_rider` ($10k, live 2026-08-20) and
`rally_rider_x` ($100k breadth variant, live 2026-08-21). This document is
self-contained: everything needed to run the strategy on another machine or
another paper venue, including the honesty machinery that makes the results
trustworthy. **Nothing here is investment advice; the strategy's own prior is
negative (see §8). PAPER ONLY.**

---

## 1. What this is

A volume-confirmed rally strategy with sigma-scaled profit-taking ("trade the
rallies, take profit as we go" — funder-directed). It is deployed as a
pre-registered live counter-test: the backtest family says rally-timing does
not beat holding; these sleeves put that verdict on trial with a live,
tamper-proof record.

## 2. The frozen rule — rally_rider ($10,000)

- **Universe:** SPY QQQ IWM EFA EEM TLT IEF LQD HYG GLD SLV DBC USO IYR AGG
  (the ETF15) + XBT/USD + ETH/USD.
- **Entry** (settled daily closes only): today's settled close is the highest
  close of the trailing 20 trading days AND that bar's volume >= 1.5x the
  median of the PRIOR 20 sessions' volume. Fill books at that settled close.
- **Sizing:** 25% of sleeve equity per position, max 4 concurrent, never
  pyramid an open name.
- **Exits** (the sigma ladder — sigma frozen at entry):
  - scale out 1/3 of initial units when close >= entry*(1 + 2σ√t)
  - scale out another 1/3 when close >= entry*(1 + 4σ√t)
  - remainder exits on trailing stop: close <= peak_close*(1 − 2.5σ)
    (peak = highest SETTLED close since entry), or a 40-trading-bar time stop
  - t = bars held at the deciding settled bar; no exit ever fires on the
    entry bar; TPs are checked before the stop on the same close; every fill
    books at the triggering settled close — never the threshold, never the peak.
- **σ definition:** std-dev of the last ~60 daily simple returns (min 25)
  ending at the entry bar, computed from the SAME settled daily series used
  for marking (yfinance daily auto-adjusted for ETFs; Kraken 1440-min bars for
  crypto, in-progress bar dropped). Frozen at entry, never re-estimated.
  A leg entered without enough history for σ is exit-exempt (time stop only),
  loudly flagged.
- **Costs:** 10 bps/side ETFs, 30 bps/side crypto, charged on EVERY fill
  including each partial exit.
- **Kill:** freeze the sleeve at −30% drawdown. **Evidence:** no verdict
  before 182 days; ranked by return/maxDD, never raw return.

## 3. rally_rider_x deltas ($100,000 — the breadth experiment)

- Universe widened to ~34 deep-liquid names: the ETF15 + XLK XLF XLE XLV XLI
  XLY XLP XLU XLB DIA MDY VNQ EWJ FXI INDA + XBT/USD ETH/USD SOL/USD XRP/USD.
- **10 slots x 10% of equity.** When qualifying signals exceed free slots,
  priority = highest volume-ratio (declared, frozen).
- **v2 accounting:** entries are sized NET OF COSTS (notional + fee <=
  allocation) so cash can never go negative. (v1 sizes gross and can overdraw
  cash by exactly the costs paid — the house set_holdings convention;
  disclosed, harmless, but v2 is cleaner. Use v2 on any new build.)
- Registered hypothesis: same ladder, one new axis — does BREADTH (10
  concurrent rallies, wide field) improve return/maxDD vs concentration (4
  slots, narrow field)? The two sleeves run head-to-head on the same tape.

## 4. The four traps (implementation law — each one has burned a real desk)

1. **Peak harvesting / lookahead.** Walk forward bar by bar; decide at each
   settled close using only information knowable then; book at the CLOSE of
   the triggering session. Never book at the threshold price (assumes a limit
   fill daily data cannot evidence) and never at the peak. A gap through the
   threshold books the full gap close. A peak that touches a level intraday
   without a CLOSE through it does NOT trigger.
2. **The rule that only exists in the backtest.** The exit function must have
   call sites on the LIVE tick path, proven by grep, not just in the lab.
3. **Volatility from the wrong series.** σ comes from the same settled daily
   series the backtest uses. Never from an intraday mark feed, never
   sqrt(252)-annualised from sub-daily returns (a sister desk understated vol
   ~30x this way, making every threshold ~30x too tight).
4. **Uncharged turnover.** Every exit AND every re-entry passes the same cost
   model at the same bps as any other fill. Report gross AND net; headline on
   NET only.

## 5. Verification protocol (run before calling any build live)

- `--selftest` causality proof: a rise-then-collapse synthetic series must
  book at the triggering close (strictly below the peak, different from the
  threshold), and a DELIBERATELY PEEKING evaluator included in the test must
  be detected and failed. A test that cannot fail is decoration.
- **Live replay:** re-fetch fresh settled public data and confirm every
  recorded fill equals the settled close of its bar and genuinely qualified
  (20d closing high, >=1.5x median volume).
- **Hand-recompute costs** from the state file (units x fill x bps).
- **Frozen-file safety:** registrations are append-only (old content must
  remain an exact byte prefix); diffs to shared modules show zero deleted
  lines; sibling sleeves byte-identical.
- Hostile review: a second, independent reviewer re-derives all of the above
  from raw data, assuming the builder is wrong.

## 6. Registration discipline (what makes the record mean anything)

- Pre-register the COMPLETE rule before the first fill; hash-seal the text.
- Rule edits invalidate the entry — any change means a NEW sleeve/clock.
- State the honest prior in the registration (see §8), the kill rule, and the
  182-day evidence bar. Past tape is hypothesis, never evidence.
- Failures must be LOUD: exceptions print an ERROR line and set a state flag;
  a collector/feed schema miss must raise, never return empty success.
- UTC timestamps in all new code (host-local clocks drift and migrate).

## 7. Operating notes (as run on the origin machine)

- Ticks ride the hourly desk heartbeat (`desk_tick.sh` via launchd) —
  entries/exits evaluate at most once per UTC day per position, against the
  latest settled bar.
- State: `reports/tournament/rally_rider.json` / `rally_rider_x.json`
  (positions, entry_px, entry_sigma, peak_close, scaled thirds, event log
  with every fill's price/cost/reason).
- Dashboards: tournament board row + live position rows; the original rider
  has a dedicated cockpit panel (per-leg TP/trail distances, thirds taken,
  bars clock). An hourly watchman task pings the funder's phone on the first
  new exit event (silent otherwise; stops/freezes reported as such, never as
  profits).
- Code on origin: `tournament.py` (tick_rally_rider / tick_rally_rider_x),
  `exit_axis.py` (shared σ + exit conventions, selftest), registration in
  `TOURNAMENT_PREREGISTRATION.md` (2026-08-20 and 2026-08-21 addenda +
  verification disclosures).

## 8. The honest prior (carry this with the strategy, always)

- 582+ momentum/trend trials across the desk family: zero after-cost
  survivors. The spec-rally lab (15y, identical rule family): even liquid
  rally-riding did NOT beat buy-and-hold of its own names (excess t = −2.01);
  spec/small-cap universes were destroyed by spreads (+22%/yr gross → −15%/yr
  net on ASX juniors). Profit-taking (exit axis) tied the null at best after
  turnover. Search count at last audit: ~1,486 hypotheses, one surviving
  mechanism on the whole desk (delta-neutral basis carry).
- These sleeves exist as funder-directed live counter-tests of that prior —
  deployed eyes-open, judged only by their own 182-day record. Early green
  (both riders are green at day 1–2) is NOISE and must be labelled as such.
- First scheduled review: 2026-11-06, with the tournament.

## 9. Replication checklist for a new machine/venue

1. Stand up the settled-daily data feeds (ETF daily adjusted; crypto 1440-min
   bars) and verify the in-progress bar is dropped.
2. Implement §2 (or §2+§3) exactly; wire the §4 laws in from the start.
3. Write and hash-seal the registration BEFORE the first fill (§6).
4. Run the §5 verification protocol; fix the implementation, never the test.
5. Start the clock; report NET only; no verdict before 182 days.
6. If any rule must change: new sleeve, new clock, old record sealed.

*PAPER ONLY. No broker, no credentials, no live order path — on any version,
anywhere.*
