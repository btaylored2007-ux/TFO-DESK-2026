# What changed against the captured live files

The four payload files are the captured live bytes plus the provenance edits. They are not a replacement from an older repo snapshot. `coverage`, `publish_authority`, `load`, `interpret_reply`, `AUTHORITY`, and the seat lists stay. `AUTHORITY.rule` is the same sentence. `RULE_GAP` is additional text. It is not an acceptance of validation.

Removed only as the old bodies those edits replace:

- `lesson_trace.record_included` used to accept a missing task id and a non-ref id. It now requires both. A caller with no task id gets no included row.
- `record_validated` used to accept any check name. It now fail-closes because `GENUINE_VERIFIERS` is empty.
- `observe_book_close` used to treat the closed position id as the lesson id. A close now matches only a prior decision whose `queue_id` is that position id or a `-P<n>` / `-Q` slice.
- `seat_brain.after_chat` used to cite the latest included row for the seat, including a different task. It now cites only the task id it was given.
- `thf_round` records `queue_id` only after `submit_candidate` returns a queued id. The citation task is `runid-seat`. The decision task is that queued id.
- `thf_msgdesk` passes `msg["id"]` into the brain block. A chat answer is `no_decision`. The engineer uses `reply_to` or the thread root already passed in. No id is invented.

`seat_brain.nightly` still calls `prompt_block(seat, 1100)` and `after_chat` with no task id. That stays fail-closed. No nightly ticket is invented.

`desk.py` still calls `prompt_block(seat, 1100)`. That call keeps working and does not write an included row. The live `desk.py` was not in this capture and is not in the payload.

`thf_shadow_sampler.py` calls `publish_authority` and `load`. Those signatures are unchanged. Its live hash was not in this capture, so it is not a base. It is not overwritten.

## Feed, not closed

`feed_status_facts.py` is absent on live and on dash. It imports `veteran_desk.feed_health.us_rth_open` and `asia_session_open`. The keeper and the dashboard that would call it were not captured. Those three files are listed in `MANIFEST.json` with `sha256` null. The installer does not copy `feed_status_facts.py` and does not copy a repo snapshot of the keeper or the home page.
