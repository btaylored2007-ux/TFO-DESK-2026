# THF guardian (PAPER only)

Always-on watchdog for the Taylor Hedge Fund desk on the Singapore droplet
(`tfo-sgp1`). It checks the live book, the feeds, the systemd units and each
seat's brain. It restarts what it is allowed to restart. It does not place
broker orders, and it does not call a model on the fill, stop, kill, flatten
or self-heal path.

The AI seats stay. A hunt still goes through `veteran_desk.desk run` when that
seat's brain is up. The code grade is computed first. If the brain has not
answered inside 25 seconds it is not waited for.

## What runs

| Unit | Role |
| --- | --- |
| `tfo-thf-guardian.service` | Long-running. `Restart=always`. Inbox on `127.0.0.1:18871` and a check every 90 seconds. |
| `tfo-thf-guardian-check.timer` | Oneshot every 90 seconds. This is the watchdog over the daemon. |
| `tfo-thf-guardian-check.service` | The oneshot. It does **not** use `Restart=always`. systemd would restart a successful oneshot immediately and the timer would never get a gap. |

The inbox is localhost only. Brett's dashboard stays where it is: nginx on the
Tailscale address `100.120.8.11:18870`, proxying to the dash process on
`127.0.0.1:28870`. The guardian does not bind the dashboard to a public
interface and it does not put the PIN back.

Phone (`/desk/phone`) gets a compact strip: brain counts, a short status line,
a one-line box. It does not draw charts. Desktop keeps the same controls with
the feed, lock and drift detail openable. The existing phone rows and
tap-to-open charts are not rewritten.

## Ports

| Port | Bind | What |
| --- | --- | --- |
| 18870 | Tailscale `100.120.8.11` via nginx | Existing THF dashboard. Unchanged. |
| 28870 | `127.0.0.1` (the dash unit) | Existing dash process. Unchanged. |
| 18871 | `127.0.0.1` only | Guardian inbox. Refuses any other bind. |
| 8790 | existing ai-bridge | Grok then Claude. Used by the message box and by seat hunts. Not used by heal, fill, stop, kill or flatten. |

## Files

All under `/opt/tfo/live/thf` unless noted.

| Path | What |
| --- | --- |
| `state/thf/guardian_status.json` | Latest pass, including `screen_rev` for signed actions. |
| `state/thf/guardian_alerts.jsonl` | The queue the dashboard shows. |
| `logs/guardian_incidents.jsonl` | Append-only incidents. |
| `state/thf/guardian_thread.jsonl` | Message-box thread. |
| `logs/guardian_actions.jsonl` | Confirmed actions. |
| `state/thf/guardian_heal.json` | Restart timestamps and the auto-clear halt. |
| `state/thf/seat_ai_mode.json` | Per-seat brain mode. |
| `state/thf/FUNDER_TAKEOVER` | Present only while Brett has taken the desk. |
| `state/thf/seat_pause/<seat>` | Seat pause. Stops new arms for that seat. The book tick still runs stops. |
| `state/thf/ENGINE_KILL` | Existing tick pause. The tick script exits while this file exists, so stops do not run either. That is the current tick, not a new rule. |
| `state/thf/guardian_action.key` | HMAC key for one-time actions. Created on install, mode `0600`. Not in git. |
| `state/thf/approved_code.json` | Baseline hashes. Drift is alerted. The file is not reverted. |
| `state/thf/feed_active.json` | Which source the doctor currently trusts. |
| `state/funder_messages.jsonl` | Existing funder inbox. Head-trader queue rows go here. |
| `reports/kill_events.jsonl` | Read. The guardian does not append fake kill events. |

State files are written to a temp file, fsynced, then renamed. A full disk
refuses the write and leaves the previous file. A corrupt status file is
renamed `*.corrupt` and the pass starts clean. A timestamp more than 90
seconds in the future is clock drift: signed actions and auto-fills are
refused.

## What a pass checks

Every 90 seconds, in code:

- `tfo-thf-engine`, `tfo-dash-thf`, `tfo-thf-wstape`, `tfo-thf-news` are active.
- `tfo-thf-feed.timer` is active. The feed unit is a oneshot, so "active"
  between runs would be a false alarm. The `.feed_ok` sentinel is the
  freshness check (paths tried: `$THF_GUARDIAN_FEED_OK`, `live/.feed_ok`,
  `state/thf/.feed_ok`, `state/.feed_ok`, `/opt/tfo/data/.feed_ok`).
