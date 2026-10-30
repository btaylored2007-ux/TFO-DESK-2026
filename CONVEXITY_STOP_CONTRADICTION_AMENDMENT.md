# CM-070 — an unresolved contradiction in the convexity sleeve's exit rules

**Date:** 2026-09-03 · **Status:** OPEN — funder decision required · **Deployed: $0**

Two documents froze on the same day with directly opposed exit rules, because two
independent analyses ran concurrently. Nothing is deployed, so nothing has been
harmed yet — but this must be settled before the first position opens.

## The contradiction

`CONVEXITY_SLEEVE_PREREGISTRATION.md` (from the 37-agent sizing study) says:

> "Premium-percentage stops are noise on long convexity; these are the real
> stops: thesis stop, date-change stop, time stop at 21 DTE, liquidity stop."

`convexity_sleeve.py` line ~251 implements exactly what that sentence rejects:

```python
STOP_FRACTION = 0.50    # cut at 50% of premium lost — half the ticket back
```

## Why this is not cosmetic

A far-OTM option routinely loses 50% of its premium and then recovers **on the
catalyst**. The catalyst is the entire thesis. A -50% premium stop therefore
systematically sells the position immediately BEFORE the event it was bought for,
harvesting the loss and forfeiting the tail. On a book whose whole expected value
sits in the right tail, that converts a low-probability 21x path into a
high-probability total loss with extra steps.

The sleeve already carries the two exits that genuinely control this risk, and
they fire on information rather than on price noise:
  * `thesis_dead` -> exit at once
  * `catalyst_date` passed unresolved -> exit

## Resolved on the same pass (not left contradictory)

The reciprocal conflict — a 3x/8x profit ladder in the study versus "winners are
never trimmed" in the code — is settled in the CODE's favour and the constant is
renamed `PROFIT_LADDER_NOT_IN_FORCE` with the reasoning inline. The code is right:
this desk MEASURED a hard take-profit cutting a breakout book from +3.393% to
+0.108%, and the study itself conceded its ladder "reduces P(21x) from ~0.4%
toward ~0.1%". A dead constant that looks live is a trap for the next reader.

## Recommendation

**Remove `STOP_FRACTION`,** leaving thesis-death, catalyst-passed, liquidity and
time stops. Keeping it is defensible ONLY as capital protection that the funder
knowingly prefers over the mandate — the same trade the profit ladder represented,
and it should be decided once, explicitly, rather than twice by accident.

Not changed unilaterally: it tightens or loosens a live risk control depending on
which way you read it, and the sleeve is the funder's declared experiment.
