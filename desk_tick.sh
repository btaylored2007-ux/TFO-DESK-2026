#!/bin/bash
# Taylor Family Office — the firm's heartbeat.
#
# One locked, ordered tick of everything: heal -> watch carry -> execute carry ->
# tournament -> books -> risk -> report -> floors. Run by BOTH launchd (hourly,
# survives reboots, needs no Claude app) and the Claude scheduled task (2-hourly,
# adds analysis + operator notification). The lock makes overlap harmless:
# whoever arrives second simply skips.
#
# PAPER ONLY. Everything this touches is simulated.

DESK="/Users/test/Applications/MultiVenueDesk"
KRAKEN="/Users/test/Applications/KrakenQuantDesk"
LOG="$DESK/reports/desk_tick.log"
LOCK="$DESK/reports/.tick.lock"

# ---- single-flight lock (mkdir is atomic; stale after 30 min) ----
if ! mkdir "$LOCK" 2>/dev/null; then
  if [ -n "$(find "$LOCK" -maxdepth 0 -mmin +30 2>/dev/null)" ]; then
    rmdir "$LOCK" 2>/dev/null; mkdir "$LOCK" 2>/dev/null || exit 0
  else
    echo "$(date '+%F %T') tick skipped — another tick is running" >> "$LOG"
    exit 0
  fi
fi
trap 'rmdir "$LOCK" 2>/dev/null' EXIT

echo "==== $(date '+%F %T') tick start ($1) ====" >> "$LOG"

# ---- interpreter (pinned 2026-08-11, operator-authorised) ----------------
# launchd runs this script with a minimal PATH, where `env python3` resolves to
# /usr/bin/python3 — the system Python, which has NO numpy and NO yfinance. That
# silently killed growth_book.py, value_book.py, risk_engine.py and 5 tournament
# probes on EVERY launchd tick, while operator-run ticks (richer PATH) passed.
# Half the firm's hourly marks were therefore never taken. Never go back to a
# PATH-relative interpreter here: pick the first one that can actually import the
# firm's dependencies, and say loudly in the log if none can.
PY=""
for c in /Library/Frameworks/Python.framework/Versions/3.14/bin/python3 \
         /usr/local/bin/python3 /opt/homebrew/bin/python3 \
         "$(command -v python3 2>/dev/null)" /usr/bin/python3; do
  [ -n "$c" ] && [ -x "$c" ] || continue
  if "$c" -c 'import numpy, yfinance' >/dev/null 2>&1; then PY="$c"; break; fi
done
if [ -z "$PY" ]; then
  PY="$(command -v python3 2>/dev/null || echo /usr/bin/python3)"
  echo "!! NO interpreter with numpy+yfinance found — books/risk WILL NOT update" >> "$LOG"
fi
echo "-- interpreter: $PY" >> "$LOG"

FAILED_STEPS=0
# 2026-10-21: per-step wall-clock cap. A step blocked on a dead socket or a hung
# file open (trend18/yfinance, firm_risk on an iCloud path) used to stall every
# later step and freeze firm_risk.json. perl's alarm survives exec, so SIGALRM
# (exit 142) ends just that step and the tick moves on. Override per step with
# STEP_TIMEOUT=<sec> run ...; default DESK_STEP_TIMEOUT (600s).
DESK_STEP_TIMEOUT="${DESK_STEP_TIMEOUT:-600}"
# 2026-10-21 (Brett): whole-tick budget 25 min, below the 30-min stale-lock rule, so
# a slow tick can never overlap the next one. Once spent, remaining steps are
# skipped and logged; each step's cap is also clamped to the budget left.
TICK_BUDGET_S="${TICK_BUDGET_S:-1500}"
TICK_T0=$(date +%s)
SKIPPED_STEPS=0
budget_left(){ echo $(( TICK_BUDGET_S - ($(date +%s) - TICK_T0) )); }
run(){
  local t="${STEP_TIMEOUT:-$DESK_STEP_TIMEOUT}"
  local left; left=$(budget_left)
  if [ "$left" -le 0 ]; then
    SKIPPED_STEPS=$((SKIPPED_STEPS + 1))
    echo "!! TICK BUDGET ${TICK_BUDGET_S}s SPENT — skipped: $1" >> "$LOG"
    return 0
  fi
  [ "$left" -lt "$t" ] && t="$left"
  echo "-- $1" >> "$LOG"
  (cd "$2" && exec /usr/bin/perl -e 'alarm shift; exec @ARGV or exit 127' "$t" "$PY" $1) >> "$LOG" 2>&1
  local rc=$?
  if [ "$rc" -eq 142 ]; then
    echo "!! STEP TIMEOUT after ${t}s: $1" >> "$LOG"
  fi
  if [ "$rc" -ne 0 ]; then
    FAILED_STEPS=$((FAILED_STEPS + 1))
    echo "!! STEP FAILED exit=$rc: $1" >> "$LOG"
  fi
  # Continue checking other books' exits even when one route fails.
  return 0
}

