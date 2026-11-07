# Coverage log — conceptual review of unpublished v1.2.0, resulting v1.2.1

Review date: 7 November 2026, Asia/Makassar (UTC+8).
Reviewer: AI conceptual pass only. Not expert, legal or compliance review.
Scope: semantically read all 287 question stems, keys and each option rationale;
compare all 21 practical prompts with their rubrics and the capstone list;
read high-risk lessons in full and claim-scan the rest of the 126 lesson bodies.
This is not a count-based comprehensive claim and not a course-complete mark.

## Counts (unchanged structure)
- Modules: 21
- Lessons: 126 (all IDs preserved)
- Objectives: 97
- Questions: 287 (A 215, B 72)
- Keys changed vs v1.2.0: none
- Rubric criterion IDs changed: none

## Lesson-read method
- Full prose read (23 lessons): tts.m01.l3, tts.m01.l4, tts.m02.l6, tts.m04.l1, tts.m04.l4, tts.m05.l4, tts.m09.l4, tts.m11.l2, tts.m11.l4, tts.m12.l2, tts.m12.l6, tts.m14.l4, tts.m16.l1, tts.m16.l6, tts.m17.l3, tts.m18.l5, tts.m18.l6, tts.m19.l2, tts.m19.l6, tts.m20.l1, tts.m20.l6, tts.m21.l5, tts.m21.l6
- Remaining 105 lessons: claim-scanned for numerical/legal rates, always/never/guarantee language,
  APRA/ISM/franking/Basel/VaR wording, and unlabelled fictional figures. That is not the same
  depth as a line-by-line literary edit of every paragraph. Residual pedagogical roughness
  in those 105 bodies is a remaining limit, not a hidden pass.

## Per-module log
### tts.m01 How Markets Work
- QIDs reviewed (17, A=11 B=6): tts.m01.q01, tts.m01.q02, tts.m01.q03, tts.m01.q04, tts.m01.q05, tts.m01.q06, tts.m01.q07, tts.m01.q08, tts.m01.q09, tts.m01.q10, tts.m01.q11, tts.m01.qb01, tts.m01.qb02, tts.m01.qb03, tts.m01.qb04, tts.m01.qb05, tts.m01.qb06
- Objectives reviewed: tts.m01.lo1, tts.m01.lo2, tts.m01.lo3, tts.m01.lo4
- Lessons: tts.m01.l1, tts.m01.l2, tts.m01.l3, tts.m01.l4, tts.m01.l5, tts.m01.l6
- Issues / fixes: Fixed m01.q03:d overclaim that market orders guarantee execution. Added settlement-cycle/time-zone ask so session_settlement matches the prompt. Remaining: auction 'maximises matched volume' is the standard teaching simplification (venues also use imbalance/reference-price tie-breaks).
- Remaining confidence: medium-high on quiz keys and prompt/rubric alignment; not expert-certified.

### tts.m02 Shares, ETFs and Indices
- QIDs reviewed (11, A=11 B=0): tts.m02.q01, tts.m02.q02, tts.m02.q03, tts.m02.q04, tts.m02.q05, tts.m02.q06, tts.m02.q07, tts.m02.q08, tts.m02.q09, tts.m02.q10, tts.m02.q11
- Objectives reviewed: tts.m02.lo1, tts.m02.lo2, tts.m02.lo3, tts.m02.lo4
- Lessons: tts.m02.l1, tts.m02.l2, tts.m02.l3, tts.m02.l4, tts.m02.l5, tts.m02.l6
- Issues / fixes: Rewrote instrument_fit rubric text (id kept) to grade fees, domicile and top-10 concentration rather than an unasked leveraged-ETF suitability essay. No key issues. No pool B (explicit teaching limitation).
- Remaining confidence: medium-high on quiz keys and prompt/rubric alignment; not expert-certified. Pool B absent — explicit teaching limitation, not scored as PASS and not treated as an automatic launch block.

