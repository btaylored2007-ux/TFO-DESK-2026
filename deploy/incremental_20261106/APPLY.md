# Guarded increment — parent runs this, the build machine does not

Paper software only. This does not place trades and does not restart `tfo-thf-tick`.

## What drives the shadow trial

- Production inference stays `llm_backend.grok_call` inside `veteran_desk.desk run` → `thf_round.run` (one provider answer, can reach the existing gate).
- Shadow evaluation is a different process: `python -m veteran_desk.thf_shadow_sampler once`.
- Timer unit `tfo-thf-shadow.timer` starts that command every 30 minutes.
- A round only appends the desk prompt to `state/thf/shadow_jobs.jsonl`. It does not call the four providers inline.
- Evidence after a run: `state/thf/shadow_samples.jsonl` (one row per provider, `shadow_calls` 1) and `state/thf/shadow_sampler_status.json`.
- The window in that status file stays `started: false` until one task has a real answer from Claude, ChatGPT, Grok, and DeepSeek. Quota, bridge-off, and missing credentials are `unavailable` or `quota`, not a zero score and not a winner.

## Dry-run

From the unpacked bundle or the repo:

```bash
python3 deploy/incremental_20261106/install.py --root /opt/tfo/live/thf
```

- `ALREADY_PROTECTED veteran_desk/book.py` means the live book hash is `b4453b503f33e34110519bf4d7a9bf391ecc2802edbd87acca3e77dc96b7e891`. It is not copied.
- `BLOCKED` on that file means stop. Do not apply.
- `REPLACE` means the destination hash is one of the recorded base hashes for that path.
- `INSTALL` means the destination file is absent.
- `CURRENT` means the destination already matches the payload.

## Apply

```bash
python3 deploy/incremental_20261106/install.py --root /opt/tfo/live/thf --apply
```

Apply copies nothing if any row is `BLOCKED`. Replaced bytes are saved under `state/thf/increment_20261106_backups/` and listed in `state/thf/increment_20261106_receipt.json`.

## Rollback

```bash
python3 deploy/incremental_20261106/install.py --root /opt/tfo/live/thf --rollback
```

Rollback refuses a path whose current hash is no longer the payload hash. It does not restart services. It refuses if `book.py` is not the audited hash.

## Activation after apply

Do not restart `tfo-thf-tick.timer`. The book tick is unchanged when the book hash matches.

```bash
python3 -m py_compile veteran_desk/book.py veteran_desk/thf_shadow_sampler.py veteran_desk/thf_round.py
python3 -c "import hashlib; p='veteran_desk/book.py'; h=hashlib.sha256(open(p,'rb').read()).hexdigest(); assert h=='b4453b503f33e34110519bf4d7a9bf391ecc2802edbd87acca3e77dc96b7e891', h"
sudo cp deploy/incremental_20261106/tfo-thf-shadow.service /etc/systemd/system/
sudo cp deploy/incremental_20261106/tfo-thf-shadow.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now tfo-thf-shadow.timer
```

Desk and chat processes keep the previous code until they start again. After the hash checks, restart only `tfo-dash-thf` and `tfo-thf-msgdesk` if those units are the ones that import `thf_round` and `thf_msgdesk`. Do that between rounds, not during a book tick.

## Post-deploy checks

- `systemctl status tfo-thf-shadow.timer` shows the timer enabled.
- `state/thf/shadow_sampler_status.json` exists after the first `once` and does not report a winner.
- `state/thf/shadow_samples.jsonl` shows `availability` of `answered` or an explicit `unavailable` / `quota` / `timeout`. Empty provider text is not a paired row.
- Book hash is still the hash above.
- `python3 -m pytest tests/test_bank07_20261106.py` on the box. The four catalyst tests stay red until `filing_freshness.py` is present. `test_half_risk_a_allowed_at_1pct` stays red until `small_coin.py` is present. Do not loosen the 1% floor.
