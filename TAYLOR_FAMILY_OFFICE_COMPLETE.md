# TAYLOR FAMILY OFFICE — THE COMPLETE DOCUMENT
### One file. Everything learned and built. Hand this to anyone.
*Generated 2026-08-07. Paper only. Not financial advice. The live code + ledgers live in the same git repo.*

This single document merges all four knowledge files. Contents:
1. README (the master overview)  2. MASTER SKILLS (rebuildable recipes)
3. FIELD MANUAL (the 8 laws)  4. BETTING DESK OPERATING SYSTEM (workflow + templates)

---

# ===== PART 1: MASTER OVERVIEW =====

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

# ===== PART 2: MASTER SKILLS (rebuildable recipes) =====

# MASTER SKILLS — Every System, as a Rebuildable Recipe
### Hand this to any session (human or AI) and they can reconstruct the machine.
*Each skill: what it does · the exact method · inputs/outputs · [UNIVERSAL] or [TRADING-ONLY].*
*Paper only. Nothing here is financial advice.*

================================================================
SKILL 1 — THE VALIDATION HARNESS  [UNIVERSAL — the crown jewel]
================================================================
PURPOSE: decide whether an apparent edge is real or a self-deception. This is the
single most important skill; it killed 4 beautiful fakes (incl. a t=7.63 that fell
to 2.17, and a t=4.48 ML "discovery").

METHOD (reimplement in ~400 lines, numpy/scipy):
1. DEFLATED SHARPE (Bailey & Lopez de Prado): given a strategy's returns and the
   NUMBER OF STRATEGIES YOU TRIED (n_trials), compute the Sharpe you'd expect from
   the best of n_trials on pure noise (E[max] grows with sqrt(2 ln n_trials)).
   The edge is real only if observed Sharpe beats that null. Rule: reject unless
   deflated Sharpe > 0.95.
2. EFFECTIVE SAMPLE SIZE: correlated observations aren't independent.
   n_eff = n / (1 + (m-1)*rho) for m correlated series with mean corr rho, then
   divide by overlap_factor for overlapping windows. Recompute the t-stat on n_eff,
   NOT n. (This alone turned a t=7.63 into 2.17.)
3. PURGED + EMBARGOED K-FOLD CV: when testing a rule, drop training rows whose
   label window overlaps the test fold (purge), plus an embargo gap after it, so
   the model can't see its own answer through serial correlation.
4. WHITE'S REALITY CHECK: bootstrap the best-of-N strategies to get a p-value that
   the winner beats a benchmark after accounting for the whole search.
5. BENJAMINI-HOCHBERG FDR: control false-discovery rate across a family of tests.
6. KILL-TESTS (each killed a real candidate):
   - LIQUIDITY SCREEN: recompute the edge at rising tradeable-size floors; if it
     dies at realistic size, it's illiquidity not alpha.
   - DECAY/EXECUTION-DELAY: re-measure the edge with entry delayed 1s, 1min...;
     if it collapses, it was impact/footprint, not signal.
   - EFFECTIVE-BETS: a portfolio with rank-autocorrelation 0.99 rebalanced 110x is
     ~1 bet, not 110. Discount accordingly.
BETTING TRANSLATION: "deflate for how many systems you tried"; "500 correlated
bets are a handful of independent ones"; "a price that vanishes on real stakes is
impact." CLV over 200+ bets is the betting analog of the deflated Sharpe.

================================================================
SKILL 2 — PRE-REGISTRATION  [UNIVERSAL]
================================================================
PURPOSE: make results impossible to curve-fit after the fact.
METHOD: before the first trade/bet, write a doc with (a) exact rule, (b) universe,
(c) honest pre-registered expectation, (d) kill-criteria, (e) evaluation date.
Hash the doc (sha256, first 16 chars) and write the hash into every ledger entry —
any later edit changes the hash, so tampering is visible forever. NO VERDICT before
the sample matures (we used 182 days / 200 events). Label early results "noise."
BETTING: freeze the method before betting it; edits void it; 6-month minimum.

