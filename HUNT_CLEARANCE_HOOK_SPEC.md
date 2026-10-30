# HUNT CLEARANCE: hook spec (2026-10-27, HEAD TRADER). NOT yet applied. PAPER ONLY.
Module: hunt_clearance.py (new). It reads plans, ledgers and the sweep, and writes only to state/clearance/YYYYMMDD/.
hunt48_short_paper.py already arms ONLY from plan files (apply-plan PLAN.json), so the gate sits at plan-writing time. The engine needs no code change.

## Outputs per day (state/clearance/YYYYMMDD/)
<TICKER>.json/.md breakdowns | verdict_<book>_<TICKER>.json | internal_verdicts.json | summary.json
external_prompt.md (to ChatGPT web + Claude) | external_reviews.json {book:{TICKER:{GPT,CLAUDE}}} (Brett/agent fills it in)
cleared_plan.json (main), cleared_plan_claude.json, cleared_plan_gpt.json: same plan schema; set = CLEARED only, cut = BLOCKED with reasons

## Final rule
CLEARED = RISK PASS + BEHAVIOR PASS + SPECIALIST PASS + GPT PASS + (CLAUDE PASS | CLAUDE PENDING_LOGIN, waived and flagged in the note)

## 20:30 WITA routine (replaces "apply-plan plan_bench_D.json" directly)
1. (unchanged) build plan_bench_D.json per book
2. python3 hunt_clearance.py run --date D            # ~10 s rules-only
3. Paste external_prompt.md into ChatGPT, then record the verdicts in external_reviews.json
   Claude: claude -p --model opus --effort max < external_prompt.md (after re-login); record the verdicts
4. python3 hunt_clearance.py finalize --date D
5. python3 hunt48_short_paper.py apply-plan state/clearance/D/cleared_plan.json
   python3 hunt48_short_paper.py apply-plan state/clearance/D/cleared_plan_claude.json --book claude
   python3 hunt48_short_paper.py apply-plan state/clearance/D/cleared_plan_gpt.json --book gpt
   (cut lists only names that are ARMED now, so cut_trade() never hits its non-ARMED SystemExit. FILLED/SHADOW trades are never touched. Re-run finalize just before apply, because ledgers move.)

## Optional hunt48 hard guard (apply only when the other workers are finished with that file)
In apply_plan(), refuse a plan whose path is not under state/clearance/ unless "--uncleared" is passed:
    if "state/clearance/" not in os.path.abspath(path) and "--uncleared" not in sys.argv: raise SystemExit("plan not cleared")
