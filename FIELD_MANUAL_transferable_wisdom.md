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