================================================================
SKILL 3 — THE TOURNAMENT  [UNIVERSAL]
================================================================
PURPOSE: let live forward results — not backtests — pick winners among strategies.
METHOD: run each strategy as a small, capped, independently-accounted "sleeve" on
one leaderboard. Rank ONLY by return/max-drawdown (never raw profit early — early
profit is mostly luck). Freeze a sleeve at -18% drawdown (kill rule, funder-
directed 2026-08-28; -30% before that). A sleeve
leading on the risk-adjusted metric for 2+ quarters earns more allocation, nothing
sooner. Include deliberately-naive controls (equal-weight, a coin-flip) — if the
clever strategies can't beat them, they're fired.
BETTING: run 3-5 pre-registered methods as capped sleeves; rank by CLV-over-200 and
return/drawdown; a coin-flip control keeps everyone honest.

================================================================
SKILL 4 — THE RISK ENGINE  [UNIVERSAL in spirit, TRADING math]
================================================================
PURPOSE: answer "what could the WHOLE book lose, together?" — the question no
single-position view answers.
METHOD: historical simulation. Take today's positions, hold weights static over
~2y of daily returns, compute: annualised vol, 1-day VaR (5th/1st percentile),
max drawdown, FACTOR BETAS (regress the book on SPY/BTC/oil/FX to expose HIDDEN
bets — we found +0.30 equity beta despite owning zero index), CONCENTRATION
(effective bets = 1/HHI of weights — our 21 positions were 8.5 real bets), and
currency drag for a foreign-denominated investor. Alarm if VaR or unmeasured-book
share crosses a threshold.
BETTING: your "factor beta" is the hidden common thread (all favourites, one weather
front). Your "effective bets" is how many truly independent positions you hold.

================================================================
SKILL 5 — THE COST-WALL MODEL  [UNIVERSAL — the filter that kills most ideas]
================================================================
PURPOSE: state every venue's toll in basis points and refuse edges that don't clear it.
METHOD: per venue, model spread + commission + financing + FX conversion as a
round-trip cost. An edge must exceed this AFTER realistic turnover. Most "signals"
die here (we found a 1bp signal against a 30bp wall = worthless). Compute break-even
move / break-even win-rate explicitly.
BETTING: toll = overround + exchange commission (~2-5%). Break-even win-rate =
(1/odds) grossed up for commission. If you can't state it, don't bet.

================================================================
SKILL 6 — HONEST MARKING & REPORTING  [UNIVERSAL]
================================================================
PURPOSE: never let the scoreboard flatter you.
METHOD: (a) persist last-good prices so closed markets show TRUE last-close P&L, not
a misleading $0; (b) ALWAYS separate REALIZED (banked) from UNREALIZED (open, can
still vanish) — the headline number must be the honest net, not the rosy open figure
(we had to fix exactly this: a +$3k unrealized pill deflating felt like a loss when
nothing was lost); (c) show costs and realized losses as loudly as gains; (d) NEVER
annualize a tiny sample — 1.5 days annualizes to anything from -100% to +600%; it is
noise, and a Sharpe-0.3 effect needs ~1,900 days to even detect.
BETTING: separate settled P&L from open bets; never count an unsettled bet as won;
track CLV, not just profit; never extrapolate a good week.

