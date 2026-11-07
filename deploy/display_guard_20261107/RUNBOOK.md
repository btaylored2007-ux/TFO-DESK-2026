# Display guard

Not deployed. The display mark is PENDING. The archived kit sha `abfc2af4bdeae0959727afb14f308b0f99ebab06eb7e87bce583e872a5f955e4` is unchanged and is not applied.

The book is not a historical pin. Both roots must have the same book bytes at the start. If those bytes change during the copy, the command is `REFUSED_BOOK_CHANGED`, the display files already copied are restored, and the book is left as the runtime wrote it. There is no stale-book rollback and no root override.

Operator apply is refused while `deployment_ready` or `ops_review_ready` is not true. Both remain false. `apply_release` itself raises `REFUSED_NOT_READY` before any write, including a direct call and including when a test environment is set. The independent preflight can show equal book bytes and matching bases and still is not an approval. Do not edit those booleans in place. `t2809u` stays spent. See `LINEAGE.md` for the bound archive `9d91e5fc…` and the unbound file `c7102844…`.

```
python3 release_control.py --approval approval.json --archive fixture/display-epoch-ui-20261107.tar.gz
```

Expected while the flags stay false:

```
REFUSED_NOT_READY
NOT_DEPLOYED
```

Recovery is not a second apply:

```
python3 release_control.py --recover restore-files
python3 release_control.py --recover restart-dash
python3 release_control.py --recover note-stale-receipt
python3 release_control.py --reconcile
python3 tests/fixture_matrix.py
```

`restore-files` restores only the display write-set and is allowed before and after a dash restart. `restart-dash` restarts only `tfo-dash-thf.service` when the write-set already matches its targets. A repeat apply after a commit is `REFUSED_RERUN`.

`note-stale-receipt` writes `restart_outcome` UNKNOWN and `dash_restarted` null when the journal already has `restart_ok` and the receipt is not `RECORDED`. It does not restart dash and it does not rerun apply. UNKNOWN is not a claim that the restart did not happen.

`python3 tests/fixture_matrix.py` prints `FIXTURE_OK`. The dash restarter in that fixture is a fake callable, not systemd. It covers an interrupted copy, a book change during the copy, dual-root book mismatch, a receipt failure before restart, a receipt failure after a fake restart, stale reconcile, `note-stale-receipt`, repeat-apply refusal, and file hashes at reconcile.
