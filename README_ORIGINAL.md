# THE TAYLOR FAMILY OFFICE — Complete Package
### A local, paper-only autonomous trading firm, built in ~24 hours, and everything it learned.
*Clone this repo and you have the whole thing: the machine, the wisdom, the skills, the templates.*
*Paper only. No broker connected, none may be added. Nothing here is financial advice.*

---

## 0. WHAT THIS IS (the one-paragraph brief)
Over one long session a firm was built from a single dashboard screenshot: **6 trading floors,
68 live paper positions, 3 currencies, 4 continents, 21 competing strategies** — plus the research
that judged them (**~1,119 hypotheses across 13 streams**). The punchline, and the most valuable
finding: **almost nothing reliably beats the simplest thing.** So the firm's real edge became a
*machine that refuses to fool itself* — cuts losers, rides winners, pays the smallest toll, and waits.
This README is that machine and its lessons, packaged to hand to anyone (human or AI).

---

## 1. THE 8 LAWS (the transferable wisdom — works for trading OR betting)
0. **No edge is the default.** Burden of proof is on the bet, never the market.
1. **Every market charges a toll** (spread/fees; in betting: vig + commission). Beat it in numbers or don't play.
2. **Four ways you fool yourself:** multiple-testing (deflate for search) · correlated positions aren't
   independent (count *effective* bets) · impact/decay (a price that moves when you touch it isn't real) ·
   liquidity (an edge only in tiny size is no edge).
3. **The only judge is the future.** Out-of-sample forward results (in betting: **Closing Line Value**)
   are the one metric that can't be curve-fit. No verdict before ~182 days / 200 events.
4. **Risk is job one.** Fractional Kelly (¼–½, never full). Never scale a loser. Cap exposure. Daily stop.
   *The highest-returning thing we found was barred — +37%/yr with a −95% crash. Beware income that detonates.*
5. **Edges average, they don't add.** Diversification protects capital and dilutes conviction; know your
   *real* number of independent bets (our 21 positions were ~8 bets).
6. **Turnover is the tax.** Presence beats churn. We made money on zero-trade days. No bet is a position.
7. **Authority is restraint, reported to the cent.** The discipline to *skip* is the edge. Separate realized
   from unrealized; show losses as loudly as gains; the scoreboard that flatters you is the one that ruins you.

---

## 2. THE SKILLS (reusable methods — the "how", each is a real module here)
- **Validation harness** (`quant_validation.py`): Deflated Sharpe (Bailey/López de Prado), effective-sample-size
  correction, purged+embargoed K-fold CV, White's Reality Check, Benjamini-Hochberg FDR, and 3 kill-tests
  (liquidity screen, execution-delay/decay, effective-bets). It killed four beautiful fakes, including a
  naive t=7.63 that fell to 2.17 and a t=4.48 ML "discovery." **This is the crown jewel — the thing that
  refuses to lie.**
- **Pre-registration** (`*_PREREGISTRATION.md`): rules + expectations + kill-criteria frozen BEFORE the first
  trade, hash-stamped so any later edit is visible forever. No verdict before the sample matures.
- **Strategy tournament** (`tournament.py`): every strategy on one leaderboard, ranked by return/max-drawdown
  (never raw profit early), each a small capped sleeve, losers freeze at −30% DD.
- **Firm-wide risk engine** (`risk_engine.py`): historical-sim VaR, factor betas (the hidden bets), stress
  replays, concentration/effective-bets, currency drag. Alarms on VaR or unmeasured-book thresholds.
- **Cost-wall models** (`venue_costs.py`): every venue's toll in basis points; the filter that killed most ideas.
- **Honest marking** (`command_server.py`): persistent last-good prices so closed markets show true P&L,
  never a misleading $0; realized vs unrealized always separated.
- **Self-healing ops** (`desk_healer.py`, `desk_tick.sh`, launchd): bounded auto-restart of safe processes,
  escalate the rest; hourly heartbeat; daily git backup; the trust-anchor engine is never restarted blindly.

---

## 3. WHAT'S DEPLOYED (the live paper books)
- **Growth Book** ($50k) — dual-momentum top-2 of 15 asset classes, monthly. Risk-managed beta.
- **Value Book** ($50k) — global value/quality top-10 ex-traps, quarterly. Forward experiment (fundamentals
  can't be backtested honestly on free data, so the live record IS the evidence).
- **Kraken engine** (A$40k) — autonomous Chief-PM paper desk.
- **Carry executor** — delta-neutral basis carry; the ONE mechanism that clears its cost wall ~8×; armed,
  fires automatically at ≥4%/yr, currently ~1% (regime-dependent, waiting).
- **OANDA Claude Book** — trend on 4 index CFDs, 3×ATR stops, six-railed daily reconcile (funder-lit first fill).
- **Tournament**: 10 sleeves incl. 4 global premium probes (Japan/India/agri/EM-debt) and vol sleeves
  (short-vol ETPs permanently barred).

---

## 4. THE RESEARCH VERDICT (so nobody re-litigates it)
13 streams, ~1,119 hypotheses: literature, microstructure/tape, cross-sectional, holding-period, ML/nonlinear,
equity factors, multi-asset allocation, 36-year regime, sentiment (Fear&Greed 8y), mining fundamentals (hash
ribbons), resources & shorts, IPOs, volatility/options. **No validated after-cost alpha.** Two real *mechanisms*:
basis carry (clears costs, rate-dependent) and crash-managed trend (equity-like return at ~half the drawdown,
no excess). Everything else was beta, noise, or a search artefact. **Shorting loses to going to cash. Buying
the index beat every clever allocation.** These are conclusions to build ON, not to re-run.

---

## 5. FOR THE BETTING BROTHER (the translation, since betting = trading with new names)
Toll = vig + commission. Judge = **Closing Line Value** (log price-taken vs closing on every bet; positive CLV
over 200+ bets = real edge, even through a losing run). Size = quarter-Kelly, capped, daily stop, never chase.
Pre-register the method, freeze it, no verdict for 6 months. Full playbook + templates:
`BETTING_DESK_OPERATING_SYSTEM.md`. Philosophy: `FIELD_MANUAL_transferable_wisdom.md`.

---

## 6. HOW TO RUN / REBUILD IT
Needs: macOS, Python 3 + numpy/scipy/pandas/scikit-learn/yfinance, Claude Code (for the analyst push + engine
Chief-PM loop), Tailscale (any-device access). `desk_tick.sh` is the heartbeat (hourly via launchd); it heals,
ticks every book, marks, backs up, and reports. Dashboards: war room `:18811`, mobile `/m`, live report `/report`.
The `taylor-family-office` skill is the full operating manual; `SKILL.md` in that skill encodes every path,
rule, and non-negotiable.

---

## THE WHOLE FIRM IN ONE LINE
We could not find a way to reliably beat the market — so we built a machine that never fools itself,
cuts losers fast, rides winners, pays the smallest possible toll, waits for the fat pitch, and reports
every number honestly, win or lose. That discipline — not any signal — is the edge. Pass it on.

---

## AMENDMENT NOTE — liquidity screen on the crypto floors (2026-08-25, append-only)

Funder-directed 2026-08-25 ("option 2"): `replica_floor.py` and
`KrakenQuantDesk/kraken_floor.py` now REFUSE any **NEW** entry whose measured
24h USD volume is below **$5,000,000**, or whose volume cannot be measured
(fail-closed). Volume comes from Kraken's PUBLIC ticker API — the engine's
`/state` `vol_musd` field reads `0.0` for every symbol and cannot be used.

**Existing positions are never force-closed, resized or altered by this rule.**
They exit by their own registered rules. No risk control is loosened; this only
ADDS a veto. New amendment, own clock from 2026-08-25 — it does not retroactively
change either floor's existing track record, and nothing above this line is edited.

Full registration: `LIQUIDITY_SCREEN_AMENDMENT.md`.
