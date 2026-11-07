# Display guard

Not deployed. The display mark is PENDING. The archived kit sha `abfc2af4bdeae0959727afb14f308b0f99ebab06eb7e87bce583e872a5f955e4` is unchanged and is not applied.

The book is not a historical pin. Both roots must have the same book bytes at the start. If those bytes change during the copy, the command is `REFUSED_BOOK_CHANGED`, the display files already copied are restored, and the book is left as the runtime wrote it. There is no stale-book rollback and no root override.

Recovery is not a second apply:

```
python3 release_control.py --recover restore-files
python3 release_control.py --recover restart-dash
python3 release_control.py --reconcile
python3 tests/fixture_matrix.py
```

`restore-files` restores only the display write-set and is allowed before and after a dash restart. `restart-dash` restarts only `tfo-dash-thf.service` when the write-set already matches its targets. A repeat apply after a commit is `REFUSED_RERUN`.

`python3 tests/fixture_matrix.py` prints `FIXTURE_OK` for an interrupted copy, a book change during the copy, a recorded dash restart, repeat-apply refusal, reconcile, and restore before and after restart.
