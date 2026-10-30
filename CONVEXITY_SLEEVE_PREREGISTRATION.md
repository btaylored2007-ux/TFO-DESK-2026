# CONVEXITY SLEEVE — PRE-REGISTRATION (FROZEN)

**Registered:** 2026-09-03 · **Status:** ARMED-EMPTY, $0 deployed · **PAPER ONLY**
**Authority:** funder-directed — "$150K 21X IN 3 MONTHS", "MAKE THEM HARD RULES"

This file is FROZEN and append-only. Editing it invalidates it. Amendments go in
their own `*_AMENDMENT.md` alongside, per desk convention.

Seed $150,000 · Target $3,203,976 (21.36x) · Horizon 2026-12-03
Sleeve max loss $150,000 (structural) · Halt at $75,000 (50%)

---

## THE RESULT THAT GOVERNS EVERYTHING BELOW

A fully-paid, non-negative-payoff portfolio has risk-neutral expectation equal to
the cash invested grown at the risk-free rate. By Markov's inequality:

    Q(V >= 21.36 x premium)  <=  e^(rT) / 21.36  =  1.0067 / 21.36  =  4.71%

**4.71% is a hard mathematical ceiling on P(21x), for ANY defined-risk structure,
however chosen.** It is arithmetic, not opinion. Paying a realistic 25% markup
over fair value lowers it to 3.75%. No selection skill can raise a ceiling that is
a property of the payoff structure — skill can only move you toward it, and this
desk has 0 of 32 books showing skill (best |t| 2.95 against a 4.23 bar).

Honest probability for THIS desk today: **<1%, point estimate 0.3-0.5%.**
Expected value: **-$44,000 to -$46,000** on $150,000 (approx -30% of committed).
Modal outcome: **-$150,000 total loss, probability ~96%.**

## THE OPPOSITION THE FUNDER MUST SEE

**The risk controls and the 21x target are in direct mathematical opposition.**

Diversification destroys the 21x probability unless every leg alone suffices:
10 equal legs each capable of 100x gives P(21x) ~ 0.011% — it needs three
simultaneous 100-baggers. 20 legs gives 0.00014%. The only diversified structure
preserving the probability is one where each leg returns >= 21.36 x N.

So the choice is: concentrate and accept ~96% chance of losing the whole sleeve,
or diversify and accept ~0.01% chance of the mandate. **There is no third option.**
These rules choose survival, and in doing so push P(21x) toward the bottom of that
range. That is a deliberate, disclosed trade — not an oversight.

## WHY IT IS ARMED-EMPTY

36 convexity domains hunted worldwide, 37 agents, zero errors. Every domain
returned a real live-quoted instrument and every one priced NEGATIVE EV, -2.5%
to -50.7% per dollar risked. Entry gate 1 requires a refutation-surviving
candidate; `SURVIVORS = []`, so gate 1 blocks every entry.

**Capital deployed today: $0.00.** The rules below are the conditions under which
anything could ever be deployed. None are currently met.

---

# $150,000 DEFINED-RISK CONVEXITY SLEEVE — SIZING AND HARD RULES

## HEADLINE (read this and stop if you read nothing else)

**No combination of defined-risk positions gets the firm to 3x with meaningful probability. Not this sleeve, not a bigger one, not a better-selected one.**

Three separate facts, each independently fatal:

1. **The survivor set is empty.** After adversarial refutation, `SURVIVORS = []`. There is not one candidate to size. Every number below is therefore about a *hypothetical* sleeve. **Capital deployed today: $0.00.** The rules in §3–§5 are the conditions under which anything could ever be deployed; **none of them are currently met.**

2. **There is a hard mathematical ceiling on the probability, and it is ~4.7%.** This is not an opinion. A fully-paid, non-negative-payoff portfolio has risk-neutral expectation equal to the cash invested grown at the risk-free rate. By Markov's inequality, Q(V ≥ 21.36 × premium) ≤ e^(rT)/21.36 = 1.0067/21.36 = **4.71%**. Every dollar of bid/ask paid above fair value lowers it (at a 25% markup over fair, the ceiling is 3.75%). No selection skill can raise a ceiling that is a property of arithmetic — skill can only move you *toward* it, and this desk has 0 of 32 books showing skill, best |t| 2.95 against a 4.23 bar.

