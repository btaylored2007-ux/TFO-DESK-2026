# SuperGrok production release candidate

Not deployed. Not connected. This file is not the staging installer.

`deploy/owner_chat_20261107/install.py` still refuses `/opt/tfo` and `--activate`. Do not point it at production and do not copy its payload by hand.

`deployment_ready` and `ops_review_ready` stay false. `accepted_approval_sha256` stays null. `--apply` requires `--approval`. The controller hashes that file and compares it with `accepted_approval_sha256`. An empty or different hash is `REFUSED_NOT_READY` or `REFUSED_APPROVAL_MISMATCH`. The unsigned template is `APPROVAL_TEMPLATE.json`, sha256 `020dfa0788427d6e08d1a21e0855ed5b6cbca2498037288cf59f9cb3307584b0`. It is `example: true`, so it is refused. It is not the accepted approval. Its scope binds the payload target and base digests, host, roots, protected paths, and service names. It does not contain the outer archive hash. A parent who accepts that scope supplies a nonce and removes `example`; the hash of that new file is a later build input. `t2809u` must not be reused.

`--restore-files` does nothing unless each tree already has an applied, unrestored receipt and backups. It does not create a restored receipt on a tree that was never applied.

`--lifecycle status` is read-only and reports absent health as not connected. `--lifecycle start-page-worker --confirm OWNER-PAGE-WORKER` is wired to `systemctl start` for `tfo-grok-xvfb.service` then `tfo-grok-web.service` only, with no shell. While the readiness flags stay false, that command returns `REFUSED_NOT_READY` and does not call systemctl. `signin-window` is the same gate for `tfo-grok-signin.service` alone.

Ops, only after a later reviewed package that ships a real approval, would use this directory:

```
python3 release_control.py --apply
python3 release_control.py --restore-files
python3 release_control.py --lifecycle status
python3 release_control.py --lifecycle start-page-worker --confirm OWNER-PAGE-WORKER
python3 release_control.py --lifecycle signin-window --confirm OWNER-PAGE-WORKER
python3 tests/fixture_matrix.py
```

`--apply` writes only the manifest files on `/opt/tfo/live/thf` and `/opt/tfo/dash/base`. If `/opt/tfo/dash/codemerge` exists, it must match the same bases and is included. A second apply is `REFUSED_RERUN`. `--restore-files` restores that write set from `state/thf/supergrok_prod_20261107_backup/`.

The book, ChatGPT worker, ChatGPT client, seat brain, and round runner are checked and not written. Engine, tick, firm risk, nginx, and the dash service are not started.

`start-page-worker` starts only `tfo-grok-xvfb.service` and then `tfo-grok-web.service`. The worker listens on `127.0.0.1:18892`. `signin-window` is a separate command for `tfo-grok-signin.service` and is not implied by the worker start. Neither command opens a public port. A page challenge or sign-in stays manual. `status` reports absent health as not connected. There is no fake health body and no fallback to the Cursor Grok route.

`python3 tests/fixture_matrix.py` prints `FIXTURE_OK`. It also checks that the staging installer still refuses production and activation.
