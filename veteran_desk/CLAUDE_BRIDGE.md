# Claude review bridge (PAPER ONLY)

Claude is an advisor. GPT (the desk rules path), Grok (`veteran_desk/llm_backend.py` and `tools/llm_queue_cli.py`) and the TFO gates decide every arm, stop and exit. Claude's verdict is written to a log. It is never applied.

The client is `veteran_desk.claude_review`. The same calls work for Veteran Desk, Taylor Hedge Fund, hunts, OANDA, crypto, the entry engines, Fundamental and V2.

```python
from veteran_desk.claude_review import observe, shadow, tiebreak, review

observe(candidate, decision)     # trade path: returns the same decision, immediately
shadow({"desk": "oanda", "ticker": "EURUSD", "side": "SHORT", "grade": "A", "paper_only": True})
tiebreak({"desk": "vd", "grade": "A+", ...})   # log tag only. does not wait. cannot veto
review({"mode": "code", "desk": "vd", "diff": "..."}, wait=True)          # explicit CLI only
review({"mode": "postmortem", "desk": "thf", "notes": "..."}, wait=True)  # explicit CLI only
```

Trade mode ignores `wait=True`. A disagree, a timeout, a dead bridge or an expired `claude` login does not change shares, the stop, the exit or the arm, and it does not add a wait on the calling thread.

`seat_cli.propose` and `book.tick` call `observe` only after the gate or the arm is already decided. Stops and exits do not call Claude.

## What the daemon does

`tools/claude_bridge_daemon.py` listens on `127.0.0.1` and a unix socket under `state/claude_bridge/`. It keeps a small worker pool, a queue, a 60s CLI timeout (`TFO_CLAUDE_TIMEOUT`), one retry on an empty answer, backoff on subscription rate limits (`TFO_CLAUDE_BACKOFF`, default 20s then 60s) and a short cache of identical prompts (`TFO_CLAUDE_CACHE_S`, default 90s).

It also:

- restarts under launchd `KeepAlive` + `ThrottleInterval` (`launchd/com.tfo.claude-bridge.plist`)
- is health-checked every 60s by `tools/claude_bridge_watchdog.py`, which SIGKILLs a *hung* process, not only a crashed one
- opens a circuit breaker after repeated CLI failures and probes again when the cooldown ends
- sets `login_expired` and `alerts: ["claude_login_expired"]` in the status JSON when Claude asks for `/login` (that alert does not restart the desk)
- reaps zombie `claude` children, kills stale ones recorded in `children.json`, and exits 75 over the RSS cap so launchd starts a fresh process
- appends crash-safe JSONL (`calls.jsonl`, fsync, size rotation)
- runs a startup self-test and a periodic warm ping
- treats a sleep/wake clock jump or a network drop as a reason to kill hung CLI children and probe again, without dropping the socket

`GET /health` and `GET /status`. Status is also `state/claude_bridge/status.json`: `up`, `queue_depth`, `p50_latency_s`, `p95_latency_s`, `error_rate`, circuit, alerts. Nothing in those files is a secret. Prompts are not logged; a prompt that looks like a key is refused.

The CLI is `claude -p` (default `~/.local/bin/claude`). No API key is read or printed.

## Scorecard

```bash
python3 tools/claude_scorecard.py --days 14 --json
python3 tools/claude_scorecard.py --calls state/claude_bridge/calls.jsonl --book reports/veteran_desk_book.json
```

Two weeks by default. Joins each advice row to a later `kind=outcome` line or to a closed paper-book row. Agree-and-won is a hit. Disagree is only a score, never a veto.

```python
from veteran_desk.claude_review import record_outcome
record_outcome("VET-XOM-...", net_aud=120.0, win=True)
```

## Deploy on the Mac

The desk lives at `~/Applications/MultiVenueDesk`. Deploys go through `tools/safe_deploy.sh`. The gate refuses the first four minutes of every quarter hour (`:00–:03`, `:15–:18`, `:30–:33`, `:45–:48`) and refuses a start that would still be running when the next window opens. With the default 450s budget, start only in the `:04–:07` slot of a quarter hour.

```bash
cd ~/Applications/MultiVenueDesk
tools/safe_deploy.sh --tag claude-bridge --note "PAPER ONLY. Claude is advice. It does not arm, size, stop, exit or veto." \
  veteran_desk/claude_review.py \
  veteran_desk/seat_cli.py \
  veteran_desk/book.py \
  veteran_desk/llm_backend.py
```

New tools are not on the tick path until `book.py` imports them, and that import is inside a try. Install the launchd jobs after the gate, still outside a tick window. If the Mac user is not `test`, edit the two plists (`HOME`, paths, `python3`) before bootstrap.

```bash
cp launchd/com.tfo.claude-bridge.plist ~/Library/LaunchAgents/
cp launchd/com.tfo.claude-bridge-watchdog.plist ~/Library/LaunchAgents/
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.tfo.claude-bridge.plist
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.tfo.claude-bridge-watchdog.plist
curl -sS http://127.0.0.1:8766/health
```

`claude` must already be signed in for that user (`claude -p`). The bridge does not log in and does not store a key.

Tests: `python3 -m pytest veteran_desk/tests/test_claude_bridge.py -q`
