# Independent review findings: Transparent Trading School v1.1.1 -> v1.2.0

Date: 2026-11-07 (WITA / UTC+8).
Reviewer stance: AI content review. Not expert-certified, not legal advice, not compliance sign-off.
Working directory: `/workspace/transparent-trading-school-deep-review/` (prior immutable folders and the app repo were not overwritten).
Source of truth read: `/workspace/transparent-trading-school-review/v1.1.1/course.json`, `REVIEW.md`, `evidence/register-v1.1.1.md`, `v1.1.1/changes.json`, and `/workspace/transparent-trading-school/` QA/coverage/sources.

Status of the deliverable: **unpublished draft, source-ready for a later whole-file replace**. Comprehensive acceptance is not claimed. Expert and legal review remain a hold.

## What was actually measured in v1.1.1

The parent notes were checked against the files, not taken on trust.

- Exactly 21 modules, 126 lessons (6 each), 251 questions (pool A 215, pool B 36 in Modules 3, 8, 9, 18, 19, 20 only).
- Lesson prose: 6,295 core + 3,633 deep = **9,928 words**. That count matches `REVIEW.md`. Counts are not quality.
- Every module had `practical_required` and a short `paper_exercise` string. Only Module 21 / top-level `capstone` had a marking rubric.
- Thin lessons were real: several cores were under 40 words (for example M13.l5 23, M14.l5 25, M12.l3 28, M18.l2 28, M21.l2 28).
- About 733 option explanations were three words or fewer, many of them numeric ("Arithmetic error"), and a few were unhelpful ("No.", "Nonsense.").
- No VaR or expected-shortfall teaching, no dedicated data-quality lesson, no worked dataset, no structured psychology loop, no alternate pool for the capstone.

## Defects and independent checks

### Calculation items (inherited)

The published v1.1.1 numeric checklist was recomputed (112 later including new items). Inherited stems that were checked independently from the question text, not merely "looked valid", included:

- Spread mid and cash cost; index-weight contributions; total return, compounding, TWR, drawdown recovery.
- Bond price at par when coupon equals yield; duration **sign** (`dP/P ≈ -Dmod × dy`); expected credit loss ≈ PD × LGD.
- FX inverse, crosses, pip P&L on notional, home-currency return `(1+r_asset)(1+r_fx)-1`.
- Commodity operating-leverage margin change; roll plus collateral minus fee compounding.
- Crypto daily vol `σ / sqrt(365)` and funding `rate × periods × notional`; leverage wipeout `1 / leverage`.
- Option breakevens and expiry P&L including premium; futures notional = multiplier × index.
- Implementation shortfall and borrow-fee day-count.
- Statement ratios, multiples, Gordon value, franking illustration (still entity-dependent).
- Sector unit metrics (AISC margin, reserve life, RAB return, cap rate, NRR, churn compounding, RASK−CASK).
- True range, `σ × sqrt(252)` annualisation, position size `risk / stop distance`, portfolio vol with ρ = 0.
- Expectancy, Sharpe, Sortino, SE, t, profit factor; Bonferroni and `1-(1-α)^n`.