================================================================
SKILL 7 — SELF-HEALING OPERATIONS  [TRADING-ONLY plumbing, principle is universal]
================================================================
PURPOSE: run 24/7 without a human, safely.
METHOD: an hourly heartbeat (launchd) that ticks every book, marks, backs up (daily
git commit), and reports. A healer that auto-restarts ONLY safe read-only processes
and ESCALATES anything that owns money/state (never blind-restart the core engine).
A watchdog that relaunches a dead supervisor via its own single-instance-safe path.
Dynamic config (e.g. derive the current network IP, don't hardcode it).
PRINCIPLE (universal): automate the provably-safe, escalate the rest, and let the
machine's failures be loud and logged.

================================================================
THE META-SKILL (the one that matters most)
================================================================
Authority is exercised as RESTRAINT. Given unlimited power, the right move is usually
NOT to act — a frozen rule beats a 3am impulse. Every decision, including the decision
NOT to trade, is logged with its reason. The firm's whole edge is not a signal — it is
a machine that refuses to fool itself. Rebuild THAT and the rest is detail.

---

# ===== PART 3: FIELD MANUAL (the wisdom) =====

# The Taylor Family Office Field Manual
### Everything learned in 24 hours, distilled for any speculative account — including a paper betting book
*Written 2026-08-07 · ~1,119 hypotheses tested · zero validated edges found · and that is the most valuable finding of all.*

---

## LAW 0 — "No edge" is the default. The burden of proof is on the bet, never on the market.
We tested 13 research streams across every market on earth. **Almost nothing beat doing the simplest thing.** Assume any angle is worthless until it survives everything below. This isn't pessimism — it's what stops you paying to learn the same lesson twice.
**Betting translation:** assume every selection is -EV (the bookie built it that way) until proven otherwise. The crowd, the tipster, the "system" — all guilty until acquitted by evidence.

## LAW 1 — Every market charges a toll. Find it, name it in numbers, beat it or don't play.
Crypto cost ~30 bps a round trip on Kraken, ~350 on eToro (12× — a door we shut). A signal worth 1 bp against a 30 bp wall is worthless no matter how "real" it is. **Readable ≠ bankable.**
**Betting translation:** your toll is the **overround (vig)** + **exchange commission** (Betfair ~2–5%). A method that wins 52% of coinflips still *loses* if the price makes you pay 5% to play. Compute the break-even win-rate for every bet and refuse anything that doesn't clear it after commission.

## LAW 2 — The four ways you fool yourself (and the four kill-tests that stop it)
1. **Multiple testing** — test 200 random things, the best looks brilliant by luck. Fix: *deflate* the winner by how many things you tried. A t-stat of 2.5 that took 200 tries is noise.
2. **Effective sample size** — 100 correlated bets are not 100 data points. 21 crypto positions were really ~8 independent bets. We once saw a t-stat of 7.63 collapse to 2.17 once we counted honestly.
3. **Impact/decay** — the strongest signal we found paid 25bp at the trigger and −3.4bp one second later. It was measuring its own footprint, not a real move.
4. **Liquidity** — an edge that only exists in sizes you can't get on is not an edge.
**Betting translation:** backtesting a tipster on their *own* winners is multiple-testing fraud. 500 bets on correlated games (same league, same day) is a handful of independent bets. A price that vanishes the moment you try to get real money on is the impact tax. And a 10.0 shot showing "value" at £5 stakes isn't value if £50 moves the line.

## LAW 3 — The only judge that cannot be bribed is the future.
Backtests get curve-fit; forward results can't. Every strategy we deployed is **pre-registered** — rules, expectations, and kill-criteria frozen *before* the first bet, hash-stamped so any later edit is visible forever. **No verdict is allowed before 182 days.** Early results are labelled noise, out loud.
**Betting translation:** the forward judge in betting has a name — **Closing Line Value (CLV)**. Did you take a price *better* than where the market closed? Beat the closing line consistently and you have a real edge, even through a losing month; fail to beat it and your winning month was luck. **CLV is your deflated Sharpe. Log it on every bet. It is the only number that matters before you have 500+ settled bets.**

## LAW 4 — Risk is job one. You cannot compound from zero.
- Size by **fractional Kelly** (¼–½, never full). Full Kelly is the fastest route to ruin that still has positive expectancy.
- **Never scale up a loser.** We cut losers via hard stops and let winners run — our realized book was *locked losses* while the winners ran unrealized. That asymmetry is the strategy, not a bug.
- **Cap total exposure** and honour a daily-loss limit. One bad day should never be able to end you.
- We **permanently barred** the one thing that returned the most (short-vol: +37%/yr with a −95% crash). *The highest return on the board was ruin dressed as income.* Beware the strategy that prints money right up until it detonates.
**Betting translation:** flat-or-fractional-Kelly staking, a stop-loss on the day, and never, ever chase. The 1.20 "sure thing" you'd stake the house on is the short-vol trade of betting.

## LAW 5 — Edges average, they don't add. Diversification protects capital and dilutes conviction.
Adding 14 asset classes to a plain index made returns *worse*, not better — we proved it on our own data. Spreading across everything bought less safety than it looked like, because most markets are the same bet in different clothes (our "true" exposure was 8 bets, not 21).
**Betting translation:** 30 small bets across every sport isn't a portfolio if they all secretly ride the same factor (favourites-longshot bias, or one weather front across the day's racing). Know your *real* number of independent positions. Concentration where you have genuine edge beats scattering where you don't.

