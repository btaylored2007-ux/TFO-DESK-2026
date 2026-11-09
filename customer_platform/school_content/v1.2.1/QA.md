# QA — conceptual review / v1.2.1

Date: 7 November 2026, Asia/Makassar (UTC+8).

## Structural and key preservation

- Modules 21, lessons 126, questions 287 (A 215, B 72).
- Every v1.2.0 question ID is present. Every `correct_option` matches v1.2.0.
- Every inherited v1.1.1 question ID that still exists keeps the same key.
- Every module's practical_rubric criterion IDs match v1.2.0 (text-only edits).
- Four unique options per question; no duplicate option text.
- Numeric target suite reused from v1.2.0 `src/checks.py`: 112/112 pass, 0
  distractor collisions at the stated tolerance.
- 0 structural failures on lesson-count bounds, pool-A minimum, profit-not-pass
  rubric language, and ID uniqueness.

The 112 numeric checks still do not prove conceptual correctness. That work is
recorded in REVIEW_FINDINGS.md and coverage.md.

## Dataset and formula

File used (immutable, not rewritten this pass):
`/workspace/transparent-trading-school-deep-review/datasets/results.json`
and `synthetic_mini_backtest.csv` / `compute_backtest.py` (seed 20261107).

| Taught figure | File value | Match |
| --- | --- | --- |
| Raw split-day return | −0.506687 ≈ −50.67% | yes |
| Split-adjusted split-day return | −0.013374 ≈ −1.34% | yes |
| Best IS honest net (N=15, 10 bps) | +0.007523 ≈ +0.75% | yes |
| Same N look-ahead IS net | +0.020324 ≈ +2.03% | yes |
| Same N OOS honest net | −0.039743 ≈ −3.97% | yes |

Interpretation in the file and in m20.qb10: not market alpha; choosing N on IS
and hiding OOS is data-snooping. Other lookbacks' OOS totals are stored for
audit and must not be used to select N.

Duration sign, FX compound identity, futures notional (where still taught in
questions), Sharpe sqrt(N), and 50% close-out arithmetic were re-checked as
part of the semantic read and the 112-target suite.

## What QA does not cover

- Expert pedagogical review of every prose paragraph.
- A locally archived official compilation of ASIC 2020/986.
- Fresh fetches of ATO franking/CGT or APRA numerical standards.
- Publication, app, or deploy checks (out of scope).