No wrong inherited keys were found in those calculation items. Deep-dive examples that were recomputed (M4 Macaulay 2.884 / modified 2.747; M7 max position about A$3,185 versus the lesson's A$3,180 rounding; M5 pip-to-AUD conversions; M9 shortfall 67.5 bps) matched.

This is **AI content review**, not a claim that every conceptual distractor in the original 251 questions was expert-certified.

### Nine-loss-streak "about 30%" (v1.1.1 author append)

Assumptions recovered from the lesson: 120 trades, independent Bernoulli trials, loss probability 0.58 (complement of the 42% win rate in the fictional report), event = at least one run of 9 losses.

- Exact Markov recurrence: **0.3053**.
- Monte Carlo, 200,000 trials, seed **20261107**: **0.3051**.
- Naive union `112 × 0.58^9 ≈ 0.83` overstates because the windows overlap.

The "about 30%" figure is therefore justified under those assumptions and was **kept**, with the method, seed and limitations written into M19.l6. It is not a market forecast. If trades are dependent or the loss rate differs, the probability changes.

### Other narrative points

- Glossary PMI on M16 said "50 separates expansion from contraction" without the S&P Global scope already used in the lesson. **Qualified** in v1.2.0.
- Index cap-weighting of named baskets other than the sourced price-weighted DJIA and Nikkei 225 remains **not re-asserted** (same caution as v1.1.1).
- M12 no longer says capital is "far above minima" (already removed in v1.1.1); v1.2.0 keeps that caution and does not invent APRA percentages.
- Share-margin call: added from a **full read** of the 10 June 2021 investor.gov bulletin (US), clearly separated from ASIC CFD minima (Australia).
- VaR / ES wording taken from a **full read** of BCBS MAR10.17-10.18 in the January 2019 (rev. February 2019) official PDF. The teaching illustration (1.3% VaR, 1.72% inclusive tail average) is fictional and states that Basel's "exceeding" language and inclusive tail averages can differ.

### New pool answers (critical items)

Independently recomputed or conceptually checked before inclusion:

| ID | Result |
|---|---|
| m01.qb01-02, qb06 | Mid (12.40+12.50)/2 = 12.45; 200×0.06 = 12 (older 15.00/15.06 mid was changed because 15.00 sat inside a 0.2% numeric tolerance of 15.03); 15×2 + 0.15%×8000 = 42 |
| m01.qb03-05 | Stop-limit may never fill; ASX T+2 as sourced; Tuesday 10:15 Sydney cannot know that Tuesday's New York close |
| m04.qb01, qb03, qb05 | −7×0.40% = −2.8%; 4%×45% = 1.8%; coupon = yield ⇒ par |
| m05.qb01-04 | 1/0.72 = 1.3889; 50,000×0.004 = US$200; 1.05×0.92−1 = −3.4%; 1.08/0.72 = 1.50 |
| m18.qb07-08 | VaR 1.3%; inclusive ES stand-in 1.72% |
| m18.qb09-10 | No drawdown guarantee; rising correlation reduces diversification |
| m19.qb07 | 0.04×√252 ≈ 0.635 ("About 0.63") |
| m19.qb08 | Streak ≈ 30% under stated assumptions; 83% naive figure is wrong |
| m20.qb07-10 | Split-as-crash; timezone look-ahead; stale halt print; synthetic IS +0.75% / OOS −3.97% is overfitting theatre, not alpha |
| m21.qb01-08 | Revenge sizing, strategic no-trade, reject rail-breaking prompts, no accreditation, verify unsourced AI stats, paper ≠ live competence |

## Coverage gaps closed in v1.2.0

Closed **within the existing 21 module ids** (no new modules or lesson ids):

1. **Per-module paper rubrics** on all 21 modules, with objective evidence and retry/remediation language. Profit or a positive paper P&L is not a pass criterion.
2. **VaR and expected shortfall**, with official definitional wording, a worked fictional sample, and limitations (not a max loss, not a drawdown, sensitive to window, distribution, correlation and liquidity).
3. **Correlation-regime and gap/slippage/liquidity stress cases** in Module 18, with explicit **no drawdown guarantee**.
4. **Data quality**: session/timezone, corporate actions, fees, delist/survivorship, missing data, stale/halt prints, lag versus look-ahead.
5. **Worked SYNTHETIC mini-backtest**: CSV + `compute_backtest.py` + `results.json`. Seed 20261107. No future-looking input columns. No order book. No historical-edge or alpha claim. Re-run reproduces the teaching numbers (raw split-day ≈ −50.7% versus adjusted ≈ −1.3%; best IS honest net lookback 15 ≈ +0.75%; OOS ≈ −3.97%; look-ahead IS ≈ +2.03%).
6. **Psychology process** in Module 21: precommitment, in-session checklist, losing streaks, revenge, overconfidence, strategic no-trading when gates fail, after-action review. Linked to the verified streak arithmetic.
7. **Upload Skills approval versus naive prompt trust**, and **human learning versus AI-strategy tracks kept separate**.
8. **Capstone** restated as configurable product defaults, not owner law, not automatic certification, not live-competence evidence.
9. **US share-margin call** worked example from investor.gov, jurisdiction-labelled.
10. **All 11 GICS sectors** have explicit metric-interpretation notes and limitations (see `coverage.md`).
11. **Alternate assessment pools** for Modules 1, 4, 5 and 21, plus extra critical items in 18-20 (36 new questions). Pool A of 215 is unchanged.
12. Lesson prose deepened from 9,928 to **22,186 words**, with why / worked or decision exercises / limitations layered onto existing ids rather than padded lists.

Word counts by field (v1.2.0, `src/qa_check.py`):

| Field | Words |
|---|---|
| Core lessons | 16,637 |
| Deep dives | 5,549 |
| Lessons total | 22,186 |
| Question stems + options + explanations | 10,565 |
| Glossary | 1,119 |
| Paper exercise strings | 766 (unchanged from v1.1.1) |
| Practical rubrics (approx, serialised) | 6,339 |

## Coverage gaps still open (prioritised)

These are honest leftovers, not a hidden "do a v1.3 tomorrow" claim that everything is finished.

1. **Expert / legal / compliance review** — still none. Regulatory passages (ASIC CFD order dates, ASX settlement, ATO CGT, insider-trading wording, TGA/FDA/EMA roles, CFA code citation) need a lawyer or qualified reviewer before any customer-facing publication. This pass added sources where it made a new claim and otherwise refused unsourced legal-penalty or tax-rate detail.
2. **Human domain review of all 251 inherited conceptual items** — numeric keys were recomputed; every original distractor was not expert-reread.
3. **Alternate pools missing** on Modules 2, 6, 7 and 10-17. Extra pools were added where correctness risk was highest (foundations, duration, FX, tail risk, data quality, capstone), not to inflate question count everywhere.
4. **Inherited terse explanations** — most of the short v1.1.1 distractor lines remain. Four unhelpful ones were rewritten.
5. **Paper exercise prompts** themselves were not rewritten; marking criteria were added beside them. A later pass could tighten the prompts so that the evidence they ask for maps even more obviously onto each rubric line.
6. **No auction-imbalance or live queue-position dataset** — the limitation is stated in M1 rather than faked.
7. **No deflated Sharpe statistic** — named as absent in M20 rather than invented.
8. **APRA / Basel numerical minima** and **ISM PMI methodology** still not primary-verified. CET1/LCR/NSFR are taught as general names. Specific index cap-weight claims beyond DJIA/Nikkei remain unrestated.
9. **Some modules are still shorter than others** (M10 and M14 remain under 800 lesson words). They now have why / decision / limitation layers and sector metrics, but they are not long-form references.
10. **No diagrams**. None were requested as a visual deliverable.
11. **No publication, hosting, or user trial link**. Course status is unpublished draft.
12. **Simulation still cannot evidence live competence**. That limit is now written into lessons, rubric, disclaimer and capstone note; it cannot be closed by more prose.

Recommended next steps, in order, if another pass is authorised:

1. Independent practitioner review of Modules 8, 18, 19, 20 and 21, and legal review of M8 CFD, M9 tax, M16 disclosure/insider language, and M21 ethics.
2. Optional pool-B items for the remaining single-pool modules, written for correctness rather than count.
3. Optional rewrite of inherited terse distractor explanations.
4. Cloud integration test of the additive `practical_rubric` and new question ids against the v1.1.0 customer-cloud renderer.

## Files

- `REVIEW_FINDINGS.md` (this file)
- `v1.2.0/course.json` and root `course.json` (identical), version **1.2.0**, supersedes **1.1.1**, status unpublished
- `v1.2.0/changes.json`
- `datasets/` (SYNTHETIC CSV, corporate-action file, script, results)
- `src/build_v120.py`, `src/expansions.json`, `src/rubrics.json`, `src/new_questions.json`, `src/checks.py`, `src/qa_check.py`
- `evidence/register-v1.2.0.md`, `evidence/qa-v1.2.0.txt`, `evidence/raw/`
- `coverage.md`, `QA.md`, `CHANGELOG.md`, `MIGRATION.md`

Re-run checks after any later edit: `python3 src/qa_check.py` and `python3 datasets/compute_backtest.py`.
