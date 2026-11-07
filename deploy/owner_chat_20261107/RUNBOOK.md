# Owner chat package

Not deployed. SuperGrok is not connected. `/opt/tfo` stays refused. `--activate` stays refused. Do not copy files by hand if preflight refuses.

From this directory:

```
python3 install.py --root /path/to/review-tree
python3 install.py --root /path/to/review-tree --apply
python3 install.py --root /path/to/review-tree --rollback
python3 tests/fixture_matrix.py
```

`--apply` is only for a review tree whose files match the capture bases in `MANIFEST.json`. It writes a backup under `state/thf/owner_chat_20261107_backup/` and a receipt. A second apply prints `REFUSED_RERUN`. Rollback restores that backup and does not start a service.

Expected production preflight:

```
REFUSED_ROOT
```

Expected activation attempt, with or without `--apply`:

```
REFUSED_ACTIVATION
```

The page worker listens on `127.0.0.1:18892` only after a later, separate activation that this package does not perform. Sign-in is a normal grok.com page. This package does not open it, does not read cookies, and does not pass a challenge. ChatGPT files, when present, must stay at the pinned hashes or the apply prints `REFUSED_PIN`.

`python3 tests/fixture_matrix.py` prints `FIXTURE_OK` when the review apply, repeat refusal, rollback, book invariance, and ChatGPT invariance hold.
