# QA report v1.1.0 (2026-11-07, UTC+8) — UNPUBLISHED DRAFT, ready for review
Not expert, legal or compliance reviewed. Do not present it as such.

## Counts
21 modules (exactly). 126 lessons: 105 core plus 21 deep-dive/practice (6 per module). About 9,500 lesson words.
251 MCQs: pool A 215 (v1 ids, keys and option orders unchanged), plus pool B 36 (6 each in M3, M8, M9, M18, M19, M20: the risk and performance gating modules).
Practical paper exercise required in every module. M21 capstone: 10-criterion rubric, journal fields, and critical-mistake remediation.

## Independent mechanical checks (src/qa_check.py; re-run after final writes)
- Numeric recomputation: 100 calculation questions recomputed from independent formulas in src/checks.py. The correct option matched and no distractor matched for 100/100.
- Structure: unique ids; 4 unique options; an explanation on every option; every question maps to an existing objective; 4-6 lessons; at least 10 pool-A questions per module. 0 issues.
- Deep-dive arithmetic recomputed separately. The bond Macaulay/modified duration (2.884/2.747) and the crypto max position (A$3,180) were corrected.
- Phrase scan: 'guaranteed profit' appears only as text in wrong distractor options (correctly marked wrong).
- Heuristic key audit: no correct-option explanation reads as a rejection, and no wrong option is labelled correct.

## Fact review
19 evidence items (sources.md). 15 come from official text read directly, and 4 from official page text via search extract where direct fetch was blocked (RBA, ATO, ASX T+1 page, ASIC CS 64).
Lesson text changed to match the sources: settlement (ASX T+2, US T+1), RBA cash rate, BIS FX turnover, ASIC CFD margins and expiry, ATO CGT discount, franking (made jurisdiction/entity-dependent), JORC, GICS, ASX LR 3.1, Moneysmart crypto/ETF.
v1 citations to empty or irrelevant pages were removed.

## Unresolved (see sources.md)
PMI 50 convention; ABS CPI publisher; price-weighted indices; Basel/APRA definitions; insider-trading wording; TGA/FDA/EMA; leveraged ETF daily reset; Sharpe official definition; franking rate per entity.
Most formula content is general reference without a citation.

## Residual gaps
- Not every question has been read by a human domain expert.
- Pool B exists only for 6 critical modules. Other modules reshuffle a single pool.
- No diagrams or charts.
- Paper capstone marks are practice evidence only, not validated skill.
- Regulatory items are time-sensitive (ASIC CFD order expiry 23 May 2027 with a proposed extension; ASX T+1 consultation; Australian DAP laws from April 2027).
