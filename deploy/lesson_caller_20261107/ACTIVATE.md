# Lesson caller review

Not an activation. Do not copy this onto `/opt/tfo/live/thf` or `/opt/tfo/dash/base`. Do not restart `tfo-thf-tick`. `--apply` without `--staging` is refused. A production root is refused even with `--staging`. The refusal is the same rule as `deploy/lesson_feedclose_20261107`.

The prior archive sha256 `fce743e36742c681b1c25dc3fecb7cf6289f3e4b7038eb3fde2b39405b63ed10` stays as the verified feed package. This package adds the captured `veteran_desk/desk.py`.

`desk.py` base `670a55e65f52e6730561f0bac46248ccdee965680444aaff62384e2252124dd4` is the live and dash file from transfer archive `50b1e1fa05a4df29ea940f82edee9e1163afe417a1e7e0d9c30697487ed56843`.

`GENUINE_VERIFIERS` is empty. `AUTHORITY.rule` is unchanged. The book must remain `b4453b503f33e34110519bf4d7a9bf391ecc2802edbd87acca3e77dc96b7e891`.

```bash
python3 deploy/lesson_caller_20261107/install.py --root /path/to/staging-copy
```
