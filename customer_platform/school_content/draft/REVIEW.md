# Review pass 2: The Transparent Trading School v1.1.0 → v1.1.1 (2026-11-07, UTC+8)
The v1.1.0 folder was not modified. All outputs are here. Not expert, legal or compliance reviewed. Comprehensive acceptance is NOT met by counts alone.

## Word count correction (by field, measured)
- v1.1.0: core lessons 5,860; deep dives 3,609 (lessons 9,469); questions/options/explanations 7,384; glossary 941; exercises 766.
- v1.0.0's "19,900 words" was the whole curriculum.md, mostly quiz text; its lesson prose was about 3,000-3,500 words. Earlier depth statements should be read with this in mind.
- v1.1.1 lessons: core 6,295 + deep 3,633 = 9,928 words.

## Material facts corrected (v1.1.1)
1. Index weighting (m02.l3): removed the unverified claim that S&P/ASX 200, S&P 500 and FTSE 100 are float-cap weighted. DJIA and Nikkei 225 price-weighting is now sourced (R01, R02).
2. Leveraged/inverse ETFs (m02.l2): SEC daily-reset wording and example (R03).
3. RBA corridor (m04.l1): exact spreads; cash rate near floor; ample reserves (R04, full read).
4. CGT (m09.l4): foreign/temporary-resident exclusion; Budget 2026-27 caveat (R05, full read).
5. Franking (m11.l2, q03): 25% base-rate entity vs 30%; 30% labelled illustrative; stem clarified (R06).
6. PMI (m16.l1, q02): scoped to S&P Global's 50.0 definition; ISM not claimed (R07).
7. Insider trading (m16.l3): Moneysmart/RG 264 definition; general education, not legal advice (R09, R10).
8. Health regulators (m15.l3): TGA/FDA/EMA roles and limits (R11-R13).
9. Sharpe (m19.l3): 1966 origin, annualisation/serial-correlation caveat (R14). Volatility annualisation convention (m17.l3).
10. Removed or qualified unverified claims: 'far above minima' (m12.l6), SEC S-K 1300 (m13.l6). Quantified the streak claim (m19.l6, about 30% by simulation).

## Independent re-read: 36 pool-B questions and 21 deep dives
- Numeric recomputation: 100/100 checked questions pass (src/qa_check.py), including all numeric pool-B items.
- Defects fixed: m08.qb02 and m20.qb03 had vague distractor explanations. No wrong keys found. No ambiguous rounding found in pool B.
- Deep-dive arithmetic rechecked (bond, roll, funding, implementation shortfall, borrow, DCF, bank/insurer stress, REIT gearing, expectancy/SE/CI). No further errors beyond the v1.1.0 fixes.
- Residual minor issues: some distractor explanations are terse ("No."). m21 has no pool B. Correct letters remain unchanged from v1.1.0 (ids and keys stable).

## Not resolved
APRA/Basel capital definitions (general reference only); ISM PMI; cap-weighting of specific non-US indices; insider-trading penalties (deliberately not stated); franking rate changes after the ATO page's 2020 update; S&P DJI R01 read via extract only.

## Coverage gap checklist (draft; for expert review)
| Area | Current state | Gap |
|---|---|---|
| Liquidity / order execution | M1, M9 core + deep | No queue-position modelling exercise; no auction-imbalance data exercise |
| Margin | M8 (ASIC CFD), investor.gov margin | No margin-call worked case for share margin loans |
| Tail risk / stress | M18 gap stress, M19 CI caveat | No historical-crisis scenario set; no VaR/expected shortfall |
| Correlations | M18 | No correlation-regime-change exercise |
| Sector financial metrics | M11-M15, 11 GICS sectors | Metric definitions not source-verified per sector; company-defined metrics vary |
| Data quality | M20 partial | No dedicated lesson on corporate-action adjustment, stale prices, survivorship-free data sources |
| Backtesting / out-of-sample | M20 | No worked dataset; no deflated Sharpe |
| Psychology | M21 | Brief; no structured bias self-assessment |
| Practical (all 21) | Exercise text per module; capstone rubric | Rubrics only for M21; per-module practical marking criteria are not yet defined |
| Expert review | none | Needed: market practitioner, compliance/legal, educator |

## For the cloud
Apply v1.1.1/course.json as a whole-file replacement of v1.1.0 (same schema and ids; changes listed in v1.1.1/changes.json). Keep it as an unpublished draft.