## LAW 6 — Turnover is the tax. Presence beats churn.
The firm made money on days it placed **zero** trades — positions already correct, carried by the market. Every extra trade pays the toll again. The mandate that actually works isn't "trade constantly," it's "**be positioned, and act only when a rule has earned it.**"
**Betting translation:** you do not need a bet every race. The edge is being *ready* to strike a mispriced line, not manufacturing action. A quiet day with no qualifying bet is a position, not a failure.

## LAW 7 — Authority is exercised as restraint, and reported honestly.
Given unlimited authority, the right move was usually *not to act* — because a frozen rule executes better than a 3am impulse. We automated only what was provably safe and escalated the rest. And every number, win or lose, gets reported to the cent: unrealized ≠ realized (a paper gain the market can still take back), realized losses shown as loudly as gains, costs never hidden.
**Betting translation:** the discipline to skip a bet is the edge. Track your CLV and P&L with brutal honesty — separate settled profit from open exposure, and never let a good week talk you out of the rules you wrote in a calm hour.

---

## THE ONE-PAGE BETTING CHECKLIST (pour this into the paper account)
1. **Break-even first.** For every selection: implied prob + commission = the win-rate you must beat. If you can't state it, don't bet.
2. **CLV is the judge.** Log the price you took vs the closing price. Positive CLV over 200+ bets = edge. Nothing else counts yet.
3. **Fractional Kelly, capped, with a daily stop.** Never scale a loser. Never chase.
4. **Pre-register the method.** Write the rule and its kill-criterion before betting. Freeze it. No verdict for 6 months.
5. **Deflate for how hard you searched.** The tipster/system that looks best out of many is probably the luckiest, not the best.
6. **Count independent bets, not tickets.** Correlated bets are one bet.
7. **No bet is a position.** Presence over churn.
8. **Report honestly, to the cent.** The scoreboard that flatters you is the one that ruins you.

*The firm's whole edge, in one line: we could not find a way to reliably beat the market — so we built a machine that refuses to fool itself, cuts losers fast, rides winners, pays the smallest possible toll, and waits. In betting, that machine is: bet only positive-CLV prices, at fractional stakes, by a frozen rule, and let the closing line be your honest judge.*

---

# ===== PART 4: BETTING DESK OPERATING SYSTEM =====

# The Betting Desk Operating System
### 24 hours of trading-firm wisdom, converted into a run-it-today paper betting operation
*Slots into the Betfair paper desk workflow: SCREEN → CLV-GATE → SIZE → HONEST-REPORT.*

Everything below is the same machinery that ran the Taylor Family Office — 6 floors, 68 positions,
~1,119 hypotheses — rebuilt for a betting book. Two halves: **the 8 laws (why)** and **the 5 skills
+ templates (how)**.

================================================================
PART A — THE 8 LAWS (the wisdom, in one screen)
================================================================
0. NO EDGE IS THE DEFAULT. Every selection is -EV (the book built it that way) until proven otherwise.
1. EVERY MARKET CHARGES A TOLL. Yours = overround (vig) + exchange commission (~2-5%). Beat it or don't play.
2. FOUR WAYS YOU FOOL YOURSELF: (a) testing many systems, best looks great by luck — deflate for search;
   (b) correlated bets aren't independent — count real bets, not tickets; (c) a price that vanishes when you
   bet real money is impact, not value; (d) an edge only in tiny stakes is not an edge.
3. THE ONLY JUDGE IS THE FUTURE. In betting its name is CLOSING LINE VALUE. Beat the closing line and you have
   an edge even through a losing streak. Fail to, and your winning week was luck.
4. RISK IS JOB ONE. Fractional Kelly (1/4-1/2, never full). Never scale a loser. Daily stop. Never chase.
5. EDGES AVERAGE, THEY DON'T ADD. 30 bets across every sport isn't a portfolio if they ride one hidden factor.
6. TURNOVER IS THE TAX. No bet is a position. Presence beats churn.
7. AUTHORITY IS RESTRAINT, REPORTED HONESTLY. The discipline to SKIP is the edge. Track to the cent.

