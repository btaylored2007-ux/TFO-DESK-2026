# Safe activation — parent runs this on Singapore

Paper only. This does not place a trade and does not restart `tfo-thf-tick`. Cloud has no live credentials, so the seven-day provider calls start only after the steps below.

Book hash that must remain: `b4453b503f33e34110519bf4d7a9bf391ecc2802edbd87acca3e77dc96b7e891`.

## 1. Dry-run

```bash
python3 deploy/incremental_20261106/install.py --root /opt/tfo/live/thf
```

Stop if any line is `BLOCKED` or `PAYLOAD_HASH_MISMATCH`. `ALREADY_PROTECTED veteran_desk/book.py` means the live book matches and will not be copied. `REPLACE` is allowed only when the destination hash is one of the recorded base hashes. `CURRENT` means the file is already the payload.

## 2. Apply and rollback

```bash
python3 deploy/incremental_20261106/install.py --root /opt/tfo/live/thf --apply
python3 deploy/incremental_20261106/install.py --root /opt/tfo/live/thf --rollback
```

Apply copies nothing when any row is blocked. Replaced bytes go to `state/thf/increment_20261106_backups/`. Rollback refuses a file whose current hash is no longer the payload, and refuses if `book.py` is not the hash above.

## 3. Checks before any restart

```bash
python3 -m py_compile veteran_desk/book.py veteran_desk/thf_shadow_sampler.py veteran_desk/thf_round.py veteran_desk/desk.py
python3 -c "import hashlib; h=hashlib.sha256(open('veteran_desk/book.py','rb').read()).hexdigest(); assert h=='b4453b503f33e34110519bf4d7a9bf391ecc2802edbd87acca3e77dc96b7e891', h"
python3 -m pytest tests/test_bank07_20261106.py tests/test_fillfix_20261103.py veteran_desk/tests/test_review_20261030.py::test_half_risk_a_allowed_at_1pct veteran_desk/tests/test_shadow_sampler_20261106.py veteran_desk/tests/test_hub_brand_20261106.py -q
```

`test_intent_routing_20261104.py::RoutingIntegration::test_advisor_uses_original_intent_and_respects_ask` stays red until `queue_answerer_sgp.py` is the real live file. Do not skip it and do not invent that file.

## 4. What starts the measured seven-day calls

The comparison is not a production log. `note_production` writes `shadow_calls` 0 and `kind` `production_inference`. Those rows do not start the clock.

The trial is `python -m veteran_desk.thf_shadow_sampler once`, driven by `tfo-thf-shadow.timer` every 30 minutes. Each run calls the existing `claude_brain.ask`, `chatgpt_bridge.ask`, `thf_grok.ask`, and `deepseek_bridge.ask` for at most `max_jobs_per_run` queued prompts (2). It does not import the book and does not enqueue an order.

Required on the droplet, already expected to be present there:

- `state/thf/shadow_sampler.json` with `"enabled": true` (the apply copies this when the destination is absent).
- The four provider clients and their existing credentials. This archive does not add a key.
- `systemctl enable --now tfo-thf-shadow.timer` after the unit files are copied:

```bash
sudo cp deploy/incremental_20261106/tfo-thf-shadow.service /etc/systemd/system/
sudo cp deploy/incremental_20261106/tfo-thf-shadow.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now tfo-thf-shadow.timer
```

Do not restart `tfo-thf-tick`. Desk and chat pick up `thf_round.py` and `thf_msgdesk.py` the next time those processes start. Restart `tfo-dash-thf` and `tfo-thf-msgdesk` only between rounds if those units import the new files.

Evidence after the first real answers:

- `state/thf/shadow_jobs.jsonl` — queued, started, finished. Eighteen seats are queued. A finished seat is queued again after `resample_after_s` (6 hours).
- `state/thf/shadow_samples.jsonl` — one row per provider, `shadow_calls` 1, `kind` `shadow_evaluation`. Unavailable, quota, and timeout stay explicit and are not scored.
- `state/thf/shadow_sampler_status.json` — `window.started` becomes true only when one `task_id` has a real answer from Claude, ChatGPT, Grok, and DeepSeek with the same prompt hash and `data_asof`. `global_winner` stays null. `n` below 20 per cell stays a gap.

The seven-day review time in the bench module is `2026-11-13T21:00:00+08:00`. The clock itself starts at the first paired row, not at deploy time and not from a fixture.

## 5. ChatGPT stall, recorded from the 22:04–22:07 read-only audit

Health, state, and chat HTTP were fast (about 1–55 ms, chat up to 227 ms). A ChatGPT ask took about 169 seconds and the bridge moved between busy and up. Forty-one broken-pipe errors in 90 minutes happened while the bridge wrote a finished ask result; the client had already gone. At 22:07 the bridge queue was 0, state logged_in, and the last ask was ok. There was no out-of-memory evidence and no msgdesk error. No new user image has been stored since 5 Nov, so the picture report is not attributed to the server.

`POST /ask` in `tools/chatgpt_web.py` waits on the browser job, then writes the JSON. A disconnect at that write is a broken pipe, not a cancelled ask. A deadline that expires first returns HTTP 504 and cancels the request. An asynchronous ack/poll was not added: `tools/chatgpt_admission.py` is not in this tree, and the live bridge file was not replaced. The chat chips now say busy, timed out, or unknown instead of up when the last ask failed or the Grok check is older than five minutes. A stale Grok success is not shown as up.

Do not restart `tfo-thf-tick` and do not clear provider queues for this note.

## 6. What this does not activate

No billing, tenant, or new data vendor. No live broker order. The hub display name is Transparent Trading and the label is PAPER. Internal routes stay THF.