run desk_healer.py       "$DESK"

# ---- engine-supervisor watchdog (added 2026-08-07, head-of-desk decision) ----
# The engine's supervisor can die and orphan a still-healthy engine (no watchdog).
# We NEVER restart the live engine directly. Instead, if the supervisor process is
# absent, relaunch it via its own launchd job: the supervisor is single-instance +
# port-checked, so it EXITS CLEAN when the engine already holds :18799 (verified
# 2026-08-07 — engine stayed single, equity intact), and RESTARTS the stack only if
# the engine is actually down. Safe because the supervisor self-guards; this closes
# the watchdog gap without touching a live/flat engine.
if ! pgrep -f kraken_supervisor.py >/dev/null 2>&1; then
  echo "-- engine supervisor absent -> kickstart launchd job (self-guarded)" >> "$LOG"
  launchctl kickstart -k "gui/$(id -u)/com.local.kraken-quant-desk" >> "$LOG" 2>&1 ||     launchctl kickstart -k com.local.kraken-quant-desk >> "$LOG" 2>&1 || true
fi
run carry_monitor.py     "$DESK"
# ARCHIVED 2026-09-25 (HT Option A; paper-trading step removed, book archived via book_archive.py): run carry_paper_desk.py  "$DESK"
run carry_hurdle_monitor.py "$DESK" # read-only; legacy funding remains flagged, never guessed
run "tournament.py --board" "$DESK"   # 2026-09-25 HT: sleeve tick disabled (capacity_probe archived; all other sleeves retired); board only
# Firm halt is published before any entry-capable book. Uses ledgers as of the previous
# tick (same freshness the old end-of-tick placement gave the next tick's entries) and
# is no longer the step most likely to be dropped when the 25-minute budget runs out.
# Floors and books read reports/firm_risk_state.json before opening. Exits are never blocked.
run firm_risk.py         "$DESK"
# RETIRED 2026-09-19 — growth_book archived; do not resurrect
# run growth_book.py       "$DESK"
# RETIRED 2026-09-19: archived empty worldwide_gap_book, registry removed.
# Keep its history; do not recreate a ledger or invoke its writer.
run gap_close_levered_book.py "$DESK"   # Gap-Close Levered (funder 2026-09-16): residual → TQQQ/SOXL; PAPER ONLY
run value_book.py        "$DESK"
# ARCHIVED 2026-09-25 (HT Option A; paper-trading step removed, book archived via book_archive.py): run carry_newnames_probe.py "$DESK"   # $10k paper XMR/HYPE basis-carry probe (sealed 2026-08-10)
run maker_fill_probe.py     "$DESK"   # fill-rate at scale: 25 pairs x 6 offsets/tick (was N=6 in a week)
# RETIRED 2026-09-19 — do not resurrect crypto_rumour_book
# run crypto_rumour_desk.py   "$DESK"   # $20k declared forward test (prereg 1b3bc7e77e2a7467); NO validated edge
run "convexity_sleeve.py --tick" "$DESK"   # $150k defined-risk 21x shot (funder 2026-09-03); NO validated edge; max loss capped at seed
run "floor_exit_shadow.py --tick" "$DESK"   # PAIRED shadow of kraken_floor: real stops + 14d min hold (prereg 2026-09-05); $0 deployed
# RETIRED 2026-09-19 — dated_event archived
# run "dated_event_book.py --tick" "$DESK"   # dated-event LONG book, $75k from sleeve (prereg 2026-09-06); fills at session open, holds through the event
run maker_calibration_probe.py "$DESK"   # $10k maker-fill calibration probe, risk-tool NOT edge (prereg 2026-08-22; reads ws_tape.sqlite)
# RETIRED 2026-09-19 — punt_24h archived
# run punt_24h.py         "$DESK"   # 24h punt (funder-directed 2026-08-26; quarantined from the board, marks + auto-closes at deadline)
run tfo_corporate_actions.py "$DESK"   # 2026-09-29: V1 splits/dividends (Yahoo events) applied before the book marks; logs every action
run fundamental_book.py  "$DESK"   # TFO Fundamental Desk: $500k funder-directed
run "seed_recovery_sleeve.py tick" "$DESK"   # Seed Recovery Sleeve (funder 2026-09-16): dual-momentum idle-cash deploy inside fundamental book; PAPER ONLY
run "profit_gate_sleeve.py tick" "$DESK"   # Profit-Gate Sleeve (funder 2026-09-16): dual-mom ex-USO/DBC; HARD +5% net profit gate; PAPER ONLY
                                    # global long/short book (registration
                                    # reports/research_global_value_screen_2026-08-27.md).
                                    # Marks BEFORE risk_engine and firm_risk so the
                                    # firm's risk numbers include it from the first tick.