### tts.m03 Returns, Compounding and Honest P&L
- QIDs reviewed (17, A=11 B=6): tts.m03.q01, tts.m03.q02, tts.m03.q03, tts.m03.q04, tts.m03.q05, tts.m03.q06, tts.m03.q07, tts.m03.q08, tts.m03.q09, tts.m03.q10, tts.m03.q11, tts.m03.qb01, tts.m03.qb02, tts.m03.qb03, tts.m03.qb04, tts.m03.qb05, tts.m03.qb06
- Objectives reviewed: tts.m03.lo1, tts.m03.lo2, tts.m03.lo3, tts.m03.lo4
- Lessons: tts.m03.l1, tts.m03.l2, tts.m03.l3, tts.m03.l4, tts.m03.l5, tts.m03.l6
- Issues / fixes: Added ledger drawdown/recovery ask so drawdown_recovery matches the prompt. Numeric keys recomputed and sound. Many inherited short numeric rationales remain.
- Remaining confidence: medium-high on quiz keys and prompt/rubric alignment; not expert-certified.

### tts.m04 Interest Rates, Central Banks and Bonds
- QIDs reviewed (17, A=11 B=6): tts.m04.q01, tts.m04.q02, tts.m04.q03, tts.m04.q04, tts.m04.q05, tts.m04.q06, tts.m04.q07, tts.m04.q08, tts.m04.q09, tts.m04.q10, tts.m04.q11, tts.m04.qb01, tts.m04.qb02, tts.m04.qb03, tts.m04.qb04, tts.m04.qb05, tts.m04.qb06
- Objectives reviewed: tts.m04.lo1, tts.m04.lo2, tts.m04.lo3, tts.m04.lo4, tts.m04.lo5
- Lessons: tts.m04.l1, tts.m04.l2, tts.m04.l3, tts.m04.l4, tts.m04.l5, tts.m04.l6
- Issues / fixes: Fixed m04.q04:b FRN duration rationale (time-to-next-reset, not unconditionally near zero). Keys sound. APRA/CET1 numerical minima were already qualified in lessons and were not invented here.
- Remaining confidence: medium-high on quiz keys and prompt/rubric alignment; not expert-certified.

### tts.m05 Foreign Exchange
- QIDs reviewed (16, A=10 B=6): tts.m05.q01, tts.m05.q02, tts.m05.q03, tts.m05.q04, tts.m05.q05, tts.m05.q06, tts.m05.q07, tts.m05.q08, tts.m05.q09, tts.m05.q10, tts.m05.qb01, tts.m05.qb02, tts.m05.qb03, tts.m05.qb04, tts.m05.qb05, tts.m05.qb06
- Objectives reviewed: tts.m05.lo1, tts.m05.lo2, tts.m05.lo3, tts.m05.lo4, tts.m05.lo5
- Lessons: tts.m05.l1, tts.m05.l2, tts.m05.l3, tts.m05.l4, tts.m05.l5, tts.m05.l6
- Issues / fixes: Rewrote fx_arithmetic/exposure_intent evidence to the asked cross and unhedged decomposition. Numeric inverses/crosses/compounding recomputed and sound.
- Remaining confidence: medium-high on quiz keys and prompt/rubric alignment; not expert-certified.

### tts.m06 Commodities
- QIDs reviewed (10, A=10 B=0): tts.m06.q01, tts.m06.q02, tts.m06.q03, tts.m06.q04, tts.m06.q05, tts.m06.q06, tts.m06.q07, tts.m06.q08, tts.m06.q09, tts.m06.q10
- Objectives reviewed: tts.m06.lo1, tts.m06.lo2, tts.m06.lo3, tts.m06.lo4
- Lessons: tts.m06.l1, tts.m06.l2, tts.m06.l3, tts.m06.l4, tts.m06.l5, tts.m06.l6
- Issues / fixes: Clarified m06.q03 stem (producer unit cash margin, not futures margin); key unchanged (b). Added one producer-versus-futures sentence so exposure_type is asked. No pool B.
- Remaining confidence: medium-high on quiz keys and prompt/rubric alignment; not expert-certified. Pool B absent — explicit teaching limitation, not scored as PASS and not treated as an automatic launch block.

