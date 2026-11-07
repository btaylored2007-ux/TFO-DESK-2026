# Caller coverage

`veteran_desk/desk.py` on live and dash is `670a55e65f52e6730561f0bac46248ccdee965680444aaff62384e2252124dd4`. The target adds the task id the caller already has. It does not replace the rest of the file.

`desk.run` with `THF_DESK=1` returns into `thf_round.run`. That function already has `runid + "-" + seat` before `prop_prompt`. The prompt the model sees now passes that same string into `_brain_block`. A veteran `desk.run` uses the `runid` it already writes on the run log, plus the seat. Neither path creates a decision link by itself. A queued round ticket is still only `accept_round_ticket` after a real queued id.

`seat_brain.nightly` stores `now_wita()` on its status file. The inclusion and citation task is `nightly|<that stamp>|<seat>`. The review reply is cited only when it names a `ref=`. There is no decision link and no trade id.

`code_hands` returns text. It does not call `note_accepted_decision`.

## Sampler

The absent paths `/opt/tfo/live/thf/thf_shadow_sampler.py` and `/opt/tfo/dash/base/thf_shadow_sampler.py` are not the module. The repo import is `veteran_desk.thf_shadow_sampler`. `deploy/incremental_20261106/tfo-thf-shadow.service` runs `python -m veteran_desk.thf_shadow_sampler once`. Callers in this repo are `thf_round.py` (`enqueue_production_prompt`) and `thf_msgdesk.py` (same, inside the trace helper). The module calls `lesson_trace.publish_authority` and `load`. It does not call `prompt_block`. Its live bytes were not captured.

## Still required before those two files can be packaged

- `/opt/tfo/live/thf/veteran_desk/thf_shadow_sampler.py` and `/opt/tfo/dash/base/veteran_desk/thf_shadow_sampler.py`, with sha256.
- `/opt/tfo/live/thf/veteran_desk/thf_chat_tasks.py` and `/opt/tfo/dash/base/veteran_desk/thf_chat_tasks.py`, with sha256. `learning_prompt(seat, msg)` calls `prompt_block` and does not pass `msg["id"]`. `thf_msgdesk` already has that id. The live file is not in this package.

Nothing here is deployed. `GENUINE_VERIFIERS` is empty. `RULE_GAP` is not accepted. Live validated count is 0. The same 20 policy tests stay unaccepted. `book.py` is not edited.
