# SuperGrok production release candidate

Not deployed. Not connected. This file is not the staging installer and it is not an approval.

`deploy/owner_chat_20261107/install.py` still refuses `/opt/tfo` and `--activate`. Do not point it at production and do not copy its payload by hand.

`deployment_ready` and `ops_review_ready` stay false. `accepted_approval_sha256` stays null. `t2809u` stays spent. Nothing in this tree starts a unit or restarts a chat service.

The unsigned template is `APPROVAL_TEMPLATE.json`, sha256 `91d0142a28b624f382bb43bc1cb58822760e4964960b86938738d25153d60f31`. It is `example: true`, the nonce is empty, and it is refused. It is not an accepted approval. It does not contain the outer archive hash. A parent who accepts the scope copies it, sets a new authorization id and a nonempty nonce, and sets `example` to false. The sha256 of that new file is a later build input. Do not reuse `t2809u`.

## What the words are

`scope_verbatim` is an operator interpretation of the connection ask. It is not a literal owner quote. `scope_provenance` is `operator_interpretation_not_an_owner_quote`.

The owner transcript lines, copied separately, are:

- t2853: `Can you link Supergrok to the chat as another Ai that’s seperate to Grok Supergrok is the chat function of grok`
- t2854: `Same way we connected Chat GPT`
- t2857: `and make sure that Supergrok is connected`

Those lines are the existing connection instruction. They are not a new click, and they do not make this host connected.

## Preflight this contract binds

Read-only preflight `5b1c028dda1bc25df1abc9a27ba896735dfd34c6a23e4037cfd85606b6ca3f87`, observed 2026-11-07 22:17:56 WITA on `tfo-sgp1` as user `tfo`. All three trees existed and matched on the eight bases and the protected pins, including book module `a739477001c33b23ad87ebdf8ca5fc40c76b441eb13100daf1bc67136eb12c7b`. Ledger data was not read. `127.0.0.1:18892` was closed. The sign-in unit was not started. No browser block was observed, because the page worker was not running.

## Three records

These files are not substitutes for each other.

1. Apply receipt `state/thf/supergrok_prod_20261107_receipt.json` on each written root. It records the file write only. `activated` and `connected` stay false. `pages_started_by_apply` and `chat_services_restarted` stay false. It is not a claim about which processes are running.
2. Lifecycle journal `state/thf/supergrok_prod_20261107_lifecycle.jsonl` on the live root of that invocation. One line when a page-worker or sign-in command actually reaches `systemctl`. It records prior state, units started, and units stopped. `connected` is false. It does not rewrite the apply receipt. It does not read health, and health is not connection.
3. Reply receipt `state/thf/supergrok_prod_20261107_reply.json`. This controller never writes it. A later recorder may write it only after a real reply whose route is `grok_com_page_ui`. A ready composer, an open port, or a running unit is not that file.

`--lifecycle status` is the health read. Absent health is `state: unavailable` and `connected: false`. A body that classifies as ready still has `connected: false`.

## Phase 1 — apply

```
python3 release_control.py --apply --approval APPROVAL.json
python3 release_control.py --restore-files --approval APPROVAL.json
```

`--apply` without `--approval` is `REFUSED_APPROVAL`. While the flags stay false the command is `REFUSED_NOT_READY` and writes nothing.

The write roots are exactly these three, in this order:

- `/opt/tfo/live/thf` — msgdesk loop and `tfo-grok-web.service` execute code from here
- `/opt/tfo/dash/base` — `tfo-dash-thf.service` and `tfo-dash-vd.service` execute `veteran_desk/thf_dash.py` from here
- `/opt/tfo/dash/codemerge` — refresh mirror; bound because the apply writes it; not a proved service working directory

There is no optional codemerge. A two-root approval is `REFUSED_APPROVAL`. A second apply is `REFUSED_RERUN`. `--restore-files` does nothing unless each tree has an applied, unrestored receipt and the backups that receipt names. It does not invent a restored receipt.

