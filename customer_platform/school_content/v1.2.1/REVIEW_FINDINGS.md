# Conceptual review findings — unpublished v1.2.0 → v1.2.1

Date: 7 November 2026, Asia/Makassar (UTC+8).
Status: unpublished draft. Not expert-certified. No legal or compliance review.
The course is not marked complete. Human review was not available and was not pretended.
Paid expert procurement was not required and was not performed.

This was one bounded conceptual-correctness pass. It is not a word-count or pool-count
acceptance exercise. The inherited 112 numeric QA checks still pass; they do not by
themselves prove conceptual correctness. That required reading the stems, keys and
wrong-option rationales.

## What was read

- All 287 questions (215 pool A, 72 pool B), including inherited conceptual items:
  stem, designated key, every option text, and every explanation.
- All 21 paper-exercise prompts against their `practical_rubric` criteria.
- The official capstone list (10 criteria) against Module 21's module rubric (13 criteria)
  and the Module 21 prompt.
- All listed learning objectives.
- High-risk lessons in full (settlement, franking, APRA/CET1, PMI, VaR/ES, streak,
  SYNTHETIC backtest, capstone limits). The other 105 lesson bodies were claim-scanned
  rather than line-edited. See coverage.md.

Prior immutable folders were not modified:
`/workspace/transparent-trading-school/`,
`/workspace/transparent-trading-school-review/`,
`/workspace/transparent-trading-school-deep-review/`
(including the already-shared v1.2.0 `course.json`).

## Why a new version was minted

Real semantic defects were found. They are not padding. v1.2.1 keeps every question ID
and every existing correct key. Old quiz attempts continue to grade the same way.
No answer key was judged to be a real error.

## Material fixes

1. Wrong rationale: `tts.m13.q01` option d explained A$800m as "Per-oz margin only".
   That is incorrect. A$800 is the per-ounce all-in margin; A$800m is revenue
   (A$3,200 × 250,000 oz). The key remains b (A$200m = A$800 × 250,000).
2. Overclaim: `tts.m01.q03` option d said a market order "guarantees execution".
   That contradicts the module's own "when each can fail" objective. Execution is
   not assured in a halt or an empty book.
3. Ambiguous stem: `tts.m06.q03` asked only "Margin change?" after giving a cash
   cost and a price rise. That can be read as futures margin. The stem now says
   producer unit cash margin. Key remains b (+40%, 20 → 28).
4. Imprecise rationale: `tts.m04.q04` option b said floater duration is "near zero".
   Duration is roughly time to next reset, which is short compared with a 30-year
   zero but not unconditionally zero.
5. Critical exercise–rubric mismatch (M08): the prompt asks for a fictional 10:1
   CFD of A$20,000 notional and the move that triggers a 50% margin close-out.
   That move is 5% of notional (loss of half of the A$2,000 initial margin).
   The old rubric asked for the no-buffer wipeout `1/leverage` (10%) and for an
   index-future multiplier problem the prompt never set. A learner who did the
   exercise correctly could have been marked wrong. Criterion IDs were kept;
   the text now grades the asked 50% close-out.
6. Other prompt/rubric mismatches rewritten or closed with one asked-for clause
   (see MIGRATION.md): M01 settlement/time zone; M02 instrument_fit retargeted
   to fees/domicile/concentration; M03 drawdown of the same ledger; M05 cross
   and unhedged decomposition rather than an extra inverse form; M06
   producer-versus-futures sentence; M07 five Moneysmart risks rather than an
   invented custody form; M09 slippage versus ADV rather than an unasked
   carry/tax worksheet; M11 three stated Gordon discount rates; M14 no surprise
   SBC fail; M15 five required companies rather than eleven sectors; M16
   publisher and time zone; M17 SMA/z-score/ATR rather than a surprise
   annualised-vol worksheet; M18 gap stress without a mandatory VaR sheet;
   M19 outlier-removal note; M20 data-quality and no-edge-claim sentences;
   M21 capstone prompt aligned to the official ten criteria, with upload optional.
7. Conventional VaR/ES: M18.l5 already distinguished teaching fifth-percentile
   historical VaR and an inclusive tail-average stand-in from Basel MAR10
   "exceeding VaR" wording, and already denied any maximum-loss guarantee.
   This pass added an explicit stationarity / non-iid / volatility-clustering
   qualifier. `tts.m17.q02` now states the same sqrt(N) assumption.
8. Streak figure: `tts.m19.l6` and `tts.m19.qb08` already treat ~0.3053 as the
   probability of at least one 9-loss run under n=120, q=0.58, independent
   Bernoulli trials (exact recurrence plus Monte Carlo seed 20261107). It is
   not a market probability. Left unchanged.
9. SYNTHETIC dataset: `m20.qb10` already refuses to read +0.75% IS / −3.97% OOS
   as alpha. Those percentages still match `datasets/results.json`
   (lookback 15, honest with 10 bps costs; look-ahead IS +2.03%). M20.l6's
   separate Sharpe 1.8 / 0.6 / 0.4 / 0.1 walk-forward case and its random-entry
   0.3 counterfactual are now labelled invented teaching numbers, not results
   from the SYNTHETIC file and not a holdout-selected rejected rule. That
   avoids leaking a training-future comparison.
10. ASIC Instrument 2020/986 s7(2)(d) 50% crypto CFD initial margin (key b on
    `tts.m08.qb03`) matched a search-extract of the published instrument text
    this pass. The explanation now tells the learner to check the current
    compilation. A full official PDF was not archived locally.

## What was not treated as a defect

- Inherited one-word numeric rationales such as "Decimal error" or "Sign error"
  on clearly arithmetic distractors. They name the error type. Many inherited
  "No." lines on numeric distractors were left in place rather than padded.
  Selected conceptual "No." lines (scam indicator, CET1 denominator, beat-then-
  sell-off, completion limits) were expanded because they taught nothing.
- Missing pool B on eleven modules (M2, M6, M7, M10–M17). This remains an
  explicit teaching limitation. It is not a PASS claim and not an automatic
  launch block.
- No new arbitrary 800-word lesson floors or pool-count bars were added.
- Auction "maximises matched volume" (M01) remains the standard introductory
  description.
- Fictional A$ examples in lessons were already generally labelled fictional
  or paper; the SYNTHETIC file was already labelled. The walk-forward Sharpe
  case was the one that needed an unequivocal invented-illustration label.

## Remaining limits (not a complete course)

- No market-practitioner, legal or compliance review.
- 105 lesson bodies were claim-scanned, not equally deeply line-edited.
- Many inherited terse numeric explanations remain.
- Eleven modules still have no pool B.
- Franking 25%/30% figures remain tied to the already-qualified ATO page dates
  in M11.l2; they were not independently re-fetched this pass.
- APRA numerical minima remain unverified and are already taught as unverified.
- ASIC 2020/986 figures rest on a search-extract, not a stored current compilation.
- Historical VaR fifth-percentile convention on a 100-observation sample is a
  teaching rule, not the only quantile definition in use.
- `results.json` also stores other lookbacks' out-of-sample totals for audit;
  teaching must not use those unused-window totals to pick N (the questions
  already forbid that).
- Paper process is not live competence, accreditation, or a profit forecast.
- This review itself is AI review, not human certification.

Do not describe v1.2.1 as comprehensive, complete, or ready to publish.
