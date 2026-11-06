# Included, cited, queued, closed, validated

These are separate rows. One does not imply the next.

- **included.** `prompt_block` placed the ref in the prompt for this task id. The chat learning prompt uses `msg["id"]` when that id is already on the message. A missing id does not get a substitute. A round uses `runid-seat`. Nightly uses the status stamp it already writes, `nightly|<stamp>|<seat>`.
- **cited.** The reply text contains `ref=` for a ref that this same task included. The reply is not stored. A citation is not a decision.
- **queued.** `accept_round_ticket` runs only after a proposal id was actually queued, and only when that reply cited the ref. The row stores `queue_id`. The citation task and the decision task are different strings. This is not a close and it is not validated.
- **closed.** A book close matches that `queue_id`, or a `-P<n>` / `-Q` slice from `partial_close`. `closed_utc` is not before the citation. The row is `outcome_link` with `validated` false and `causal_edge` false.
- **validated.** `record_validated` raises. `GENUINE_VERIFIERS` is empty. `AUTHORITY.rule` is still: "A lesson is included when placed in a prompt, cited only if the reply names that ref, and validated only by a later book close. Otherwise the state is insufficient_data." The code does not follow the validation sentence. That difference is `RULE_GAP`. It is not accepted. The live validated count is 0.

`code_hands` returns text and does not call `note_accepted_decision`. A chat answer is `no_decision`.

## Sampler

`veteran_desk/thf_shadow_sampler.py` is `a779a30626e765e5d6351ab851399240154802a54cdb955c309e8f97d4ad28ef` on live and dash, the same bytes as the repo. `enqueue_production_prompt(root, seat, prompt, data_asof, task_id, role="trader", task_class="production_proposal")` is what `thf_round` and `thf_msgdesk` call. `once` calls `publish_authority` and `load`. It does not call `prompt_block` or `record_validated`. The package checks the hash and does not copy the file.

Nothing in this package is deployed. The production-root refusal is unchanged. `book.py` is not edited.
