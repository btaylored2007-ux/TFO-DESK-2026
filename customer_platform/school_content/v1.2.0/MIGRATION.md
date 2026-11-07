# Schema migration: tts-course 1.1.0 / 1.1.1 -> 1.2.0

Replace the unpublished course file as a whole. Do not merge field-by-field onto a live edited copy.

Unchanged:

- `schema` remains `tts-course`
- All 21 module ids, all 126 lesson ids, all v1.1.1 question ids
- All v1.1.1 `correct_option` keys and option orders
- Existing fields (`lessons`, `questions`, `paper_exercise`, `practical_required`, `glossary`, `objectives`, `progression`, `capstone`)

Additive (safe to ignore until the customer cloud is updated):

- Top-level `content_review`, `learning_tracks`, `worked_dataset`, `schema_migration`
- `modules[].practical_rubric` (per-module paper marking criteria)
- New pool-B question objects (36 ids listed in `v1.2.0/changes.json`)
- Additional glossary entries on M16, M18, M20 and M21
- Extra `progression.rules` and a clearer `capstone.note`

Lesson `body` strings are longer. The cloud should render the full body.

Status remains unpublished draft. Do not host a user trial link from this file.