3. **The EV is negative and it is not close.** See §2.

The mandate asks for +1.85%/day compounded on the firm. The sleeve version of that ask is **+5.24%/day compounded for 60 straight days** ($150,000 → $3,203,976 = **21.36x**). The honest answer to "can we do that" is no, and the second-most-honest answer is "only by taking the ~41% chance of losing half the firm that the mandate explicitly rejects."

---

## 1. AGGREGATE HONEST PROBABILITY THE SLEEVE RETURNS 21x

| Layer | P(sleeve ≥ 21.36x in 60 days) | Basis |
|---|---|---|
| Theoretical ceiling, fair-value entry | **4.71%** | Theorem (no-arbitrage + Markov). Not achievable in practice. |
| Ceiling after a realistic 25% premium markup | **3.75%** | Same theorem, adjusted for paying the ask |
| Best practically-constructible book (8 independent, *fairly priced*, ~200:1 structures) | **~3.9%** — and **96.1%** chance all eight expire worthless | Binomial, p = risk-neutral fair, independence assumed |
| Same book, realistic real-world win rate (fair p haircut 30% for spread + adverse selection) | **~2.8%** | Same, p reduced |
| **This desk, today, with zero validated survivors** | **< 1%. Point estimate 0.3–0.5%.** | Judgment, stated as judgment |

**Why the last row is so much worse than the row above it.** The ~3.9% row requires eight *independent*, *liquid*, *fairly-priced* structures that each return ≥170x within 60 days, found and entered at good prices. We have identified **zero**. The instruments that actually pay 100x+ in 60 days with capped loss (deep-OTM short-dated options on high-vol names) are precisely the ones with the widest spreads and the ones with the most documented adverse pricing. A structure quoted 0.00 bid / 0.05 ask has structurally capped loss and a 100% *realized* loss, because you cannot exit.

**A counter-intuitive but important result, stated honestly because it cuts against the prudent design below:** diversification *destroys* the 21x probability unless every leg alone is sufficient. Ten equal legs each capable of 100x gives P(21x) ≈ 0.011% — you need three simultaneous 100-baggers. Twenty legs at 100x gives 0.00014%. The only diversified structure that preserves the probability is one where each single leg returns ≥ 21.36 × N. So the choice is: concentrate and accept a ~96% chance of losing the whole sleeve, or diversify and accept a ~0.01% chance of the mandate. **There is no third option.** The prudent rules in §3–§4 choose survival, and in doing so they push P(21x) toward the bottom of the table. The funder must be told that explicitly: *the risk controls and the 3x target are in direct mathematical opposition.*

---

## 2. AGGREGATE EV OF THE SLEEVE, IN DOLLARS

**Negative. Plainly negative. Central estimate: −$45,000 on $150,000 deployed, over 60 days.**

EV = P × (e^(rT) − 1 − c − λ), where P = premium deployed, c = round-trip transaction cost as a fraction of premium, λ = the risk premium embedded in long convexity.

The risk-free drift over 60 days is +0.67% (+$1,004). **Break-even therefore requires total drag c + λ ≤ 0.67% of premium.** That is not achievable on any instrument with the convexity this mandate requires. A $0.20 bid / $0.25 ask market is 22% of mid on a single round trip.

| Total drag (c + λ) | Sleeve EV |
|---|---|
| 10% | **−$13,995** |
| 20% | **−$28,995** |
| **30% (central estimate)** | **−$43,995** |
| 40% | −$58,995 |
| 50% | −$73,995 |

**Honest central estimate: EV ≈ −$44,000 to −$46,000**, i.e. the sleeve is expected to destroy roughly **30% of the capital committed to it** and about **2.9% of firm capital**, before counting the ~$1,000 of T-bill interest forgone.

**Modal outcome, not expected outcome:** in any configuration aggressive enough to have a non-trivial shot at 21x, the single most likely result is **−$150,000 (total loss), with probability ~96%**. Firm capital goes $1,526,988 → $1,376,988, **−9.8%**, and the 3x is missed anyway.

I will not dress this up: **this is a negative-EV lottery ticket.** Per the mandate's own rules that is allowed to be reported — as long as it is labelled, and it is now labelled.

---

## 3. SIZING (per position, per sleeve)

