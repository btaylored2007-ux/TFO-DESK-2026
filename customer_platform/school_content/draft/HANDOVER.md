# Handover — The Transparent Trading School content v1.1.0
Status: UNPUBLISHED DRAFT, ready for review. Not expert or compliance reviewed. Not user-delivered.
The customer cloud must replace its v1.0.0 course.json with this file. The schema changed: `sources` was replaced by `evidence_ids`, and new fields were added. Ids are stable.

Approvals and provenance (owner messages, WITA = UTC+8):
- pt2832u 2026-11-07 20:38: request for a 21-module Transparent Trading School with MCQs and pass rates to progress.
- t2843u 20:50: "Anything to add to make them an excellent trader? ... upload skill section ... easy to navigate and keep track of."
- t2845u 20:52: "Add it 👌". Approval of practical paper exercises, journal, mistake reviews, risk/execution/discipline assessment beyond quizzes, and easy Upload Skills (upload or describe, AI understanding review, paper test, versions, results, approval with hard rails).
Proposed defaults (not owner-specified): 80% pass mark; critical-mistake remediation; capstone pass average >= 2.0; at least 30 journaled paper trades.
Separation: Human School progress is tracked separately from AI Upload Skills strategy paper-testing. No badges are presented as qualifications.
Upload Skills implementation belongs to the customer cloud. This package contains only requirements text.

Paths: /workspace/transparent-trading-school/{course.json,curriculum.md,coverage.md,QA.md,sources.md,CHANGELOG.md,HANDOVER.md,MANIFEST.sha256}; evidence/raw/; src/; v1.0.0-checkpoint/
Re-run checks: `cd /workspace/transparent-trading-school && python3 src/qa_check.py`
