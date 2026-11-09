# Migration: v1.2.0 → v1.2.1

Replace the unpublished course JSON as a whole. Do not merge field-by-field
into a mutated copy of the already-shared v1.2.0 file.

## Grading of stored quiz attempts

No `correct_option` changed. Every question ID is unchanged. A stored answer
that was right under v1.2.0 remains right; a stored wrong answer remains wrong.

One stem was clarified without changing the key:

- `tts.m06.q03` now says "producer's unit cash margin" instead of the bare
  phrase "Margin change?". The correct option is still b (+40%). Old attempts
  that selected b still pass. Learners who misread the old stem as futures
  margin may have selected another option; that is not a key migration, only
  a clearer stem going forward.

Explanation text displayed after submission has changed for the items listed
in `v1.2.1/changes.json`. That does not affect right/wrong scoring.

## Rubric IDs

All `practical_rubric.criteria[].id` values are unchanged so an app that
already mapped those IDs does not need a new ID table. Several descriptions
and `objective_evidence` strings were rewritten so they grade the written
exercise. Notable behaviour changes for human markers (not quiz keys):

- `tts.m08` `leverage_identity` / `futures_quantity`: grade the asked 50%
  CFD close-out (5% of A$20,000 notional at 10:1), not a 10% full wipeout
  and not an unasked index-future multiplier problem. The `futures_quantity`
  id is retained for compatibility.
- `tts.m02` `instrument_fit`: fees, domicile and concentration of the two
  broad-market ETFs, not a leveraged-ETF suitability essay.
- `tts.m07` `custody_checklist`: five Moneysmart-page risks.
- `tts.m09` `carry_and_tax_caveat`: slippage versus % ADV.
- `tts.m15` `eleven_sector_coverage`: the five required companies.
- `tts.m17` `vol_convention`: SMA / z-score / ATR lookbacks.
- `tts.m18` `gap_var_stress`: gap stress and no-guarantee language; VaR/ES
  optional.
- `tts.m21` extra fields: an AI-strategy upload is not required; an
  uneventful psychology sample is not an automatic fail.

A few paper_exercise strings gained one clause so an already-present
criterion is actually requested (M01 settlement/time zone, M03 ledger
drawdown, M06 producer vs futures, M10 page cites, M16 publisher/time zone,
M18 no-guarantee sentence, M19 outlier note, M20 data-quality and no-edge
claim). Module 21's prompt now names the official ten capstone criteria.

## Schema

No new required top-level keys. `schema_migration.to` is `1.2.1`.
Module 21's rubric gained an informational `alignment_note` field that
consumers may ignore.

## What not to do

- Do not overwrite the immutable v1.2.0 folder.
- Do not treat this file as publication or course-completion authority.
- Do not require users to re-sit quizzes solely because explanations changed.