**Current state: ARMED-EMPTY. $0 deployed. Nothing may be entered until §4 entry gates pass on a named, refutation-surviving candidate.**

**Sleeve max loss: $150,000. Hard. Structural, not behavioural** — enforced by only ever paying cash in full for non-negative-payoff instruments. If total premium outstanding ever exceeds $150,000 by one dollar, that is a defect, not a decision.

| Control | Limit |
|---|---|
| Sleeve notional cap (= sleeve max loss) | **$150,000** |
| **Tranche 1 cap while survivors < 3** | **$30,000** (20%). Steps to $75,000 at 3 survivors; to $150,000 only at 6+ independent survivors. |
| Max premium per position | **$15,000** (10% of sleeve) |
| Max premium per position at first entry | **$7,500** (5%) — the other $7,500 only on a re-verified add |
| Max premium per single underlying (all strikes/expiries) | **$15,000** |
| Max premium per theme / sector / macro factor | **$45,000** (30%). Correlated legs collapse to one observation; treat them as one bet for the cap, exactly as the t-stat rule requires. |
| Max premium expiring in any one calendar month | **$60,000** (40%) |
| Minimum uncommitted cash at all times | **$60,000** (40%) — there is no configuration in which the desk is all-in |
| Max loss per position | **premium paid + fees, and nothing else** |

**Permitted instruments:** long calls, long puts, fully-paid debit spreads (both legs same underlying, short leg strictly covered by a long leg at an equal-or-better strike and equal-or-later expiry), listed warrants purchased outright, capped-loss event contracts settled in cash.

**Banned outright, no exceptions, no override:** naked or uncovered short options; any short leg whose long cover expires earlier; futures, CFDs, perpetuals, spot margin, box/financing structures; any position requiring maintenance margin; any structure whose maximum loss cannot be stated as a single dollar figure at the moment of entry; **exercising anything** (exercise converts capped risk into an unbounded stock position).

---

## 4. HARD RULES

### Entry — all eight must pass, in writing, before capital moves

1. **A named survivor.** The candidate must have passed adversarial refutation and be on the survivor list. Today that list is empty, so this gate blocks everything.
2. **Sourced, dated catalyst with a primary URL.** Issuer IR calendar, exchange notice, regulator docket, or court docket. A date from a secondary summary is not a date. **A date you cannot source does not exist.**
3. **Catalyst date read back after write.** The desk has a live defect where `--catalyst-date` validates but is never persisted (`tfo-catalyst-date-dropped`). Re-read the position record and assert the date is there. No read-back, no position.
4. **Real two-sided quote on the actual instrument**, timestamped within 15 minutes of entry. **Enter at the ask, model the exit at the bid.** Mid is banned. If bid = 0, the instrument is banned.
5. **Tradability proven, not inferred.** "A line exists" is not "we can trade it" — this is the exact shape of the unpatched `US_LISTED` aggregator defect that scored ~$70k of untradable book as executable.
6. **Currency and quotation convention verified** (pence vs pounds, cents vs dollars, contract multiplier). The GBp/GBP defect 100x'd a screen once already.
7. **Implied move vs. historical realized event move measured.** If the market already prices the move, there is no convexity, only theta. Skip.
8. **Pre-registered thesis, invalidation condition, and exit plan, written before entry.** A rule written after a position opens cannot invalidate it (`tfo-attribution-no-skill`).

### Exit — profit

- Sell **1/3 at 3.0x** premium.
- Sell **1/3 at 8.0x** premium.
- Final 1/3 runs to the catalyst resolution or the time stop, whichever comes first.

**Stated honestly: this rule reduces P(21x) further, from ~0.4% toward ~0.1%.** It is chosen deliberately to protect capital, not to chase the mandate. If the funder wants the scaling removed to preserve the 21x path, that is a request to raise ruin probability and must be escalated in writing, not configured away.

### Exit — stop

Premium-percentage stops are noise on long convexity; these are the real stops:

- **Thesis stop:** the sourced catalyst is cancelled, or moves past the position's expiry → close at the bid, same session.
- **Date-change stop:** the primary source's date changes at all → close, re-underwrite from scratch, do not roll.
- **Time stop:** at **21 DTE**, close any position not at ≥1.5x, unless the catalyst falls strictly inside the remaining window.
- **Liquidity stop:** if the bid goes to zero or the spread exceeds 50% of mid for two consecutive sessions, mark the position at zero in reporting and stop counting it as an asset. Do not let a stale mark flatter the book (`carry-monitor-stale-funding-rows`).