- The book `state/thf/thf_book.json` is younger than 7 minutes while
  `SGP_PRIMARY` is set, the Mac lease is absent, and `ENGINE_KILL` is absent.
- Engine journal errors are reported. They do not by themselves restart the
  engine.
- Dashboard `/`, `/desk/phone`, `/healthz`, `/api/state`, `/api/ticker` return 200.
- `reports/kill_events.jsonl` can be opened.
- Armed orders whose trigger has traded through and are still unfilled after
  5 minutes. A stale-price block on a delayed venue (ASX included; the book
  gate's `VENUE_DELAY_MIN` still omits ASX) counts as stuck.
- Tick lag, cleared-but-not-armed rows, waiting-list overflow, and a gate
  threshold that does not match `gate_v2.TH`.
- Stale mkdir locks (`.tick.lock` older than 12 minutes). A flock file is
  reported and not deleted: the lock is on the inode.
- Code drift against `approved_code.json`.
- Each seat's brain, and whether five or more trader seats are down.

Restarts use backoff: 60s, then 180s, then 600s, at most 3 in 30 minutes,
then an alert and no further restart. If the deadman restarted the engine in
the last 5 minutes the guardian waits. A tick restart is refused when the
book fails an integrity check (every open or armed position needs a stop and
a size; an open position needs a fill) or a live startup lock is held. A
stale mkdir lock is removed first.

The book bytes are not edited by a restart. Stops, sizes and grades are not
touched by the heal path.

## Feed doctor

| Feed | Freshness | Backup | Action |
| --- | --- | --- | --- |
| WS tape / price | 5 minutes, and per-venue floor+5 (ASX floor is 20, so 25 minutes) | Yahoo fallback file `state/feed_delay.json` | Restart `tfo-thf-wstape` |
| News | 20 minutes | last-good age | Restart `tfo-thf-news` |
| Borrow | 24 hours, the existing rule | last-good age | Start `tfo-thf-borrow` |
| Kraken 24h sweep | 26 hours (the daily timer) | last-good age | Start `tfo-thf-kraken-daily` |
| Yahoo fallback | 30 minutes | none | Start `tfo-thf-feed` |
| Announcements | — | — | **Not wired.** `news_desk.NOT_WIRED` still says ASX, LSE, HKEX and JPX have no machine feed. The doctor reports that. It does not scrape one. |

Failover means the status file names the backup as the source the dashboard
should trust. The doctor does not invent prices and it does not change
`book.stale_limit_min`. Backfill is "start the existing collector again",
with the same backoff as any other restart.

## Lag, stuck orders, auto-clear

- Tick older than 7 minutes, and the book passed integrity: start
  `tfo-thf-tick`.
- Cleared rows that are still not armed are logged. Today's cleared names are
  moved ahead of older rows. Risk, stop, size and grade fields are not
  changed and nothing is deleted. Rows are not auto-acked: the ack rail stays.
- A gate threshold that differs from the approved `gate_v2` numbers is
  logged. The gate file is not rewritten.
- An armed A or A+ that has traded through, or is blocked on a stale delayed
  print, can be filled at its armed entry with the same shares and the same
  stop. Grade B is refused. Cash that would go negative refuses the fill and
  leaves the order armed.
- Three auto-fills in 30 minutes is the budget. The next one **halts**
  auto-clear until the halt window passes. Re-ranking is not counted.

Stale marks are flagged. The first fresh print after a stale stretch is
compared with the stop. A gap through the stop is flagged and the stop is
left where it is, so the book's own gap rule can run on the next tick.

## Brains, per seat

Trader seats: Lena, Rex, Kai, Nico, Dex (trend **and** price_action; two
brains), Theo, Victor, Nora, Iris, Quinn. Admin brains: Marcus, Sloane
(challenger), Jonah (devil's advocate). Ada's risk gate is code and is not
probed.

- The first real call that times out degrades that seat only.
- A ping does not degrade and does not recover.
- Recovery takes two later real successes. It happens on its own.
- While degraded, that seat does not get an AI hunt (`desk run` is not
  started for it). It may still be offered an A+ or A from the code grade,
  at half the planned risk, and never a B. Half of a 1% A is 0.5%, which is
  under the existing 1% floor, so that arm is rejected rather than lifted.
  Half of a 4% A+ is 2% and can pass.
- Five or more degraded trader seats pause **new** arms for the whole firm.
  Opens, stops and exits keep running.
- Theme (one name per theme and side), sector (two names) and venue+side
  (two names) caps reject the extra arm. A missing sector is not armed.

LLM output is merged only when it arrives within 25 seconds and it passes a
schema check, the seat universe, a 3% price-sanity check and the 5% size cap.
The brain cannot lift the code grade. This is the same direction as
`desk._engine_cap`.

## Message box and takeover

Brett types in the strip. Status, armed orders, fills and "why didn't X fill"
are answered from the book and the guardian status. The bridge is asked to
phrase that, and if it is down the same facts are returned with
`bridge=down`.

Whitelist, each one confirmed on the screen and signed with the current
`screen_rev`:

- restart a THF unit (engine, dash, feed, tape, news, tick, queue-answerer,
  borrow, kraken). Anything else is rejected.
- re-run the health pass
- pause or resume the engine via `ENGINE_KILL`
- fill a named stuck A at its armed entry
- push traders (all, or Lena, Rex, Kai, Nico, Dex, Theo, Victor, Nora, Iris, Quinn)

Anything else is queued for Marcus on the existing funder inbox.

Takeover is a separate switch. While it is on, and only with a signed
one-time token that matches the screen revision:

- arm, if the caps and the kill switch allow it (FLAT blocks new arms; the
  existing gate still has to pass before `book.tick` arms)
- close a paper trade at the mark
- move a stop, tighten only (`seat_cli.amend`'s rule)
- pause or resume a seat
- kill and flatten **immediately**. Open paper trades are closed at the mark
  and armed orders expire. This does not wait for the next tick.

A stale `screen_rev`, a reused nonce, an expired token (120 seconds) or a
timestamp in the future is rejected. Email and Telegram hooks stay off unless
`THF_GUARDIAN_EMAIL=1` or `THF_GUARDIAN_TELEGRAM=1` and the matching
`*_HOOK` is a path to an executable. No tokens are in the code.

## Deploy

On the droplet, as a user who can `sudo`:

```bash
bash /opt/tfo/live/thf/thf_guardian/install_sgp.sh
systemctl restart tfo-thf-guardian.service tfo-dash-thf.service
systemctl start tfo-thf-guardian-check.timer
```

The script copies this directory to `/opt/tfo/live/thf/thf_guardian`, installs
the three units, appends the dashboard mount and the `desk.enqueue` arm guard
if those files are missing the marker, writes `approved_code.json` if it is
missing, creates the action key, and appends a line to
`/opt/tfo/logs/DEPLOY_LOG.txt`. Every file it replaces is copied to
`<file>.bak-<YYYYMMDD>-guardian` first.

Do not rely on `run_thf_tick_sgp.sh` copying `/opt/tfo/data/code_push`. That
copy is disabled. This tree is droplet-native. Install is a copy, not a
code_push.

The script is not run from this repo. Tests are `python3 -m unittest discover -s thf_guardian/tests`.

## Rollback

```bash
# restore each file the install printed, for example
cp -a /opt/tfo/live/thf/veteran_desk/thf_dash.py.bak-YYYYMMDD-guardian \
      /opt/tfo/live/thf/veteran_desk/thf_dash.py
systemctl disable --now tfo-thf-guardian.service tfo-thf-guardian-check.timer
systemctl daemon-reload
systemctl restart tfo-dash-thf.service
```

The `.bak-*-guardian` copies are the rollback. Status and incident logs can
stay; they are not ledgers.

## What the existing watchdogs already cover

**Deadman** (`tfo-thf-deadman.timer`, every 120s, `run_thf_deadman_sgp.sh` →
`vd_deadman.py`). Runs only when `SGP_PRIMARY` is set and the Mac lease is
absent, and only if `vd_deadman.py` contains `TFO_DEADMAN_RESTART_CMD`.
Restarts the engine when the heartbeat is older than 5 minutes, at most 3
times in 30 minutes, and marks DEGRADED after 15 minutes. It reads dash
`/healthz` on `127.0.0.1:28870` and does not restart the dash. It does not
look at the feed, tape, news, borrow, Kraken, the book age, `.feed_ok`,
`kill_events.jsonl`, stuck orders, or seat brains.

**Health** (`tfo-thf-health.timer`, every 10 minutes, `thf_health.sh`). Checks
the lease heartbeat (6 minutes), the last tick's `rc=0`, `ENGINE_KILL`, the
book kill-switch state, pit sqlite, dash `/healthz`, `/api/state`,
`/api/chart`, `/api/ticker`, and the feed service on `:8787`. On failure it
restarts `tfo-dash-thf` and starts `tfo-thf-tick` **once**, then writes
`/var/www/tfo-hub/lease/thf_health.json` and, if still red,
`/opt/tfo/logs/ALERTS.log`. It does not watch wstape, news, the engine unit,
`.feed_ok`, stuck orders, or brains. It has no backoff beyond that single
retry.

**Dash overlord** (`tfo-thf-overlord.timer`, every 300s). Compares
`/api/state` with the book, the top10 files, coverage, the engine heartbeat
and the deadman file. It may delete the dash cache and restart
`tfo-dash-thf`. It does not edit trading data. It does not restart the
engine, the feed, the tape or the news collector.

**Gate overlord** (inside `vd_engine`, about every 15 minutes). Reports an
armed A/A+ unfilled for more than 8 market hours. The comment in that
function says the engine does not edit the book. It does not fill at entry
and it does not treat a 5-minute stale ASX block as stuck.

The guardian does not replace those three. It covers the gaps above and it
will wait if the deadman has just restarted the engine. Health's
`/api/chart` check is still only in `thf_health.sh`.

## Where THF still needs a model to arm, and what to do about each

These stay. The point of the list is the fallback, not a removal.

| Place | What the model does | If it is down |
| --- | --- | --- |
| `desk.run` → `llm()` → `llm_backend.grok_call` | The seat proposes a ticker, side, entry, stop, target and grade. This is the arm. | Do not block. The code grade from `vd_engine.grade` (same thresholds, copied in `gradewake.code_grade`) is the proposal. It still has to pass `gate_v2` and the caps. A degraded seat is not sent to `desk run`. |
| `desk.run` challenger and devil's advocate | Advisory debate. | `gate_v2` already treats a missing debate as a warning, not a block. Leave that. Do not invent an "agree" verdict. |
| `desk.head_of_desk` | Marcus's note. | The code note underneath it already runs when the model returns nothing. It does not arm. |
| Sub-agents in `desk.run` (research, chart, filings, second opinion, news) | Notes on the trade. | Skip them. They are not the gate. |
| `vd_engine.second_looks` / `gpt2l` | Advisory second look after a wake. | Already non-blocking. A missing file is "none". |
| `learning.critique_async` and the lesson loop | The learning Brett asked to keep. | `learning.py` already tags a down model MISSING and does not block the tick. Keep that. |
| `thf_arm_review` | Grok and Claude after an arm. | Advisory and fail-open. It never writes the book. |
| `queue_answerer_sgp.py` | Bridge for the LLM queue and for Brett's notes. | The message box answers from the status file when the bridge errors. The queue item stays fail-open, as that process already does. |
| `seat_cli.propose(..., run_debate=True)` | Optional debate. | Default is already `run_debate=False`. Takeover does not turn it on. |

`book.tick` is already deterministic: fills, stops, the −8%/−12% kill switch
and the gap stop do not call a model. The autonomy test blocks `urlopen` and
still sees an armed limit fill and a stop-out, then a guardian engine restart,
with no URL fetched.

## Honest limits

- `ENGINE_KILL` pauses the whole tick, including stop management. Seat pause
  is the switch that blocks new arms and leaves the tick running.
- Flatten of an FX position is refused unless the caller passes an explicit
  notional. The guardian will not invent an FX rate.
- Announcement wires are still not built. The doctor says so.
- Code drift is detected, not rolled back. `code_push` is gone on purpose.
- The half-size rule does not punch through the existing risk floor.
- The oil theme list in `caps.py` is the book's oil set. Other themes are
  honoured when the proposal or the position carries `theme`. A name with no
  theme is not given one.
- The plan file `/opt/tfo/live/thf/AUTONOMY_PLAN.md` was not on this machine.
  The behaviour above is Claude's review as Brett relayed it.