The book module, ChatGPT worker, ChatGPT client, seat brain, and round runner are checked and not written. On `/opt/tfo` the book module must still hash to `a739477001c33b23ad87ebdf8ca5fc40c76b441eb13100daf1bc67136eb12c7b`.

Installing these bytes does not load them into a process that has already imported them.

## Phase 2 — chat service refresh

Not performed by this controller. Not in the page-worker allowlist. `systemctl` of these units is `REFUSED_LIFECYCLE`.

- `tfo-dash-thf.service` has no ExecReload. It is a long-running `/opt/tfo/venv/bin/python /opt/tfo/dash/base/veteran_desk/thf_dash.py`. A dash-only restart is what would load the THF UI imports. That restart is a separate authorization, not this command.
- `tfo-thf-msgdesk.service` is a long-running `/opt/tfo/venv/bin/python -m veteran_desk.thf_msgdesk loop` with working directory `/opt/tfo/live/thf`. Its source imports `ai_router`, `ai_readiness`, `thf_funder`, and `thf_chat_home`. A restart of this unit is its own authorization, and only after in-flight work is drained and queued requests are preserved.
- `tfo-dash-vd.service` imports the same dash/base `thf_dash.py` and is outside this THF scope. Leave it alone. A dash-thf restart does not recycle it. Its already-imported modules stay old. Do not claim that UI was updated.
- `tfo-thf-engine.service`, `tfo-thf-tick.service`, `tfo-chatgpt-web.service`, and `nginx.service` stay up. The preflight did not prove the engine had imported the eight files. No engine hot reload and no debugger.

## Phase 3 — page worker

```
python3 release_control.py --lifecycle status
python3 release_control.py --lifecycle start-page-worker --confirm OWNER-PAGE-WORKER --approval APPROVAL.json
```

Refused until every allowlisted file on all three roots matches its target digest (`REFUSED_NOT_APPLIED`, and no `systemctl`). The live `tools/grok_web.py` is still the exiting stub (`b7b2fac73e03c600fdb7078b8e55352dc0a5dd52f3a63896fe8e196a7abdae4b`) until apply. `tfo-grok-web.service` runs `/opt/tfo/venv/bin/python /opt/tfo/live/thf/tools/grok_web.py` as user `tfo`, with `Restart=always` and no ExecReload. Starting that stub would churn. Do not start it before the target hash is on disk.

The controller argv is only `systemctl start`, `systemctl stop`, or `systemctl is-active --quiet` for `tfo-grok-xvfb.service` and then `tfo-grok-web.service`. No shell and no sudo. The xvfb unit itself is non-shell (`/usr/bin/Xvfb :89 -screen 0 1280x900x24 -nolisten tcp`). The web unit itself is non-shell. An already-active unit is left running; a failure stops only units this invocation started.

The units run as user `tfo`. This package does not change sudo policy and does not assume an unsudoed `systemctl start` works. It also does not add `sudo` to the argv.

After a real start, `127.0.0.1:18892` may be read with `--lifecycle status`. That read is not an ask and not a connection.

## Phase 4 — sign-in window

```
python3 release_control.py --lifecycle signin-window --confirm OWNER-PAGE-WORKER --approval APPROVAL.json
```

Separate from the page worker, and also refused until the eight targets verify. The controller argv is non-shell `systemctl` of `tfo-grok-signin.service` only.

The unit's own ExecStart is a shell: `/bin/sh -c` running x11vnc on `127.0.0.1:5989` and websockify publishing Tailscale `100.120.8.11:18893` (not loopback-only). `RuntimeMaxSec=21600`. The preflight elided the full x11vnc arguments; this package does not invent them and does not claim the unit is non-shell. Sign-in was not started. It is not port 18892.

## Phase 5 — routed reply

Connection is a reply with route `grok_com_page_ui`. This package does not write that receipt and does not send an ask.

`python3 tests/fixture_matrix.py` prints `FIXTURE_OK`. It checks the staging installer still refuses production and activation. Its synthetic approval is not an owner approval, and it does not flip the shipped flags.
