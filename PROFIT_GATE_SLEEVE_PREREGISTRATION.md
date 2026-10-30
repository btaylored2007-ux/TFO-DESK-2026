# PROFIT-GATE SLEEVE — Pre-Registration

**Sealed 2026-09-16. FROZEN. Never edit to fit a result.**

**Authority:** Funder Brett Taylor, Grok Bot chat 2026-09-16 — deploy remaining
cash after DATA.L thesis close; *"hard rule to make profit"*.

**PAPER ONLY.**

## What
Ring-fenced sleeve tagging `sleeve=profit_gate` inside the TFO Fundamental Desk
ledger, funded from idle book cash (post DATA.L close). Dual-momentum top-2 of
liquid ETFs **excluding USO and DBC** (those are already held in Seed Recovery).

## Hard profit rule (frozen)
No exit scale until **+5% NET** after modelled round-trip costs (10+10+5+5 bps),
then Growth-style ladder (+5%→25%, +10%→25%, +15%→25%, runner 25% with 5pp
giveback). Same arithmetic spirit as `fundamental_exits.py` MIN_NET_GAIN.
Positions also inherit the main book exit engine.

## Constants
UNIVERSE: SPY QQQ IWM EFA EEM TLT IEF LQD HYG GLD SLV IYR AGG (no USO/DBC)
LOOKBACK 252 · TOP 2 · SAFE IEF · REBALANCE_TD 21 · COST_BPS_SIDE 10
MIN_NET_GAIN 0.05 · MIN_CASH_BUFFER 100 · Kill −30% sleeve peak · 182-day clock

## Honesty
No new alpha claim. Funder ordered capital to work with a hard profit gate.
Verdict only after 182 days vs T-bill cash benchmark.