### tts.m07 Crypto-Assets
- QIDs reviewed (10, A=10 B=0): tts.m07.q01, tts.m07.q02, tts.m07.q03, tts.m07.q04, tts.m07.q05, tts.m07.q06, tts.m07.q07, tts.m07.q08, tts.m07.q09, tts.m07.q10
- Objectives reviewed: tts.m07.lo1, tts.m07.lo2, tts.m07.lo3, tts.m07.lo4, tts.m07.lo5
- Lessons: tts.m07.l1, tts.m07.l2, tts.m07.l3, tts.m07.l4, tts.m07.l5, tts.m07.l6
- Issues / fixes: Expanded m07.q10 a/b/c scam-indicator rationales. Retargeted custody_checklist to the five Moneysmart risks the prompt asks. No pool B.
- Remaining confidence: medium-high on quiz keys and prompt/rubric alignment; not expert-certified. Pool B absent — explicit teaching limitation, not scored as PASS and not treated as an automatic launch block.

### tts.m08 Derivatives and Leverage
- QIDs reviewed (17, A=11 B=6): tts.m08.q01, tts.m08.q02, tts.m08.q03, tts.m08.q04, tts.m08.q05, tts.m08.q06, tts.m08.q07, tts.m08.q08, tts.m08.q09, tts.m08.q10, tts.m08.q11, tts.m08.qb01, tts.m08.qb02, tts.m08.qb03, tts.m08.qb04, tts.m08.qb05, tts.m08.qb06
- Objectives reviewed: tts.m08.lo1, tts.m08.lo2, tts.m08.lo3, tts.m08.lo4, tts.m08.lo5
- Lessons: tts.m08.l1, tts.m08.l2, tts.m08.l3, tts.m08.l4, tts.m08.l5, tts.m08.l6
- Issues / fixes: Critical rubric fix: 10:1 of A$20,000 with a 50% close-out is a 5% notional move (half of A$2,000 margin), not the 10% full wipeout the old rubric described; futures_quantity id retained but now grades that CFD arithmetic. ASIC 2020/986 s7(2)(d) 50% crypto initial margin left as key b and qualified against a search-extract of the instrument.
- Remaining confidence: medium-high on quiz keys and prompt/rubric alignment; not expert-certified.

### tts.m09 Liquidity, Execution and Trading Costs
- QIDs reviewed (16, A=10 B=6): tts.m09.q01, tts.m09.q02, tts.m09.q03, tts.m09.q04, tts.m09.q05, tts.m09.q06, tts.m09.q07, tts.m09.q08, tts.m09.q09, tts.m09.q10, tts.m09.qb01, tts.m09.qb02, tts.m09.qb03, tts.m09.qb04, tts.m09.qb05, tts.m09.qb06
- Objectives reviewed: tts.m09.lo1, tts.m09.lo2, tts.m09.lo3, tts.m09.lo4, tts.m09.lo5
- Lessons: tts.m09.l1, tts.m09.l2, tts.m09.l3, tts.m09.l4, tts.m09.l5, tts.m09.l6
- Issues / fixes: Retargeted carry_and_tax_caveat to the asked slippage-versus-ADV comparison. ATO 12-month CGT item (m09.qb03) already qualified in M09.l4; not re-litigated as a new rate.
- Remaining confidence: medium-high on quiz keys and prompt/rubric alignment; not expert-certified.

### tts.m10 Reading Financial Statements
- QIDs reviewed (10, A=10 B=0): tts.m10.q01, tts.m10.q02, tts.m10.q03, tts.m10.q04, tts.m10.q05, tts.m10.q06, tts.m10.q07, tts.m10.q08, tts.m10.q09, tts.m10.q10
- Objectives reviewed: tts.m10.lo1, tts.m10.lo2, tts.m10.lo3, tts.m10.lo4
- Lessons: tts.m10.l1, tts.m10.l2, tts.m10.l3, tts.m10.l4, tts.m10.l5, tts.m10.l6
- Issues / fixes: Added 'cite report pages' so earnings_quality evidence is asked. Numeric ratio keys sound. No pool B. Several inherited 'No.' numeric rationales remain.
- Remaining confidence: medium-high on quiz keys and prompt/rubric alignment; not expert-certified. Pool B absent — explicit teaching limitation, not scored as PASS and not treated as an automatic launch block.

