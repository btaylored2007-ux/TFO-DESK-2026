# Comparison clock and lesson index

Apply onto the deployed `7049ffa` files. Do not restart `tfo-thf-tick`. This copy does not edit `shadow_samples.jsonl`, the book, or cooldown history.

Bases, from the `7049ffa` manifest:

- `veteran_desk/lesson_trace.py` `e63ed1fb7463bed9f8c748c5f927fd767e5820837e240f111cdddcf9aec90bac`
- `veteran_desk/seat_brain.py` `ab73bb07447775555c5da3b2eb630965b876fe68850c9955b7d1a20ef7638cbb`
- `veteran_desk/thf_shadow_sampler.py` `220226bed82c7285f19ff2dacb01df7948936531be034c620bf3061586110c46`

The first paired row `2026-11-06T15:36:07Z` (23:36:07 WITA) is the clock start. Later runs keep that time. The review is `2026-11-14 08:00 WITA`. Until then `seven_day` is `clock_started` and `seven_day_ready` is false. `window.started` stays true. Those are not the same claim.

`state/thf/learning/SOURCE.json` is rewritten from the existing trace by the sampler and by the nightly review. It does not create lessons. The six admins with no trace row stay `waiting_for_work` until a real chat or nightly review uses them. A cited book close is an outcome link, not evidence of an edge, and it does not promote a rule.

A browser 403 on the public route is the normal tailnet gate. Do not bypass it. The backend image upload already matched its hash.
