# Lesson integration gaps

Source only. Not deployed. `GENUINE_VERIFIERS` is empty. `AUTHORITY.rule` and `RULE_GAP` are not an acceptance of validation.

## What is proven from source

`desk.enqueue` stores the proposal id as the queue entry id. `book.tick` arms that same id as the position id. A full close keeps it. `book.partial_close` writes the slice as `position_id + suffix`. `seat_cli.close` uses `-P<n>`. The queued exit rail uses `-Q`. Rebook updates that same position and does not mint an id.

The round calls `accept_round_ticket` only after a proposal is queued. The citation task is `runid-seat`. The decision task is the queued id. They are stored separately. If they are equal, the row is `later_task_is_the_injection` and names both. The queued id is stored as `queue_id`, not assumed to be every future closed id. A close matches only that id or a `-P<n>` / `-Q` slice. Any other id stays `no_decision_for_trade`.

## What is not linked

A chat answer calls `mark_chat_answer` and records `no_decision` / `chat_answer_not_a_ticket`. There is no structured acceptance id on that path.

`_engineer_answer` uses `reply_to` or the thread `root` the caller already passed. The question handler passes `msg["id"]`. If both are missing, the trace row is `missing_task_id`. No id is invented.

A code-hands return string is not parsed into a ticket. Half-bank and rebook do not create a second decision. The residual position keeps the parent id. The closed slice is linked only by the suffix rule above.

No live trace row is rewritten. A close that arrives before the ticket stays `no_decision_for_trade`; a later ticket does not edit that row.