### tts.m11 Valuation and Unit Metrics
- QIDs reviewed (10, A=10 B=0): tts.m11.q01, tts.m11.q02, tts.m11.q03, tts.m11.q04, tts.m11.q05, tts.m11.q06, tts.m11.q07, tts.m11.q08, tts.m11.q09, tts.m11.q10
- Objectives reviewed: tts.m11.lo1, tts.m11.lo2, tts.m11.lo3, tts.m11.lo4, tts.m11.lo5
- Lessons: tts.m11.l1, tts.m11.l2, tts.m11.l3, tts.m11.l4, tts.m11.l5, tts.m11.l6
- Issues / fixes: Franking item m11.q03 states the 30% imputation rate in the stem (formula, not a universal legal rate). Sensitivity rubric now grades the three stated r scenarios (8/9/10%). No pool B.
- Remaining confidence: medium-high on quiz keys and prompt/rubric alignment; not expert-certified. Pool B absent — explicit teaching limitation, not scored as PASS and not treated as an automatic launch block.

### tts.m12 Financial Sectors: Banks, Credit, Insurance and Market Infrastructure
- QIDs reviewed (10, A=10 B=0): tts.m12.q01, tts.m12.q02, tts.m12.q03, tts.m12.q04, tts.m12.q05, tts.m12.q06, tts.m12.q07, tts.m12.q08, tts.m12.q09, tts.m12.q10
- Objectives reviewed: tts.m12.lo1, tts.m12.lo2, tts.m12.lo3, tts.m12.lo4, tts.m12.lo5
- Lessons: tts.m12.l1, tts.m12.l2, tts.m12.l3, tts.m12.l4, tts.m12.l5, tts.m12.l6
- Issues / fixes: Expanded m12.q04 a/b/d CET1-denominator rationales. Lessons already refuse unverified APRA numerical minima. No pool B.
- Remaining confidence: medium-high on quiz keys and prompt/rubric alignment; not expert-certified. Pool B absent — explicit teaching limitation, not scored as PASS and not treated as an automatic launch block.

### tts.m13 Real-Economy Sectors I: Energy, Mining and Utilities
- QIDs reviewed (10, A=10 B=0): tts.m13.q01, tts.m13.q02, tts.m13.q03, tts.m13.q04, tts.m13.q05, tts.m13.q06, tts.m13.q07, tts.m13.q08, tts.m13.q09, tts.m13.q10
- Objectives reviewed: tts.m13.lo1, tts.m13.lo2, tts.m13.lo3, tts.m13.lo4, tts.m13.lo5
- Lessons: tts.m13.l1, tts.m13.l2, tts.m13.l3, tts.m13.l4, tts.m13.l5, tts.m13.l6
- Issues / fixes: Corrected m13.q01:d (A$800m is revenue-scale, not 'per-oz margin only') and filled empty a/c rationales. Key unchanged (b, A$200m). No pool B.
- Remaining confidence: medium-high on quiz keys and prompt/rubric alignment; not expert-certified. Pool B absent — explicit teaching limitation, not scored as PASS and not treated as an automatic launch block.

### tts.m14 Real-Economy Sectors II: Technology, AI and Communications
- QIDs reviewed (10, A=10 B=0): tts.m14.q01, tts.m14.q02, tts.m14.q03, tts.m14.q04, tts.m14.q05, tts.m14.q06, tts.m14.q07, tts.m14.q08, tts.m14.q09, tts.m14.q10
- Objectives reviewed: tts.m14.lo1, tts.m14.lo2, tts.m14.lo3, tts.m14.lo4, tts.m14.lo5
- Lessons: tts.m14.l1, tts.m14.l2, tts.m14.l3, tts.m14.l4, tts.m14.l5, tts.m14.l6
- Issues / fixes: Softened unit_metrics so an unasked SBC discussion is not a fail. Keys sound. No pool B.
- Remaining confidence: medium-high on quiz keys and prompt/rubric alignment; not expert-certified. Pool B absent — explicit teaching limitation, not scored as PASS and not treated as an automatic launch block.

