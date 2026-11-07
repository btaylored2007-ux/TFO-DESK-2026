# ChatGPT maintenance-guard client

Not applied. This package does not restart a service and does not copy `book.py`.

The only file is `veteran_desk/chatgpt_bridge.py`.

- Live base `26446b5b61218bfc370fff62988db47efecb7b8afafd15e694ceaecf35deac0c` (7128 bytes), the file on `/opt/tfo/live/thf` and `/opt/tfo/dash/base`.
- Target `c477d99674f5d6dd49bd2be60c21d35f8716fbd1f72e790ad7832c2645920098` (7261 bytes).
- Diff `chatgpt_bridge.diff`, sha256 `53d01dfca611b62762e1d84dcd87eb5f6d1e961cdef9a18b3c5751823509329e`.

`ack_poll_v1` is unchanged. `ask` sets `ack: true` only when `/health` reports `delivery=ack_poll_v1`. A pending ack is polled on that same `request_id`. A dropped ack connection polls and does not submit a second ask. A bridge that does not report `ack_poll_v1` keeps the blocking POST.

## What changed

The delivery probe used to run after the hourly slot was reserved and before the pre-submit maintenance recheck. A hold that became visible in that gap still opened `GET /health`.

The probe now runs only after a maintenance recheck. A second recheck still runs immediately before `POST /ask`, and `mark_submission` stays after that recheck. A `MaintenanceHold` from either recheck removes the reserved hourly slot and does not call `urlopen`.

## Later, one root at a time

Dry-run first. `BLOCKED` copies nothing.

```bash
python3 deploy/chatgpt_maintenance_guard_20261106/install.py --root /opt/tfo/live/thf
python3 deploy/chatgpt_maintenance_guard_20261106/install.py --root /opt/tfo/dash/base
```

Apply only after both dry-runs print `REPLACE` for the bridge and `ALREADY_PROTECTED` for the book:

```bash
python3 deploy/chatgpt_maintenance_guard_20261106/install.py --root /opt/tfo/live/thf --apply
python3 deploy/chatgpt_maintenance_guard_20261106/install.py --root /opt/tfo/dash/base --apply
```

Recorded base is only `26446b5b61218bfc370fff62988db47efecb7b8afafd15e694ceaecf35deac0c`. Any other destination hash is `BLOCKED`. If `veteran_desk/book.py` is present and is not `b4453b503f33e34110519bf4d7a9bf391ecc2802edbd87acca3e77dc96b7e891`, the apply stops.

Restart only the Python process that imports `veteran_desk.chatgpt_bridge`. Do not restart `tfo-thf-tick`. Do not restart the ChatGPT web worker on `127.0.0.1:18891`. Do not clear its queue or its login profile.

## Rollback

The apply stores the live base at `state/thf/chatgpt_maintenance_guard_20261106_backups/veteran_desk/chatgpt_bridge.py` and a receipt at `state/thf/chatgpt_maintenance_guard_20261106_receipt.json`.

Rollback refuses if the destination is no longer the target hash. On success the destination is the base hash again, and the book hash is checked.

```bash
python3 deploy/chatgpt_maintenance_guard_20261106/install.py --root /opt/tfo/live/thf --rollback
python3 deploy/chatgpt_maintenance_guard_20261106/install.py --root /opt/tfo/dash/base --rollback
```

Then restart the same client process that was restarted for the apply. Do not restart `tfo-thf-tick`.

## Checks

```bash
python3 -m pytest deploy/chatgpt_maintenance_guard_20261106/test_ordering.py -q
```

Those tests load this package's payload. They do not write `data/pit`, `state`, or the book.
