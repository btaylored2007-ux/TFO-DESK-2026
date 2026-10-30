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