================================================================
PART B — THE 5 SKILLS (the workflow you run every session)
================================================================

SKILL 1 — THE SCREEN (find candidates)
  - Pick ONE market you can price better than the crowd (one league, one race type). Depth beats breadth.
  - Your "fair price" must come from a repeatable method (model, ratings, a documented angle) — never a feeling.
  - Output: a list of {selection, your_fair_prob, market_odds}.

SKILL 2 — THE EV / BREAK-EVEN GATE (the toll test) — DO THIS BEFORE EVERYTHING
  - Implied prob of the odds = 1 / decimal_odds.
  - Your edge exists ONLY if:  your_fair_prob  >  (1/odds) + commission_buffer.
  - Break-even win rate you must beat = (1/odds) grossed up for commission. If you can't state it, NO BET.
  - Expected value per $1:  EV = your_fair_prob * (odds-1) * (1-commission) - (1-your_fair_prob).
  - RULE: only bets with EV > +2% after commission pass. Everything else is declined and LOGGED as declined.

SKILL 3 — THE CLV GATE (the honest judge) — the single most important habit
  - For EVERY bet, record: price_taken AND price_at_market_close (the SP or last traded).
  - CLV% = (price_taken / closing_price - 1) * 100  (positive = you beat the close).
  - Your edge is REAL only if average CLV is positive across 200+ settled bets. Until then, results are noise.
  - A losing month with positive CLV = keep going. A winning month with negative CLV = you got lucky, tighten up.

SKILL 4 — SIZE (fractional Kelly, capped)
  - Kelly fraction f* = edge / odds = (your_fair_prob*odds - 1) / (odds - 1).
  - STAKE = bankroll * f* * 0.25   (quarter-Kelly — full Kelly is the fastest road to ruin that's still +EV).
  - Hard caps: max 2% of bankroll on any single bet; max 6% total open exposure; STOP for the day at -5% bankroll.
  - NEVER increase a stake to recover a loss. That instinct is the short-vol trade of betting: prints, then detonates.

SKILL 5 — THE HONEST REPORT (the track record that can't lie)
  - Log every bet AND every decline. Columns below.
  - Weekly: total staked, settled P&L, average CLV, hit rate vs break-even, biggest drawdown, # bets vs # declines.
  - Separate SETTLED profit from OPEN exposure — never count an unsettled bet as won.
  - The scoreboard that flatters you is the one that ruins you.

================================================================
PART C — TEMPLATES (fill these in — this IS the system)
================================================================

--- THE BET LOG (one row per bet; a spreadsheet or the paper desk) ---
date | event | selection | method | your_fair_prob | odds_taken | implied_prob | commission% |
break_even_winrate | EV% | kelly_f | stake | closing_price | CLV% | result(W/L) | settled_pnl | notes

--- THE PRE-REGISTRATION (freeze BEFORE you bet a method; edits void it) ---
METHOD NAME: __________         REGISTERED (date): __________
Exact rule (what qualifies a bet, entry price condition, staking): __________
Universe (which markets only): __________
Pre-registered expectation (honest: "roughly breakeven, CLV is the test"): __________
KILL CRITERIA: stop this method if (a) -X% bankroll drawdown, or (b) average CLV negative after 100 bets.
NO VERDICT before 182 days / 200 bets. Early results are labelled NOISE.

--- THE METHOD TOURNAMENT (run several, let live CLV judge) ---
Run 3-5 methods as separate small paper "sleeves", each pre-registered, each capped. Rank them ONLY by
CLV-over-200-bets and return/max-drawdown — never by raw profit (raw profit is mostly luck early). Freeze a
method's rules; a method that leads on CLV for two quarters earns a bigger allocation, nothing sooner.

================================================================
THE WHOLE THING IN ONE LINE
================================================================
Bet only positive-EV, positive-expected-CLV prices, at quarter-Kelly stakes, by a rule you froze in a calm hour,
and let the closing line be your honest judge. Skip everything else. That discipline — not any tip — is the edge.
