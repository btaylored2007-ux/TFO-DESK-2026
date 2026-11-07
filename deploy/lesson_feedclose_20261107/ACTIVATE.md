# Feed and provenance closure, review only

Not an activation. Do not copy this onto `/opt/tfo/live/thf` or `/opt/tfo/dash/base`. Do not restart `tfo-thf-tick`. `--apply` without `--staging` is refused. A production root is refused even with `--staging`.

This package keeps the production-root refusal from `deploy/lesson_provenance_livebase_20261107`. That earlier archive, sha256 `d43852c7ff1c4fac10f0e4a2a1b87d9d24fa88eca331513a70020429d72f06aa`, is not applied.

Bases are the captured Singapore bytes. Feed capture sha256 `71dfa9d9ecf7a3ed8f4e661dd27b7d7a7b0e867e8fd086aeb33cb01b271aaf2e`. Provenance capture sha256 `8f738d5c7579d94569a8b626e76910e804bb60411b567eda702cac2d07ebf4a8`. Live and dash match.

`veteran_desk/feed_health.py` is checked and not copied. `veteran_desk/feed_status_facts.py` is copied only when the destination file is absent. An existing different file is `NEW_FILE_CONFLICT` and is left in place. An unknown hash is `BLOCKED`. A missing existing file is `ABSENT_REFUSED`. A failed copy restores the files already written in that attempt.

`GENUINE_VERIFIERS` is empty. `AUTHORITY.rule` is unchanged. The book must remain `b4453b503f33e34110519bf4d7a9bf391ecc2802edbd87acca3e77dc96b7e891`.

```bash
python3 deploy/lesson_feedclose_20261107/install.py --root /path/to/staging-copy
```
