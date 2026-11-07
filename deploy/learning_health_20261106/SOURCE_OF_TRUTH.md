# Learning source of truth

Decisions read `state/thf/seat_brain/<seat>/lessons.jsonl` through `seat_brain.prompt_block`. The citation record is `state/thf/lesson_trace.jsonl`.

`state/thf/learning/` is the daily index. `SOURCE.json` there repeats the live trace. It is not a second lesson corpus. A stale `lessons.md` in that folder is not what a seat is prompted with. `state/learning/` is the older Veteran Desk tree and is not the THF decision store.

A lesson is included when its ref is placed in a prompt. It is cited only when the reply names that same ref. It is validated only when a later book close has the same trade id and a book `net_aud`. Otherwise the trace says `insufficient_data`. "I learned" does not validate anything. Promotion still requires the existing shadow count and an approver. Risk rails are not edited.

# Grok cooldown

A timeout says the call timed out and names the local cooldown. A usage limit says the limit is cooling and must not be bypassed. A verified model success clears the active cooldown and the current `quota` flag, and appends the old quota or timeout event to history. It does not delete those events and there is no manual clear.

The 19:34 `ActionRequiredError` for the $160 additional usage limit stays in `quota_events` after a later success. Raising that Cursor limit, or waiting for the monthly reset, is the provider's own instruction. This package does not change that limit.

A shadow seat that is busy or cooling is not a finished four-provider pair. The sampler retries only the missing providers, and does not call Grok while its cooldown is active. After three deferred passes the seat is `incomplete` so the other seats still get a turn. `seven_day` stays `not_started` until one task has real answers from all four.

The 21:19 design image upload was stored and read back. It is not the missing later screenshot, and it did not post a chat message. The phone browser path is still unchecked.