### tts.m15 Real-Economy Sectors III: Industrials, Consumer, Health Care and Real Estate
- QIDs reviewed (10, A=10 B=0): tts.m15.q01, tts.m15.q02, tts.m15.q03, tts.m15.q04, tts.m15.q05, tts.m15.q06, tts.m15.q07, tts.m15.q08, tts.m15.q09, tts.m15.q10
- Objectives reviewed: tts.m15.lo1, tts.m15.lo2, tts.m15.lo3, tts.m15.lo4, tts.m15.lo5
- Lessons: tts.m15.l1, tts.m15.l2, tts.m15.l3, tts.m15.l4, tts.m15.l5, tts.m15.l6
- Issues / fixes: eleven_sector_coverage id kept; text now grades the five required companies, not an 11-sector sweep. No pool B.
- Remaining confidence: medium-high on quiz keys and prompt/rubric alignment; not expert-certified. Pool B absent — explicit teaching limitation, not scored as PASS and not treated as an automatic launch block.

### tts.m16 Macro, News and Catalysts
- QIDs reviewed (10, A=10 B=0): tts.m16.q01, tts.m16.q02, tts.m16.q03, tts.m16.q04, tts.m16.q05, tts.m16.q06, tts.m16.q07, tts.m16.q08, tts.m16.q09, tts.m16.q10
- Objectives reviewed: tts.m16.lo1, tts.m16.lo2, tts.m16.lo3, tts.m16.lo4, tts.m16.lo5
- Lessons: tts.m16.l1, tts.m16.l2, tts.m16.l3, tts.m16.l4, tts.m16.l5, tts.m16.l6
- Issues / fixes: Expanded m16.q03 a/c/d rationales. Added publisher and time zone to the prompt. PMI item is scoped to S&P Global, not ISM. No pool B.
- Remaining confidence: medium-high on quiz keys and prompt/rubric alignment; not expert-certified. Pool B absent — explicit teaching limitation, not scored as PASS and not treated as an automatic launch block.

### tts.m17 Technical Analysis: Trend, Mean Reversion and Volatility
- QIDs reviewed (10, A=10 B=0): tts.m17.q01, tts.m17.q02, tts.m17.q03, tts.m17.q04, tts.m17.q05, tts.m17.q06, tts.m17.q07, tts.m17.q08, tts.m17.q09, tts.m17.q10
- Objectives reviewed: tts.m17.lo1, tts.m17.lo2, tts.m17.lo3, tts.m17.lo4
- Lessons: tts.m17.l1, tts.m17.l2, tts.m17.l3, tts.m17.l4, tts.m17.l5, tts.m17.l6
- Issues / fixes: m17.q02 key explanation now states the sqrt(N) iid convention. vol_convention rubric now grades SMA/z-score/ATR lookbacks. No pool B.
- Remaining confidence: medium-high on quiz keys and prompt/rubric alignment; not expert-certified. Pool B absent — explicit teaching limitation, not scored as PASS and not treated as an automatic launch block.

### tts.m18 Position Sizing, Correlation, Stops and Gaps
- QIDs reviewed (20, A=10 B=10): tts.m18.q01, tts.m18.q02, tts.m18.q03, tts.m18.q04, tts.m18.q05, tts.m18.q06, tts.m18.q07, tts.m18.q08, tts.m18.q09, tts.m18.q10, tts.m18.qb01, tts.m18.qb02, tts.m18.qb03, tts.m18.qb04, tts.m18.qb05, tts.m18.qb06, tts.m18.qb07, tts.m18.qb08, tts.m18.qb09, tts.m18.qb10
- Objectives reviewed: tts.m18.lo1, tts.m18.lo2, tts.m18.lo3, tts.m18.lo4, tts.m18.lo5
- Lessons: tts.m18.l1, tts.m18.l2, tts.m18.l3, tts.m18.l4, tts.m18.l5, tts.m18.l6
- Issues / fixes: Added no-guarantee sentence to the prompt. gap_var_stress no longer requires an unasked VaR/ES worksheet. VaR lesson now states stationarity/non-iid limits. Historical 5th-percentile teaching convention left intact and already distinguished from Basel ES wording.
- Remaining confidence: medium-high on quiz keys and prompt/rubric alignment; not expert-certified.

