# Ordering review

Compared `26446b5b61218bfc370fff62988db47efecb7b8afafd15e694ceaecf35deac0c` with `c477d99674f5d6dd49bd2be60c21d35f8716fbd1f72e790ad7832c2645920098`. The diff moves the existing delivery probe and request build to after a new `client_guard()` and leaves the pre-POST `client_guard()` and `mark_submission()` where they were. No other line in `ask` changed.

## Guards

Success path calls `client_guard` three times:

1. Before `enabled()`, `busy()`, the deadline check, and the hourly-slot reservation. A hold here reserves nothing and opens nothing.
2. After the slot is reserved and before `health()`. A hold here removes that slot and does not open `/health` or `/ask`.
3. After the probe and the request object are built, immediately before `mark_submission()` and `POST /ask`. A hold here removes the slot. `mark_submission` is not called. `POST /ask` is not called.

`client_guard` only reads the control file. It does not take the hourly-slot lock and it does not append to `_T`. The extra call does not reserve a second slot.

## Quota rollback

The timestamp is appended once, under `_LK`, before the try. Both inner holds are inside that try. The `MaintenanceHold` handler takes `_LK` and removes that same timestamp. The single-thread proofs show `_T` empty afterwards. `list.remove` deletes one equal value. Two reservations that happen to share one `time.time()` value can still drop the wrong one of those two. That collision existed before this diff. This diff does not append a second timestamp.

## Network

A hold that is already visible at guard 1 or guard 2 performs no `urlopen`. A hold that becomes visible during the probe can complete that one `GET /health`, because the call is already in flight. Guard 3 then runs before `mark_submission` and before `POST /ask`, so the model submission is not sent and the slot is removed. The gap between guard 3 returning and `urlopen` starting is the same gap the previous pre-POST guard had. This diff does not widen it. It closes the earlier gap in which `/health` ran with no recheck in front of it.

## Ack poll

`use_ack` is still `health().get("delivery") == "ack_poll_v1"`. `body["ack"]` is set only then, and the request object is built after that assignment. A pending response still polls `GET /result?request_id=` for the same id. A URL error on an ack request still polls and does not submit another ask. A health payload without `ack_poll_v1` still uses one blocking POST and no poll.
