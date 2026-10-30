# DATED-EVENT LONG BOOK — pre-registration

**Registered:** 2026-09-06 · **Status:** LIVE · **Funder-directed:** "do b."
**Capital:** $75,000 transferred from the convexity sleeve's cash (10 flat tickets), recorded as a
capital event in BOTH ledgers. The sleeve's seed moves with the cash so neither side reads the
transfer as P&L (CM-052).

## Where the strategy came from — the desk's own tape, not a hunt

Thirty analysts mined 33 books and 195 closed trades. What fell out: every positive dollar on the
firm came from **held listed-equity longs** and names **carried through a real information
event**. Every short leg on every desk lost. Every exit lab says holding beats early exit. Every
cost lab says hold 30+ days. Small equal tickets contained the tail.

The convexity sleeve's sealed registration (line 133) permits only options, warrants and
cash-settled contracts. Long equity was never on that list; TAP and 7004.T sit there as funder
exceptions. Rather than write a third, the funder chose a book whose charter says what it holds.

## ENTRY — all six or no trade

1. **Listed equity, LONG.** No shorts under this charter.
2. **Issuer-sourced information event 5–90 trading days ahead** — results, regulatory decision,
   shareholder vote, index effective date. Not dividend, record or administrative 8-K dates.
   URL recorded; the URL must **state** the date (CM-069). The event has **not yet happened**.
   *Why 5 and not the analysts' 20:* the 20-day floor had one stated rationale — the desk's
   habit of arriving **after** the event. A 5-day floor preserves that. Cost amortisation is
   handled separately by rule 5. Twenty was a preference, not a measured threshold.
3. **CONFIRMS-IT and KILLS-IT limbs written before entry.** A met CONFIRMS-IT outranks a
   KILLS-IT breached by a disclosed one-off.
4. **Fill at the next session's open** after the research is timestamped — first live quote
   after the open. **Never previousClose** (52 of 65 fundamental entries did; ~$1.2k unearnable).
5. **Declared hold ≥ 30 trading days.**
6. **Position ≤ 1% of 20-day ADV.**

## SIZE

Flat **$7,500 per name.** No conviction multiplier — this desk measured it cannot rank its own
ideas (HIGH-confidence tier −2.62%, LOW +2.09%). Caps: 20% per sector, 20% per currency bloc,
enforced from 5 names. Target 20–30 names. Everything else in cash at the T-bill rate.

## EXIT

Hold **through** the event. **No +5% scale-out rung** — that rule is right on the fundamentals
desk and wrong here (exit_axis N=1,190 and spec_rally t=−2.01 both say it loses to holding).
Close at the later of: event day + 5 sessions, or the 30-trading-day hold. One roll permitted if
CONFIRMS-IT is met and a new dated event is sourced. KILLS-IT met on a document dated after
entry: close next open. **No price stop on longs** (10-year study: mean negative at every stop
level; win rate falls as stops tighten). At **−25%**, a mandatory thesis re-read within 5
sessions, outcome recorded. Log t−1, t, t+1, t+5 closes per event — **one observation per event.**

## KILL

**Book:** −18% from an identity-computed peak freezes new entries. Never force-liquidates.
**Strategy:** dead at **20 completed events or 120 trading days**, whichever first, if mean net
return per event ≤ 0, OR hit rate of favourable t+1 close ≤ 50%, OR mean excess over the local
index ≤ 0, OR the book trails equal-weight buy-and-hold of its own names ("the return is the
market"). **Negative results are reported, not shelved.**

## Honest expected return — stated before any outcome

Cash 3.77% **plus 0 to +3%/yr excess** on deployed capital; central **+1%/yr**.
P(beats cash over 12 months) ≈ 55%. P(reaches the 9.49% sleeve target) ≈ 15%.
Per event: favourable t+1 move ≈ +1 to +2% net at a 55–60% hit rate.
One-sigma on a 20-name year: ±10–12% of deployed capital.
**Anyone quoting more than +3%/yr excess for this book is not reading its evidence.**

## Frozen artefacts

`dated_event_book.py` constants: `TICKET_USD 7500`, `EVENT_MIN_TD 5`, `EVENT_MAX_TD 90`,
`HOLD_MIN_TD 30`, `EVENT_EXIT_TD 5`, `MAX_ADV_FRACTION 0.01`, `REREAD_DRAWDOWN -0.25`,
`KILL_EVENTS 20`, `KILL_TD 120`, `BOOK_KILL_DD -0.18`. Editing them invalidates this registration.

**SEAL (sha256 of this file + dated_event_book.py at registration):** `a92921ab099cac406069e0e5066e181adbf627ecb7f11713286a908f23a041f6`
