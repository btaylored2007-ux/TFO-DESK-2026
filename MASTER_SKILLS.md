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
