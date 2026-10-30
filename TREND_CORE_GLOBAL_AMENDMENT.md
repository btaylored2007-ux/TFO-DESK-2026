# TREND-CORE global widening + SPEC sleeve 2026-10-28 (PAPER ONLY, pt1575u)
TREND-CORE GLOBAL widening + SPEC sleeve (PAPER ONLY). Brett 28 Oct 2026 09:40/09:41 WITA (pt1575u).

Same law as TREND-CORE/Trend18 R2: trend = 0.5*(px>SMA200) + 0.5*(12m excess>0); inv-vol (60d); scaled to 20% vol;
TMF duration gate unchanged (trend18 ^TNX rule); monthly rebalance (first US session); -30% NAV/unit kill (trend_core.restrike).
Budgets (fractions of book NAV): CORE+GLOBAL notional <= 0.85, SPEC notional <= 0.10, A$50k spare (5%) never deployed;
idle inside CORE budget -> MF basket DBMF/KMLM/CTA thirds.
SPEC: higher-beta legs (ETH, SOL via Kraken; ERX 2x energy, EDC 3x EM) under its own 20% vol risk-parity, <=0.10 NAV,
own stop: leg exits at next rebalance/session if -25% from entry; sleeve halt if spec sleeve -30% (flag, no new spec until review).
No pump-chasing: leg skipped if signal 1m return > +30%.
Execution: NOT now (US ETFs closed at 09:4x WITA; funding requires MF sells) -> staged for 2026-11-02 first US session at live
prices via `rebalance-if-due`. Never backdates. Commands: plan | rebalance-if-due [--force-dry] | status

Indicative weights (signal asof 2026-10-27, TMF gate open=False):
- UPRO (core): 5.1%
- TQQQ (core): 2.9%
- TMF (core): 0.0%
- UGL (core): 0.0%
- XBT/USD (core): 2.1%
- EWJ (global): 9.6%
- EWA (global): 10.2%
- EWU (global): 14.3%
- EWY (global): 1.6%
- EWT (global): 5.7%
- MCHI (global): 0.0%
- EEM (global): 3.8%
- EFA (global): 13.9%
- XLE (global): 7.1%
- DBC (global): 8.7%
- ETH/USD (spec): 1.9%
- SOL/USD (spec): 1.9%
- ERX (spec): 4.6%
- EDC (spec): 1.6%
- DBMF (mf): 0.0%
- KMLM (mf): 0.0%
- CTA (mf): 0.0%

## AMENDMENT 2026-10-28 10:14 WITA (Brett pt1580u: "trade straight away")
- Crypto legs filled NOW 10:17 WITA at live Kraken mid (tape LIVE/RECENT only): XBT/USD SELL 0.881200 @ 81,006.05 (A$101,482); ETH/USD BUY 5.677073 @ 2,359.84 (A$19,046); SOL/USD BUY 129.208329 @ 101.78 (A$18,697). Cash A$113,391 (A$50k spare intact).
- ETF legs + MF -> 0 execute TONIGHT 28 Oct US open: launchd com.tfo.trend-core-global 21:46/22:00/22:20/23:05 WITA (+ desk_tick), only after com.tfo.trend-core-rebook (21:36/21:50/22:15/23:00 WITA) writes its done-file. Sequence: rebook first, then reweight; ledger lock + executed flag => once.
- Weights recomputed from latest completed close at execution; ETF prices = today's-session 1m bars <=10 min old, else fail closed and retry. Never backdated. Vol target 20%, -30% kill, A$50k spare unchanged.
