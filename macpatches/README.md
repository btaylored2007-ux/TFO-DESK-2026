# Mac-only files this change could not patch

These paths are cited by the audit and are not in this repository. No patch was written for them, because their source is not here and inventing it would be a guess.

| Finding | Path on the desk Mac | Status |
|---|---|---|
| REPO-003 | `/Users/test/Applications/KrakenQuantDesk` `kraken_floor.py` | needs owner |
| REPO-003 | coinspot floor module (loaded via `book_coverage.py` from `KrakenQuantDesk/reports/coinspot_floor.json`; the `.py` is not in this repo) | needs owner |
| REPO-003 | cross-asset floor module (same; only `replica_floor.py` is in this repo) | needs owner |
| REPO-004 | `~/Library/LaunchAgents/com.taylor.familyoffice.plist` and the other installed `com.tfo.*` / `com.taylor.*` jobs that are not under `launchd/` | needs owner |

`tools/launchd_reconcile.py` compares whatever is installed on the Mac with `launchd/` and `ops/`. It does not invent the missing plists.

The firm-halt check those floors should call, once their source is in a repo that can be reviewed, is `book_kill.firm_halt_blocks_entry`. The floors that already call `book_kill.firm_gate` keep that fail-closed behaviour.
