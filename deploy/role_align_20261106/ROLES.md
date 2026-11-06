# Eighteen role alignment

The comparison roster is the same 18 ids already named by `lesson_trace.SEATS` and by `test_thf_deepseek_all_seats_20261105.py`: the 11 proposer seats, `head_of_desk`, `head_trader`, `blind_challenger`, `devils_advocate`, `cfo`, `software_engineer`, and `fixer`. `risk_gate` stays code-only. `root` is not a seat.

`seat_registry.staff_ids()` used to return only persona files whose role is `engineer` or `cfo`. This tree has neither `software_engineer.json` nor `cfo.json`, so the funder list had 16 ids and DeepSeek's `API_SEATS` import dropped `software_engineer`. The legacy allow-list still names that id, so the strict test rejected a real staff role before any request. Named staff are now kept, the same way `head_trader` is kept without a persona file. An unaddressed funder post still does not fan out to staff or `fixer`.

Without `allowed_seats` in DeepSeek config, only `software_engineer` and `fixer` can call that API, and neither can use workload `trader`. The other 16 remain rejected. A config that lists the 18 and sets `trader_enabled` is the existing explicit expansion. Code writes stay with `software_engineer` and `fixer`.

This bundle does not replace `deploy/runtime_20261106/thf-runtime-20261106.tar.gz`. Apply that runtime bundle on its own preflight first. Live DeepSeek config was not in the supplied snapshots. Read-only collection, only if the applied config must be checked, is `/opt/tfo/live/thf/state/thf/deepseek_api/config.json`. Seat files, only if the droplet folder differs from this tree, are `/opt/tfo/live/thf/veteran_desk/seats/`.

Reviewed bases:

- `veteran_desk/seat_registry.py` `44455cc12c2d81df2b1aa176b8094ecfe1538744662b2057d64b71947bf83de9`
- `veteran_desk/thf_funder.py` `6a5bd148bb5ffdb81b94ef242a53f565a21388dbe5e08b949e9959c9809cca2c`

`book.py` `b4453b503f33e34110519bf4d7a9bf391ecc2802edbd87acca3e77dc96b7e891` is not copied. No provider is marked up. `seven_day` stays `not_started`.