### Expiry handling

- **Never hold a spread into expiry week.** Close all legs by **T-2 sessions**. Early assignment on an American short leg leaves a stock position overnight — that is unbounded risk arriving through a door labelled "defined risk."
- **Close before ex-dividend** on any American structure with a short leg.
- **Never exercise.** Sell the long; do not take delivery.
- Cash-settled European contracts may expire worthless untouched, and only if fully OTM.
- **Nothing rolls.** A roll is a new position and must pass all eight entry gates from zero.

---

## 5. WHAT STOPS THE WHOLE SLEEVE

Any one of these halts all new entries immediately and forces a written review:

1. **Cumulative sleeve loss reaches $75,000 (50%).** Do not run it to the $150,000 structural cap.
2. **Any breach of the defined-risk invariant** — a short leg uncovered for any interval, margin used, an assignment leaving stock, a position whose max loss is not a stated number. Halt on discovery, flatten the offending leg, audit every open position.
3. **Any position found with an unsourced, unverified, or non-persisted catalyst date.** Halt and audit all. This has happened here before.
4. **Any fabricated or unverified price reaching a gate.** Halt.
5. **Any request to remove the loss cap, add leverage, add naked legs, or "size up to make the number."** That is not a parameter change; it is a change of mandate, and it converts a −9.8%-of-firm downside into the ~41%-chance-of-losing-half scenario. Escalate; do not implement.
6. **Day 30 checkpoint:** if the sleeve is not at ≥2.5x, the remaining path requires concentration the risk rules forbid. Stop and return the remaining premium to cash rather than escalate. Doubling down at the halfway mark is where firms die.
7. **Horizon:** the sleeve terminates at the mandate horizon. Nothing rolls into a "next window."
8. **And, first of all: the sleeve does not start.** With `SURVIVORS = []`, rule 1 of the entry gate blocks every entry. The correct action today is to deploy **$0** and tell the funder the 3x is not available.

---

## 6. HONESTY LEDGER — what is proven vs. what is judgment vs. what I could not check

**Arithmetic, exact, from the mandate's own figures:** $4,580,964 / $1,526,988 = 3.000x; shortfall $3,053,976; sleeve must reach $3,203,976 = **21.36x**; firm rate +1.8479%/day over 60 days; sleeve rate **+5.2349%/day** over 60 days.

**Theorem, requires only r and non-negative payoffs:** the 4.71% probability ceiling, and the fact that it *falls* with every dollar overpaid. This cannot be beaten by better security selection.

**Model, with assumptions stated:** the binomial P(21x) table assumes independence between legs and all-or-nothing payoffs. Real legs are positively correlated through macro, which slightly *raises* the extreme-upside probability while making the sleeve a single bet — it does not change any conclusion.

**Judgment, labelled as judgment:** the <1% / 0.3–0.5% point estimate for this desk, and the 30% central drag assumption behind the −$44,000 EV.

**DATA_UNAVAILABLE — could not verify in this session:**
- **No live bid/ask was read on any instrument.** The cost fraction `c` is an assumption, not a measurement. Before any entry, `c` must be measured on the actual instrument and the EV recomputed with the measured number.
- **No catalyst was sourced.** I claim zero catalysts, on zero dates, in zero geographies. Nothing in this document should be read as identifying an opportunity.
- **The academic base rates on long-option returns (variance risk premium, lottery/skew overpricing) are directionally well established but I could not fetch and cite a URL this session — the web-search budget was exhausted (200/200). They are therefore marked UNSOURCED and are not load-bearing:** the transaction-cost term alone already makes the EV negative without them.

**Bottom line for the funder, in one sentence:** the sleeve's honest probability of delivering the 3x is under 1% against a hard ceiling of 4.7%, its honest expected value is about **−$45,000**, its most likely single outcome is **−$150,000**, and the only way to raise the probability is to remove the very loss cap that makes it survivable — so the recommendation is to deploy **$0** and tell the funder the target is not reachable with capped-loss instruments.