# Display-epoch UI kit

Not deployed. The display mark is PENDING. Do not SSH from a review checkout. Do not rerun the 19:17 archives. Do not use the withdrawn 12:12:34 baseline.

Capture `94c0f2dd4133dac72c506e383f733bb761d15a725364d9484344eab24f8265ec`.
Roots named by that capture: `/opt/tfo/live/thf` and `/opt/tfo/dash/base`.
`veteran_desk/thf_home.py` stays `50d8b3dcab66bf154f2ce21433a84f7147ed446193e6c12f74731e15ed26c1ed`.

Entrypoint, from this unpacked directory:

```
python3 release_control.py --approval approval.json --archive display-epoch-ui-20261107.tar.gz
```

Expected when the approval matches ALLOWLIST.json and both roots still have the captured bases:

```
BOUND_APPLIED
9d91e5fc5c5f84c4b98832389f5cba2be2a4daa1ccff4715677e01c7c1699abb
```

Expected when the approval is the included example:

```
REFUSED_EXAMPLE_APPROVAL
NOT_DEPLOYED
```

Expected on a second run after a deployed receipt:

```
REFUSED_RERUN
NOT_DEPLOYED
```

`--restart-dash` restarts only `tfo-dash-thf.service`. Tick, shadow, and nginx are refused. The journal `state/thf/display_release_20261107_journal.jsonl` is appended and fsynced before the restart. The controller does not call apply or restart a second time when a receipt write fails.

`dash_restarted` is true only when `restart_outcome` is `RECORDED`. It is null otherwise. A failed receipt write does not store false.

Crash outcomes:

- A refusal before any file write leaves the trees and the journal untouched.
- An exception while copying restores the files already written in that attempt. No journal line is kept. Protected pins are not in the write set.
- A receipt OSError before the restart restores those files, appends `restored_before_restart`, and does not restart. Reconcile reports `RESTORED`. A later explicit apply is possible only because the restart never started.
- A crash after `files_committed` and before the receipt leaves the new files. Reconcile reports `FILES_COMMITTED_RESTART_NOT_ATTEMPTED`. Apply returns `REFUSED_RERUN`. Rollback from the backup is still allowed.
- `restart_begin` without `restart_ok` reconciles as `RESTART_UNKNOWN`. A restart command that raises reconciles as `RESTART_FAILED` and the receipt is `UNKNOWN`, not false. Apply returns `REFUSED_RERUN`. Rollback is refused.
- After `restart_ok`, the receipt is written as `UNKNOWN` and only then as `RECORDED`. If that final write fails, the command prints `RECEIPT_AFTER_RESTART_FAILED` and `UNKNOWN`. The journal still has `restart_ok`. Reconcile reports `RESTART_JOURNAL_OK_RECEIPT_STALE` when the stored outcome is not `RECORDED`. Apply returns `REFUSED_RERUN` and does not restart again.

Read-only reconcile, from this directory. It does not apply, restore, or restart:

```
python3 release_control.py --reconcile
```

Expected first line is a JSON list. The second line is `READ_ONLY`. `RESTART_JOURNAL_OK_RECEIPT_STALE` means the journal recorded `restart_ok` and the receipt is not `RECORDED`. Do not run apply again.

The retail installer and `deploy/display_epoch_20261107/install.py` still return `REFUSED_ROOT` for `/opt/tfo`. This kit does not hand-copy.

## Fresh mark, after this read model is on the dash

Not before. Not the withdrawn 12:12:34 reading. The operator captures a new baseline JSON from the live book header (`pnl_since_inception_aud`, `equity_aud`, `seed_aud`, `set_utc`, a new id). Then, on the host:

```
python3 -m veteran_desk.thf_display_epoch establish --state-dir /opt/tfo/dash/base/state/thf --baseline <fresh.json> --confirm OWNER-DISPLAY-RESET
```

Expected:

```
SET
<sha256 of the saved mark>
LEDGER_NOT_WRITTEN
```

A second identical call prints `ALREADY_SET` and does not rewrite the file. There is no HTTP route. The book is not written. Since reset is current equity-minus-seed minus that saved mark. It is 0 only while those figures still match.

## Rollback of a temp or reviewed apply

Backups are `state/thf/display_release_20261107_backup/` on each root. Rollback is `release_control.rollback_release` for a test tree. It restores replaced files, removes the new module, and marks the receipt restored. It does not restart tick, engine, or nginx. A production rollback is an operator action on the host after review, using those backups, not a hand copy from this archive's captured_base tree.