### tts.m19 Performance Metrics and Statistical Confidence
- QIDs reviewed (18, A=10 B=8): tts.m19.q01, tts.m19.q02, tts.m19.q03, tts.m19.q04, tts.m19.q05, tts.m19.q06, tts.m19.q07, tts.m19.q08, tts.m19.q09, tts.m19.q10, tts.m19.qb01, tts.m19.qb02, tts.m19.qb03, tts.m19.qb04, tts.m19.qb05, tts.m19.qb06, tts.m19.qb07, tts.m19.qb08
- Objectives reviewed: tts.m19.lo1, tts.m19.lo2, tts.m19.lo3, tts.m19.lo4
- Lessons: tts.m19.l1, tts.m19.l2, tts.m19.l3, tts.m19.l4, tts.m19.l5, tts.m19.l6
- Issues / fixes: Added outlier-removal note to the prompt (already taught). Streak ~30.53% item already qualifies iid / 0.58 loss rate and is not a market probability. Sharpe annualisation already cites Sharpe 1994 no-serial-correlation warning.
- Remaining confidence: medium-high on quiz keys and prompt/rubric alignment; not expert-certified.

### tts.m20 Backtesting, Bias and Validation
- QIDs reviewed (20, A=10 B=10): tts.m20.q01, tts.m20.q02, tts.m20.q03, tts.m20.q04, tts.m20.q05, tts.m20.q06, tts.m20.q07, tts.m20.q08, tts.m20.q09, tts.m20.q10, tts.m20.qb01, tts.m20.qb02, tts.m20.qb03, tts.m20.qb04, tts.m20.qb05, tts.m20.qb06, tts.m20.qb07, tts.m20.qb08, tts.m20.qb09, tts.m20.qb10
- Objectives reviewed: tts.m20.lo1, tts.m20.lo2, tts.m20.lo3, tts.m20.lo4, tts.m20.lo5
- Lessons: tts.m20.l1, tts.m20.l2, tts.m20.l3, tts.m20.l4, tts.m20.l5, tts.m20.l6
- Issues / fixes: Added explicit data-quality and no-edge-claim asks. Labelled the Sharpe 1.8/0.6/0.4/0.1 walk-forward case and its random-entry 0.3 counterfactual as invented teaching numbers, not SYNTHETIC-file or holdout-selected results. SYNTHETIC IS +0.75% / OOS -3.97% / look-ahead +2.03% still match results.json.
- Remaining confidence: medium-high on quiz keys and prompt/rubric alignment; not expert-certified.

### tts.m21 Disciplined Paper Practice, Ethics and Critical Thinking with AI
- QIDs reviewed (18, A=10 B=8): tts.m21.q01, tts.m21.q02, tts.m21.q03, tts.m21.q04, tts.m21.q05, tts.m21.q06, tts.m21.q07, tts.m21.q08, tts.m21.q09, tts.m21.q10, tts.m21.qb01, tts.m21.qb02, tts.m21.qb03, tts.m21.qb04, tts.m21.qb05, tts.m21.qb06, tts.m21.qb07, tts.m21.qb08
- Objectives reviewed: tts.m21.lo1, tts.m21.lo2, tts.m21.lo3, tts.m21.lo4
- Lessons: tts.m21.l1, tts.m21.l2, tts.m21.l3, tts.m21.l4, tts.m21.l5, tts.m21.l6
- Issues / fixes: Aligned the capstone prompt with the official ten criteria; extra module fields no longer require an upload or fail an uneventful psychology sample. Expanded completion-limit and wash-sale/wash-trade rationales. Official capstone list remains 10 criteria; profit is not a pass.
- Remaining confidence: medium-high on quiz keys and prompt/rubric alignment; not expert-certified.

