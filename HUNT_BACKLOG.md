# The Hunt — standing backlog of genuinely-distinct, not-yet-tested mechanisms

Rule of the hunt (never relaxed): **exploration is untethered, deployment is tethered.**
Hunt anything. Deploy nothing that fails `quant_validation.py`. Every test raises the
deflation bar for the next. Report the search count. Never fabricate. Propose survivors
to the funder for a hash-sealed forward probe — NEVER auto-deploy.

Historical note: the **1,345** count below was current on 2026-08-17 only. It is
not a valid baseline for new work.

**Current operational floor: at least 3,644 trials (reconciled 2026-09-02).**
Before any new lab, run `strategy_trial_registry.py status`, check its mechanism
ID against the closed registry, and reserve the exact variant count. Use the
reserved `count_after` as the minimum house deflation count. Never hard-code an
older count from this backlog or from a prior report.

## Done — killed with receipts (do NOT re-litigate)
trend/CTA · commodity roll-yield · FX carry · cross-asset carry · positioning (LSR) ·
crypto funding/basis · momentum · mean-rev · breakout · value · sentiment · ML · hash
ribbons · shorts · IPOs · vol/VRP · country/sector L/S · defensive factors ·
overnight/intraday session timing · cross-asset lead-lag · rates-curve trend & carry.
One real mechanism: delta-neutral basis carry (armed). One forward probe live: **cef_discount**.

## Queue — free data, definitively testable COLD (work top-down)
(queue empty — risk-overlay study DONE 2026-08-15; next candidates come from the
data-gated and ripening lists below, or from new funder direction)

## Queue — data-gated (test the DATA path first; never fabricate a feed)
2. ~~Merger / risk arbitrage~~ — **BLOCKED: no data** (2026-08-16). Deal filings are free
   and structured (EDGAR FTS: DEFM14A / SC 14D9 + CIK + date), but acquired targets have
   NO price history on any free feed (ATVI, VMW, TWTR, XLNX, SGEN, HZNP all return zero
   rows) — a backtest would contain only failed and pending deals, i.e. only the losers.
   Offer terms are unstructured prose. Not built. Revisit only with a real delisted-price
   source.
3. ~~Post-earnings-announcement drift (PEAD)~~ — **DONE 2026-08-16, KILLED** (see Log).
4. ~~Index reconstitution (S&P add/delete drift)~~ — **DONE 2026-08-16, KILLED** (see Log).
5. ~~Dispersion (index vol vs single-name vol)~~ — **DONE 2026-08-17, KILLED** (see Log).
   The "needs options chains" blocker was WRONG: CBOE publishes 20y of implied
   correlation free. What is genuinely absent is historical option PRICES.

## Queue — our own / ripening data
6. Futures positioning/flow — **WAITING on the sealed data clocks.** Horizons at or
   below 2h are answered NO and may not be reopened. Run only the predeclared 4h
   time-series family after `futures_panel_archiver.py --gate` reports at least
   27 contiguous days on the liquid-seven median using rows complete for every
   required field. As of 2026-09-02 the honest reading is 15.04 days. The later
   12h cross-sectional family remains separately gated at 36 field-complete days
   with at least 30-symbol breadth (currently 15 days).
7. Kalshi prediction-market mispricing — collector ripening (~21-day bar).
8. ~~Crypto stablecoin depeg mean-reversion~~ — **BLOCKED: phenomenon too small where
   real, not real where big** (2026-08-18, see Log). Cross-venue basis MR remains
   data-gated on ripening futures_analytics.

