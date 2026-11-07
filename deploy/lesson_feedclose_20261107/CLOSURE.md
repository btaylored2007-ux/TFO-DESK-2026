# Dependency closure

The keeper and the home page are the captured files plus the feed-fact lines. `feed_health.py` is byte-identical to the capture (`f44734c552d7cb4f751df9d039f414ec19ed9229a01a618f9673a24738a41edb`) and is not replaced. `feed_status_facts.py` imports only `us_rth_open` and `asia_session_open` from that module, plus the standard library. The home page reads `facts` already written on a keeper row. It does not import the new module.

`session_expected` stays unknown except continuous crypto. The weekday clock and the small holiday lists already in the captured `feed_health.py` are `heuristic_session_expected` and `heuristic_not_venue_calendar`. A missing `authorized` field stays entitlement `unknown`. Freshness uses an event timestamp. File mtime is not freshness. HTTP 422 stays `last_failure`.

## Still not closed by a deploy

Nothing in this package is applied. Production roots stay refused. `tfo-thf-tick` is not restarted.

`GENUINE_VERIFIERS` is empty. `RULE_GAP` is not accepted. The live validated count is 0. The same 20 book and gate tests stay unaccepted. `book.py` is not in this package.

`seat_brain.nightly` still calls the brain block with no task id. That stays fail-closed. No nightly ticket is invented. A code-hands return string is not a ticket.

`thf_shadow_sampler.py` and `veteran_desk/desk.py` were not in either capture. The sampler calls `publish_authority` and `load`, which are unchanged, so it is not overwritten. `desk.py` still calls `prompt_block(seat, 1100)`. That call does not write an included row.
