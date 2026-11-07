# QA report v1.2.0 (2026-11-07, WITA / UTC+8)

UNPUBLISHED DRAFT. AI content review, not expert, legal or compliance review.

## Counts

- 21 modules (ids unchanged).
- 126 lessons (ids unchanged): 105 core + 21 deep dives.
- Lesson prose: 22,186 words (core 16,637; deep 5,549). v1.1.1 was 9,928.
- Questions: 287 (pool A 215 unchanged; pool B 72 = 36 inherited + 36 new).
- Practical rubric on every module.

## Mechanical checks (`python3 src/qa_check.py`)

Saved at `evidence/qa-v1.2.0.txt`.

- Numeric recomputation: 112/112 (100 inherited calculation items plus 12 new ones). Correct option matched; no distractor matched within tolerance.
- Structure: unique ids; 4 unique options; explanations present; every question maps to an existing objective; 4-6 lessons; at least 10 pool-A questions; rubric present with profit-is-not-pass language. 0 issues.
- ID preservation versus immutable v1.1.1: no missing question ids, no `correct_option` changes, lesson id set unchanged, module order unchanged.
- New-question explanations all longer than the new-item minimum.
- Phrase scan: no "you will become", "certified trader" or "accredited course". "No strategy guarantees profits" appears as a denial.

## Independent calculation notes (AI review, not a certified examiner)

Rechecked from stems and from lesson worked examples, not by trusting the previous author's checklist alone:

- Duration sign and the M4 deep-dive Macaulay 2.884 / modified 2.747 figures recomputed from listed cashflows.
- FX inverses, crosses, pip notional P&L, and (1+r_asset)(1+r_fx)-1 home-currency returns.
- Futures notional = multiplier x index; 20:1 leverage wipeout = 5%.
- Sharpe daily 0.04 x sqrt(252) ≈ 0.635 (option text "About 0.63").
- Historical teaching VaR 1.3% and inclusive tail average 1.72% from the listed five losses.
- Nine-loss streak probability as in R19.
- Dataset totals as in R20 after re-running the script.

## Residual QA holds

- Most inherited distractor explanations remain short (a known v1.1.1 issue). Only four were rewritten.
- No human market-practitioner or lawyer signed this file.
- Pool B is still absent from Modules 2, 6, 7, 10-17.