## Log (append verdicts here)
- 2026-08-18 STABLECOIN DEPEG MR (queue #8) → **BLOCKED with evidence — probe refused to
  build a known-negative lab** ("testing it would be theater"; no variants run, count
  stays 1,345; probe evidence reports/stablecoin_depeg_data_probe.json, script
  stablecoin_depeg_probe.py). FACTS: desk DB holds ZERO USD-stablecoin history (only
  EUR-pegged EURC/USD); Kraken free OHLC caps ~720 bars/interval (2y daily, 30d hourly) —
  no genuine depeg (March-2023-USDC-style) in any obtainable window. The phenomenon is
  TOO SMALL WHERE REAL, NOT REAL WHERE BIG: USDT/USD median deviation 3.1 bps, max CLOSE
  21.5 bps in 2y (36 episodes ≥10 bps, median repeg ~36h) — never past the 30 bps wall;
  USDC max 5 bps. Every ≥50 bps print sits on a dead book (USDD 79% zero-volume hourly +
  credit-impaired peg at 1000-2100 bps for weeks; USDQ 94% zero-volume; USDS/USDG listing
  artifacts). Intrabar wicks (USDT 482 bps) are unfillable-unknowable from OHLC — no L2/
  tape evidence. UNBLOCK PATH (logged, not built): verified maker-tier economics + desk-
  collected USDT/USD trade tape, or external multi-year tick history spanning a real
  depeg event.
- 2026-08-17 DISPERSION / IMPLIED CORRELATION (queue #5) → **KILLED as tradeable; the
  descriptive finding is the keeper** (44 variants, count 1,301→1,345; dispersion_lab.py,
  reports/dispersion_lab.json, reports/research_dispersion.md, probe evidence
  reports/dispersion_data_probe.json). DATA: the backlog's "needs options chains" blocker
  was wrong — CBOE's public daily CSVs
  (cdn.cboe.com/api/global/us_indices/daily_prices/<SYM>_History.csv) carry COR1M/COR3M
  from 2006-01-03, DSPX from 2014, single-name IV (VXAPL/VXAZN/VXGOG/VXIBM/VXGS) and ETF IV
  (VXEEM/VXEWZ/VXFXI/VXSLV/VXGDX/VXTLT) from 2011. Yahoo caps these symbols at 1d/5d. What
  is genuinely absent free is historical OPTION PRICES (yfinance chains = snapshot only),
  so the dispersion trade's own P&L is unreconstructable — the lab refused to fake it and
  split the question. PART A (descriptive, non-overlapping windows, 30-name basket, two
  agreeing realized-correlation measures): the correlation risk premium is REAL with a TERM
  STRUCTURE — COR3M +7.21 correlation points, t 4.26, era-stable (+9.9/+6.0/+6.0, all t>2),
  still alive in 2020-26; COR1M is DEAD at the tenor the crowd trades — full-sample +2.71
  t 2.55 is entirely pre-2013 (2006-12 +9.45 t 4.82 → 2013-19 +0.99 t 0.53 → 2020-26 −1.65
  t −1.15, mildly NEGATIVE). PART B (44 tradeable variants, real prices, 5/25bps, borrow
  1%/3%): best regime_dspx_z_c5 net Sh 0.781, DSR 0.026 vs 0.280 required, t 1.228, n_eff
  128.4, WRC p=0.116 FAILS, beta +0.27 — and it LOSES TO BUY-AND-HOLD SPY (CAGR 7.89 vs
  14.19, excess t −0.68), the overnight-lab shape again. volcarry_* (short VIXY) posted the
  lab's best raw CAGR (+13.0%) on −81% to −92% maxDD — the SVXY family PERMANENTLY BARRED by
  TOURNAMENT_PREREGISTRATION; bar re-earned, not deployable at any score. Both dollar-neutral
  cross-sections (the delta-one cousins of a real dispersion book) die at honest costs: every
  XS variant is −0.93 to −1.17 net Sharpe at the 25bps tier, rankings monotone in cost tier —
  the PEAD cost-artefact diagnostic again. Gate NOT invoked (fails survives_deflation and
  t>=2 of 5 conditions); no probe written. STRUCTURAL CONCLUSION: the premium is real and
  unreachable — it lives inside option prices, and the only door back in is option-level
  execution, already priced shut by the 2026-08-16 maker/colo briefing. Carry forward: COR3M
  is a free 20y regime series better used as a risk input than a trade. AUDIT CATCHES (self,
  mid-run, both fixed pre-receipt): (a) regime family charged switch cost as ONE leg when
  SPY→IEF is a sell AND a buy — turnover ×2 when an alt instrument is held, drag 0.87→1.75bps,
  best netSh 0.827→0.781, verdict unchanged; (b) no buy-and-hold benchmark on a long-equity
  timing family — added, and it produced the decisive kill line. Cosmetic recurrence (3rd lab):
  clears_cost_wall still prints crypto venue labels on an equity receipt.
- 2026-08-16 INDEX RECONSTITUTION (queue #4) → **KILLED** (14 variants, count 1,287→1,301;
  workflow probe→build→audit, audit CLEAN; index_recon_lab.py, reports/index_recon_lab.json,
  reports/research_index_recon.md, probe evidence reports/index_recon_data_probe.json).
  DATA: honest narrow scope only — Wikipedia changes table via PINNED revision
  oldid=1329075976 (the live 2026 article DELETED the table); no announcement dates
  anywhere free, so the literature's announcement→effective drift is UNTESTABLE; deletions
  blocked (35% coverage, survivors-only, ticker recycling); additions leak 19.2% (59/308
  unpriceable, enumerated — ENDP/SIVB/TWTR-type purges). Ran additions-only, effective-
  date-anchored, 2011–2025: 249 events → 195 clustered dates. DESCRIPTIVE FINDING (real):
  the famous +3–8% inclusion pop is not merely decayed post-2010 — it is mildly REVERSED:
  post-inclusion abnormal returns NEGATIVE in every window (h5 −91bps naive t −2.82, h20
  −135bps). Index-tracking money now fully pre-positions the add. Best tradeable variant
  (short h5, 5bps): +78bps net/event, naive t 1.97 — DSR 0.000 at 1,301 trials, WRC p=0.33
  → indistinguishable from search selection. Era-unstable 4–8x magnitude swings. No
  deployable edge; gate not invoked. Audit notes: short borrow not modeled (immaterial to
  a kill; required before any future short-side claim); crypto cost-wall labels again in
  an equity receipt (cosmetic, recurring — fix the lab template someday).
- 2026-08-16 MAKER STATUS & COLOCATION BRIEFING (funder-posed) → **DO NOT PURSUE** at
  family-office scale (reports/research_maker_colo.md; 3 research tracks + verifier,
  10/10 load-bearing prices independently confirmed). Surprise finding: colocation is NOT
  the crypto moat — Kraken sells ~200μs proximity to individuals (Beeks, ~$75-350/mo all-in
  stack, depth data free); the moat is the FEE LADDER (maker ≤1.08bps/side needed for the
  2.16bps rent to survive → only the $10M+/30d zero-fee tier qualifies; base is 40bps) and
  the QUEUE (latency-rank evidence: slow makers eat toxic fills). US equities seat:
  verified published floor ~$114-213k/yr (Nasdaq colo+ITCH), realistic all-in ~$375-685k/yr
  + 9-15mo regulatory runway. Break-even needs $0.4-13M/day of captured maker flow vs
  named firms doing $10-15B/day. Honest niches (token-project mandates; long-tail pairs)
  are a services business or an inventory trap, not a desk strategy. No research count
  change (no backtest variants). Verdict from tape_scalp_lab stands, now fully priced.
- 2026-08-16 TAPE SCALPING (funder-posed: "sit on the tape, scalp the finest margins") →
  **KILLED — and the kill is the cleanest structural finding of the hunt** (24 variants,
  count 1,263→1,287; workflow build→audit, audit CLEAN first pass; tape_scalp_lab.py,
  reports/tape_scalp_lab.json, reports/research_tape_scalp.md). The desk's own 5.0M-trade
  tape (3 disjoint-era symbols, 1,640 days, 2013–2022) contains REAL, overwhelming
  short-horizon structure (bounce-reversal autocorr t to −61, flow→next-bar IC t +16.5) —
  but the best gross edge is **+2.16 bps per round trip** vs the desk's **30 bps** wall:
  the wall is ~14x the edge. Even the FANTASY zero-cost tier fails deflation (DSR 0.000).
  Best real-tier net: −28.45 bps/trade, t=−80, WRC p=1.000. The bounce IS the bid-ask
  spread — a market-maker's rent that a public-API taker pays, never earns. Only a
  structural cost change (maker status + low latency) reopens this family; no signal
  research can. Caveats honest: 3 symbols, disjoint eras, newest tick 2022-04.
- 2026-08-16 MERGER/RISK ARB (queue #2) → **blocked: no data**. Data path probed, not
  built. EDGAR gives deal filings free; no free feed gives the price path of a COMPLETED
  deal (every acquired target returns zero rows), so any backtest would be a losers-only
  sample. Deal terms unstructured. No variants run, search count unchanged by this item.
- 2026-08-16 PEAD (queue #3) → **KILLED** (36 variants, count 1,227→1,263; receipt
  reports/pead_lab.json, note reports/research_pead.md, lab pead_lab.py). Data path built
  honestly and free: 8-K **item 2.02** dates from EDGAR submissions + **point-in-time**
  XBRL EPS (earliest-filed instance = as-reported; the xbrl/frames endpoint was PROBED and
  REJECTED because its records carry restated values filed years after the period). 377
  names, 18,723 events, 2010-07..2026-08. Killed on SIGNAL, not costs: across 18,422
  announcements the SUE→next-21d Spearman IC is **0.0113, t=1.54** — the textbook effect is
  not detectably present. Best portfolio variant (CAR3, 60d window, 5bps, 0.25% borrow):
  gross Sh 0.646 → net 0.388, DSR 0.247 vs 0.283 required, t 0.459, n_eff 16.8, WRC
  p=0.396 FAILS, beta→SPY +0.042. Gate NOT invoked (fails 4 of 5 conditions); no probe
  written. Two honest findings: (a) CAR3 beats SUE at every setting — the market's own
  3-day reaction carries more forward information than the GAAP surprise that caused it;
  (b) the universe is survivorship-biased UPWARD and it still failed, which hardens the
  kill. All variant rankings are monotone in the cost tier — a 5bps-only edge is a cost
  artefact. AUDIT CATCH #5 (self, mid-run): first pass printed net/trade ABOVE gross/trade
  (negative cost drag) because vol-target leverage was applied to net but not to the gross
  series passed to evaluate(); fixed by scaling both (gross 53.9 vs net 32.4 bps), verdict
  unchanged. Same code shape exists in cef_discount_lab.py (imported at runtime by the
  DEPLOYED cef_probe.py) — currently harmless there (its vol exceeds target so its scaling
  de-levers; drag reads +42.7, and every other lab receipt shows a positive drag), so it is
  a latent presentation bug that would only bite a future low-vol candidate. NOT edited —
  sealed registration, deployed probe. Flagged for Brett.
- 2026-08-14 cef_discount → forward probe DEPLOYED (failed in-sample deflation t=1.20,
  earning significance out-of-sample; verdict 2027-02-12).
- 2026-08-14 TRIPLE SWEEP (workflow: build → adversarial audit → fix; 48 variants, count
  1,179→1,227; receipts reports/{overnight,leadlag,rates_curve}_lab.json):
  - overnight/intraday → KILLED. Decomposition CONFIRMED and large (SPY +10.2%/yr overnight
    vs +1.9% intraday; QQQ +14.1 vs −0.1) but overnight-only does NOT beat buy-and-hold
    (excess −1.1 bps, t=−0.74) and 2 trades/day fails the cost wall. The return is the
    market delivered while you sleep — a fact, not an edge.
  - cross-asset lead-lag → KILLED. All predictive ICs vs next-month SPY statistically zero
    (HYG, copper, oil, curve: |t|<1.2). Only context finding: curve steepening predicts
    BONDS (IEF, IC +0.121, t=2.06) — logged for the rates family, not tradeable alone.
  - rates-curve trend & carry → KILLED. Best (12m TSMOM IEF) doesn't beat IEF buy-and-hold
    (excess −4.6 bps). Real descriptive finding: 12m trend mechanically dodged the 2022
    bond crash (0.0% vs TLT −34%) — crash INSURANCE, not alpha; slope-carry rode the crash
    down. Dies on signal, not costs.
- 2026-08-15 RISK-OVERLAY STUDY → DONE (workflow build→audit→fix; overlay_lab.py,
  reports/overlay_lab.json, reports/research_overlay.md). Firm across 24 books /
  $566,381 capital (95% mapped): vol 5.94%, VaR99 -1.09% (~$6,185), beta 0.22,
  effective bets 5.7 risk-based. HEADLINE: growth_book (SLV+USO) carries 35.2% of firm
  RISK on 9.5% of capital; top-5 risk books hold 75% of risk on 34% of capital;
  floors+replicas re-run one strategy family on 42.8% of capital. ERC is the lowest-risk
  construction on every metric (vol 3.31%, beta 0.10). A 10% vol-target LEVERS ~1.5x in
  the current regime (amplified 2020/2022 replays; throttled+cushioned 2008) — only the
  CAP form min(1, target/vol) is recommended for consideration. Frozen registrations
  untouched; output is a decision MENU for the funder. AUDIT CATCH #3: builder's note
  claimed vol-target "worsened every crash" — falsified by its own 2008 replay
  (-13.50%/-11.04% vs -13.99%); corrected before reaching the funder.
- 2026-08-15 EXPOSURE DIAL → BUILT & LIVE (funder-directed, menu item 2 of the overlay
  study; registration EXPOSURE_DIAL_REGISTRATION.md, spec 21e05244c53d4e77). Cap form
  only: scale=min(1, 10%/trailing-63d firm vol) — de-levers, never levers. First reading
  scale 1.00 FULL (firm vol 6.07%). Wired: exposure_dial.py in desk_tick (daily compute,
  hourly no-op), dial card on /report (dashboards healer-restarted fresh), autonomous
  gate TIGHTENED fail-closed (refuses new probes when scale<0.80 / stale / unreadable),
  claude_discretionary adopted it as binding practice. Frozen books untouched; the dial
  can only ever reduce risk-taking. AUDIT CATCH #4 (adversarial review of the build):
  silent DEGRADED-data fail-open — partial yfinance/Kraken failure would zero-fill
  missing books, understate vol, and write a fresh green FULL. Hardened same day:
  DEGRADED state (missing weight-bearing instruments or mapped capital <90%) never
  opens the gate; scale outside (0,1] fails closed; unique tmp names fix a concurrent-
  write race; dial_cache gitignored. Registration carries the dated tighten-only
  addendum. 9 dial unit tests; suite fully green at 74/74 (the stale PUMP assertion in
  test_firm_modules updated for the KRK: fallback design).
- 2026-08-15 claude_discretionary → DEPLOYED (funder-directed capability experiment,
  spec 64f97dc712aa4c77, $10k, -30% kill, 182d clock, verdict 2027-02-13). Discretionary
  head-trader book; NOT an edge claim; all changes logged with rationale.
- 2026-08-14 LESSON (audit catch): qv.whites_reality_check takes strategies in ROWS.
  Three labs (rates_curve primary key, cross_asset_carry, cef_discount) had transposed
  calls → spurious WRC fields. All fixed, receipts regenerated. Corrected: cross-asset
  carry WRC p=0.556 FAILS (its "raw significance" claim retracted — kill even cleaner);
  cef_discount WRC p=0.018 still SURVIVES (deployed probe's premise intact); rates_curve
  p=0.058 fails. Harness evaluate() verdicts were never affected.