run "fundamental_spec_book.py --mark" "$DESK"
                                    # SPECULATIVE SLEEVE: $50k of NEW capital
                                    # (funder-directed 2026-08-28, Amendment 1 to
                                    # SPECULATIVE_SLEEVE_PREREGISTRATION.md).
                                    # Long only, penny/exploration tier, 15% net
                                    # exit gate against a 350bps round trip.
                                    # Separate ledger, separate kill (-40% sleeve,
                                    # -50% position); NEITHER force-liquidates.
                                    # Marks BEFORE risk_engine like the main book.
run fundamental_watch.py "$DESK"   # catalyst calendar: raises any position whose
                                    # pre-registered KILLS IT date has come due.
                                    # Keeps the calendar; never judges the thesis.
run hunt_cross_stage.py "$DESK"   # 2026-10-28 13:40 Brett approve-all #3: hunt books stage setups outside their own market (separate xstaged.json; stage only, funder tick, nothing auto-arms)
run trend18_trail.py "$DESK"   # 2026-10-28 13:40 Brett approve-all #2: LIVE (paper) weekly 20% trail on Trend18 UPRO/TQQQ; exits only at live US price
run "trend18.py --mark" "$DESK"   # 2026-10-03 DEEP18 trend sleeve (Brett-approved PAPER): re-mark, T-bill accrual, -30% kill check; never trades
run "trend_core.py mark" "$DESK"   # 2026-10-28 TREND-CORE A$1m AUD paper (path B, not firm equity): re-mark + status JSON; never trades
run trend_core_shadow.py  "$DESK"   # 2026-10-28 TREND-CORE SHADOW profit-taking (trim-stretched / weekly trail / BTC stop): log only, outside book P&L
run "trend_core_global.py rebalance-if-due" "$DESK"   # 2026-10-28 TREND-CORE global widening + SPEC: executes 28 Oct US open (>=09:45 ET) only after rebook done-file; live prices only; locked + executed flag = once
run "essentials_book.py mark" "$DESK"   # 2026-10-28 ESSENTIALS A$1m AUD paper (not firm equity): fill staged at next open (live px), re-mark, stops/spec halt flags
run risk_engine.py       "$DESK"
# firm_risk.py runs near the top of the tick, before entry-capable books.
run tfo_daily_pnl.py     "$DESK"   # record flow-adjusted daily USD P&L and %; reporting only
                                    # (FIRM_RISK_AMENDMENT.md). firm_risk.py already ran
                                    # before the entry books. Floors still read that halt
                                    # flag. WARN at -10%, HALT-NEW-ENTRIES at -20%, NEVER liquidates.
run exposure_dial.py     "$DESK"   # registered dial 21e05244c53d4e77: cheap no-op when fresh (<20h)
run profitability_controls.py "$DESK" # read-only fully loaded NAV / concentration / allocation audit
run "carry_economic_hurdle_lab.py --collect --incremental" "$DESK" # finite preregistered research; public settled data; NO orders or capital
run make_desk_report.py  "$DESK"
run "replica_floor.py --exits-only" "$DESK"
run polymarket_collector.py "$DESK"   # read-only Polymarket snapshot leg (2026-08-20, stdlib-only; see reports/POLYMARKET_LEG_PREREGISTRATION.md)
run futures_panel_archiver.py "$DESK"   # rescues the futures panel BEFORE market_intelligence.py:497
                                        # deletes it: that prune keeps only 7 days, so without this the
                                        # dataset can never ripen (found 2026-08-26, research_futures_panel_retention.md)
