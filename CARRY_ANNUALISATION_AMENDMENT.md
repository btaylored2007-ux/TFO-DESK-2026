# CM-071 — Kraken funding was annualised 8× too low

**Date:** 2026-09-03 · **Status:** LIVE, behaviour-preserving · **PAPER ONLY**
**Found by:** the 75-agent hard-rules study (§7.3)

## The defect

`carry_monitor.py` carried:

```python
# Kraken funding rates are quoted per 8-hour interval -> 3 per day.
INTERVALS_PER_YEAR = 3 * 365
```

The comment was wrong. **Kraken Futures charges funding hourly**, so the correct
factor is `24 * 365`. Every carry number this desk has published — dashboards,
reports, the carry monitor panel, `book_coverage`, `make_desk_report` — was
understated by **8×**.

### Verified against the data, not the documentation

2,009 samples over 168 hours:

| perp | as per-8h (old) | as hourly (correct) |
|---|---|---|
| PF_XBTUSD | 0.872%/yr | **6.978%/yr** |
| PF_ETHUSD | 0.876%/yr | **7.009%/yr** |
| PF_SOLUSD | 0.115%/yr | 0.922%/yr |

Sustained sub-1%/yr funding on the two largest perps is not a real market state.
Under the hourly reading the same data gives an ordinary funding regime. The data
chose the constant.

## The trap, and why the gate moved in the same commit

Fixing the constant alone would have been a **silent 8× loosening of a live entry
gate**, wearing the clothes of a bug fix.

The old gate demanded 4% of an 8×-understated number — a **true 32%/yr hurdle**.
That is why the book opened 2 episodes in 392 days. Correcting the annualisation
while leaving `HARVEST_THRESHOLD_PCT = 4.0` would have left a gate that fires on
genuinely-4% baskets: eight times more often, on a book with no validated edge.

The desk had already measured the sign of that mistake. A rule test in the same
study: relaxing this gate from 4% to 2% costs **−4.27 pp/yr**. Loosening it 8×
would be that mistake, several times over.

So both thresholds were raised 8× in the same commit:

| constant | was | now | meaning |
|---|---|---|---|
| `HARVEST_THRESHOLD_PCT` | 4.0 | **32.0** | identical real hurdle |
| `WATCH_THRESHOLD_PCT` | 2.0 | **16.0** | identical real hurdle |

**32.0 and 16.0 are not new risk settings. They are the old settings restated in
corrected units.**

## Verification

Replayed 168h of funding through both the old and new constants:

```
verdicts unchanged on 8 of 8 perps; 0 changed
```

**What is reported changed. What is traded did not.**

## The open question this now makes askable

Is a true **32%/yr** hurdle the right one? That was never a decision — it was an
arithmetic accident, and until today it could not even be stated in honest units.
It is now the funder's to make, with real numbers on both sides. Lowering it is a
genuine loosening and must be decided explicitly, never as a side effect.

Also still open from §7.5: the live carry book shows A$13.78 accrued since
2026-08-21 where the corrected rate implies ≈A$110. That gap is a separate
accrual defect and is NOT fixed here.

Backup: `/tmp/cy_backup/carry_monitor.py.pre_cm071`
