# Live-base lesson provenance review

Not an activation. Do not copy this onto `/opt/tfo/live/thf` or `/opt/tfo/dash/base`. Do not restart `tfo-thf-tick`. `--apply` without `--staging` is refused. A production root is refused even with `--staging`.

Bases are the captured Singapore bytes in evidence archive `8f738d5c7579d94569a8b626e76910e804bb60411b567eda702cac2d07ebf4a8`. Live and dash hashes match for the four files. They are not the `ea38885` repo hashes used by `deploy/lesson_provenance_20261107`.

An unknown hash is `BLOCKED`. A missing existing file is `ABSENT_REFUSED` and is not created. `veteran_desk/feed_status_facts.py` is in the payload for review and is not copied. `feed_health.py`, `thf_feedkeeper.py`, and `thf_home.py` were not in the capture. See `DIFF_NOTES.md`.

`GENUINE_VERIFIERS` is empty. `AUTHORITY.rule` is unchanged. The book must remain `b4453b503f33e34110519bf4d7a9bf391ecc2802edbd87acca3e77dc96b7e891`.

```bash
python3 deploy/lesson_provenance_livebase_20261107/install.py --root /path/to/staging-copy
```