run "kraken_floor.py --exits-only" "$KRAKEN"
# 2026-10-29 bkorder: off-Mac backup moved BEFORE the wealth runner (it was being killed by the
# tick budget after the ~20 min wealth run). Logic of the backup step unchanged.
# 2026-10-30 backupfix: offsite_backup.sh REMOVED from tick budget — now its own launchd job com.tfo.offsite-backup
# (tools/run_offsite_backup.sh: lock + 4h cap, 02/06/10/14/18/22:50 WITA). Old line kept for reference:
# if [ "$(budget_left)" -gt 0 ]; then /usr/bin/perl -e 'alarm shift; exec @ARGV or exit 127' "$(budget_left)" bash "$DESK/offsite_backup.sh" || { echo "!! offsite_backup failed — running local_ledger_backup fallback" >> "$LOG"; bash "$DESK/tools/local_ledger_backup.sh" >> "$LOG" 2>&1 || true; }; else SKIPPED_STEPS=$((SKIPPED_STEPS + 1)); echo "!! TICK BUDGET ${TICK_BUDGET_S}s SPENT — skipped: offsite_backup.sh" >> "$LOG"; bash "$DESK/tools/local_ledger_backup.sh" >> "$LOG" 2>&1 || true; fi   # off-machine backup (tier1 daily / tier2 weekly); local fallback 2026-10-27 audit
# 2026-10-29 bkorder: wealth runner (unchanged args, still after fresh marks/exits) now runs DETACHED
# with its own 40-min cap and own single-flight lock (setsid so launchd job exit cannot reap it), so it no longer consumes the tick budget.
# Output -> reports/private_wealth_runner.log. Original line kept for reference:
# run "private_wealth_cycle_runner.py --force" "$DESK"   # custodian 2026-09-25: Private Wealth claim->receipt->complete after fresh marks/exits; no tickets, never replays; --force = record every hourly tick (70m cadence skipped alternate ticks)
PW_LOCK="$DESK/reports/.pw_runner.lock"; PW_LOG="$DESK/reports/private_wealth_runner.log"
if [ -d "$PW_LOCK" ] && [ -z "$(find "$PW_LOCK" -maxdepth 0 -mmin +45 2>/dev/null)" ]; then
  echo "-- private_wealth_cycle_runner.py --force: previous detached run still active, skipped this tick" >> "$LOG"
else
  rmdir "$PW_LOCK" 2>/dev/null; mkdir "$PW_LOCK" 2>/dev/null
  echo "-- private_wealth_cycle_runner.py --force (detached, cap ${PW_TIMEOUT_S:-2400}s, log $PW_LOG)" >> "$LOG"
  ( cd "$DESK" && nohup /usr/bin/perl -MPOSIX -e 'POSIX::setsid(); exec @ARGV' /bin/bash -c 'echo "==== $(date "+%F %T") pw start ===="; /usr/bin/perl -e "alarm shift; exec @ARGV or exit 127" "$0" "$1" private_wealth_cycle_runner.py --force; rc=$?; echo "==== $(date "+%F %T") pw done exit=$rc ===="; rmdir "$2" 2>/dev/null' "${PW_TIMEOUT_S:-2400}" "$PY" "$PW_LOCK" >> "$PW_LOG" 2>&1 < /dev/null & )
fi

# ---- OANDA practice desk: REMOVED 2026-08-28 (funder-directed) --------------
# The whole OANDA block is gone: no rebalance, no status snapshot, no reads. The
# OANDA Book is no longer a book of this firm and this tick no longer touches it.
# The six-railed claude_rebalance.sh and the HALT file are LEFT IN PLACE at
# /Users/test/Downloads/oanda-desk — nothing here re-wires them, and re-enabling
# would be a new funder direction. Note for whoever reads this next: the practice
# account still held open positions when the desk was removed, and they could not
# be closed (sustained 401 from 2026-08-27 18:12, and a logged close on that desk
# is a known no-op). Removing the desk stopped us MEASURING that exposure; it did
# not flatten it. See DECOMMISSION_20260828.md.

# ---- daily snapshot: ledgers, registrations, code — the firm's memory ----
BK="$DESK/backups"; mkdir -p "$BK"
LAST=$(ls -t "$BK"/tfo_*.tar.gz 2>/dev/null | head -1)
if [ -z "$LAST" ] || [ -n "$(find "$LAST" -mmin +1380 2>/dev/null)" ]; then
  STAMP=$(date '+%Y%m%d_%H%M')
  tar czf "$BK/tfo_$STAMP.tar.gz" \
      --exclude='backups' --exclude='__pycache__' --exclude='*.log' \
      --exclude='.tick.lock' --exclude='.git' --exclude='futures_panel_archive.sqlite' \
      --exclude='pit.sqlite' --exclude='pit.sqlite-*' --exclude='pit.sqlite.bak*' --exclude='pit.sqlite.*backup*' \
      --exclude='MultiVenueDesk/data/pit_staging' --exclude='MultiVenueDesk/data/pit/backups' \
      -C "$(dirname "$DESK")" "$(basename "$DESK")" 2>>"$LOG" \
    && echo "-- snapshot tfo_$STAMP.tar.gz" >> "$LOG"
  ls -t "$BK"/tfo_*.tar.gz 2>/dev/null | tail -n +5 | xargs rm -f 2>/dev/null   # 2026-10-30 pitexcl: keep newest 4 (was 14)
fi
# 2026-10-30 pitexcl: cap pit.sqlite backup copies at newest 2 (never the live pit.sqlite; skip any file still open)
for f in $(ls -t "$DESK"/data/pit/pit.sqlite.bak* "$DESK"/data/pit/pit.sqlite.*backup* 2>/dev/null | grep -vE -- '-(wal|shm)$' | tail -n +3); do
  lsof "$f" >/dev/null 2>&1 && continue
  rm -f "$f" "$f-wal" "$f-shm" && echo "-- pruned old pit backup $(basename "$f")" >> "$LOG"
done

# ---- git auto-backup: daily commit; push only if a remote is configured ----
# Backs up code, registrations, ledgers and track record. Secrets are gitignored
# and none live in the tree. Harmless until you add a remote (`git remote add`).
cd "$DESK"
if [ -d .git ]; then
  LASTC=$(git log -1 --format=%ct 2>/dev/null || echo 0)
  if [ $(( $(date +%s) - LASTC )) -gt 79200 ]; then
    # Explicit allow-list. Never `git add -A`: health captures, DOM dumps and
    # anything an agent drops in the tree must not be committed or pushed.
    git add -u -- \
      .gitignore .github desk_tick.sh launchd ops tools tests tests_watchlist \
      veteran_desk macpatches \
      '*.py' '*.md' '*.sh' '*.plist' '*.html' \
      'reports/*.json' 'reports/*.csv' 'reports/*.jsonl' 'reports/*.md' 'reports/tournament' \
      'static/*.json' 'static/*.html' 'static/*.csv' \
      'state/*.json' 'state/*.md' \
      >/dev/null 2>&1 || true
    git add -- \
      .gitignore .github desk_tick.sh launchd ops tools tests tests_watchlist \
      veteran_desk macpatches \
      '*.py' '*.md' '*.sh' '*.plist' '*.html' \
      'reports/*.json' 'reports/*.csv' 'reports/*.jsonl' 'reports/*.md' 'reports/tournament' \
      'static/*.json' 'static/*.html' 'static/*.csv' \
      'state/*.json' 'state/*.md' \
      >/dev/null 2>&1 || true
    git -c user.name="Taylor Family Office" -c user.email="desk@tfo.local" \
        commit -q -m "Daily firm snapshot $(date +%F)" >/dev/null 2>&1
    if git remote get-url origin >/dev/null 2>&1; then
      git push -q origin HEAD >> "$LOG" 2>&1 && echo "-- git pushed to origin" >> "$LOG"
    fi
  fi
fi

# ---- keep the log bounded ----
if [ "$(wc -l < "$LOG" 2>/dev/null || echo 0)" -gt 20000 ]; then
  tail -5000 "$LOG" > "$LOG.tmp" && mv "$LOG.tmp" "$LOG"
fi
echo "==== $(date '+%F %T') tick done; failed_steps=$FAILED_STEPS skipped_steps=$SKIPPED_STEPS elapsed=$(( $(date +%s) - TICK_T0 ))s ====" >> "$LOG"
if [ "$FAILED_STEPS" -ne 0 ] || [ "$SKIPPED_STEPS" -ne 0 ]; then
  exit 1
fi
