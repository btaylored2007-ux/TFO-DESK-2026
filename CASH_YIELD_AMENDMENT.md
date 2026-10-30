# Uniform Cash Yield on Idle Capital — Amendment (ACTIVE, dated 2026-08-27)

**Status:** ACTIVE. Applies to every paper book on this desk that carries a cash
balance: all `tournament.py` sleeves, `growth_book.py`, and `value_book.py`.
PAPER ONLY: no broker, no credentials, no real orders.

**Its own clock.** This is a NEW amendment with its OWN record, opened
2026-08-27. It does **not** retroactively change any book's existing
pre-registered track record, and it does not edit any existing registration —
those documents carry only an append-only pointer to this one. Every number any
book published before its first accrual instant stands exactly as recorded.

---

## 1. Basis

### 1.1 Funder direction

**Funder direction, 2026-08-27: "GO FOR IT."** The funder was shown the
measurement in §1.2 and the argument in §1.3 and directed that idle cash be
credited at the live short-term T-bill rate, uniformly, across every book.

*(Provenance: the direction was given in session and relayed to the implementing
agent as "GO FOR IT". This document records it as directed; it is not a
counter-signed instrument, and §7 states exactly what was and was not attested.)*

### 1.2 The measurement

At the moment of direction, **$164,004.69** of paper capital sat as cash across
the desk's books, earning **0.00%** — because the desk models cash at zero, not
because it chose a zero-yield instrument. At the live 13-week T-bill rate
(**^IRX, 3.690%**) that is roughly **$6,053/yr** of return the books forgo.

Measured cash by book at implementation (2026-08-27, pre-first-accrual):

| Book | Cash | Book | Cash |
|---|---|---|---|
| capacity_probe | $70,025.67 | rally_rider | $2,725.77 |
| rally_rider_x | $58,317.37 | claude_discretionary | $490.50 |
| dip_rider | $10,000.00 | rally_rider_hold | $18.23 |
| cef_discount | $9,975.00 | growth_book | −$50.00 |
| value_book | $9,045.94 | *(6 sleeves at small negative cash)* | — |
| covered_call_etf | $3,424.43 | | |

### 1.3 Why this matters more than the dollars — it removes a bias

$6k/yr is not the point. **Modelling cash at 0% structurally penalises the books
that correctly hold cash.**

- `dip_rider` holds $10,000 in cash **on purpose**, waiting for a flush. Its
  registered edge *is* the discipline of not being deployed.
- `capacity_probe` holds $70,025 in cash because it **refuses fills its book
  cannot absorb**. Refusing is the strategy.

Against them sit books that are always deployed. At 0% cash, waiting costs the
waiter its entire opportunity return while the always-in book collects the
market's. The leaderboard then reports *deployment habit* as *skill*, and the
**November verdict tilts in favour of always-in strategies — precisely the
behaviour this desk's accumulated evidence says loses money.**

Crediting cash at the risk-free rate is what the real world does. It does not
advantage the cash-holders; it stops penalising them. **This amendment removes a
known bias. It does not introduce one** — the credit is uniform, rate-identical,
and applies to every book on the same terms.

---

## 2. The frozen rule

Implemented in `cash_yield.py`; frozen as of 2026-08-27.

**2.1 Instrument — SHORT-TERM T-BILLS ONLY.**
Rate source: **`^IRX`, the 13-week US Treasury bill discount rate**, via
`yfinance`, quoted in **percent** (`3.690` means 3.69% per annum).

**2.2 Accrual formula.** On each tick, for each book:

```
interest = cash_balance * (rate / 100) * (elapsed_seconds / (365 * 86400))
```

`elapsed_seconds` is the **ACTUAL wall-clock time since that book's own last
accrual** — not a per-tick constant. A change of tick cadence (hourly to
15-minutely, or an outage that skips six hours) therefore **cannot** distort the
credited amount. The accrual clock advances on every call, including calls that
credit zero.

**2.3 Forward-only. Never retroactive.** A book's **first** accrual sets its
baseline instant, records `cash_yield_since` (UTC), and credits **exactly
$0.00**. There is no path in the code that credits interest for any period
before that instant. Back-crediting would rewrite existing published history and
is prohibited.

**2.4 Fail-safe — accrue ZERO, loudly.** Interest is **zero** for that tick if:

| Condition | Result |
|---|---|
| `^IRX` cannot be fetched and no usable cache exists | ZERO |
| rate `<= 0` | ZERO |
| rate `> 25` (absurd) | ZERO |
| rate is NaN / non-numeric | ZERO |
| cached rate is **older than 24h** | ZERO — the cache is **REFUSED**, never carried forward |
| newest `^IRX` bar is older than 7 days (dead feed) | ZERO |
| elapsed time `<= 0` (clock skew) | ZERO |
| `cash_balance <= 0` | ZERO (see §2.6) |
| anything raises inside the module | ZERO |

Every zero-because-of-failure path **prints a loud `CASH-YIELD ERROR:` line and
persists an error marker** at `reports/cash_yield_error.json`. Nothing here
fails silently. **The rate is never guessed, never interpolated, never carried
forward past 24h.** Because the accrual clock still advances, a failed tick's
interest is **forgone, not deferred** — a later successful tick can never sweep
up a period the fail-safe already declared zero.

The on-disk rate cache (`reports/cash_yield_rate.json`) refreshes **at most
hourly** and is **refused outright once it is 24h old**.

**2.5 Separable and auditable — MANDATORY.** Each book tracks, as its **own**
fields:

| Field | Meaning |
|---|---|
| `cash_interest_accrued` | **cumulative** interest credited to this book, ever |
| `cash_yield_since` | UTC instant this book started accruing |
| `last_rate_used` | the `^IRX` percent used on the last credited tick |
| `cash_yield_rate_asof` / `cash_yield_rate_bar_date` | when the rate was observed / which `^IRX` bar |
| `cash_yield_last_interest` / `cash_yield_last_elapsed_s` | last tick's credit and its elapsed seconds |
| `cash_yield_error` | non-null whenever the last tick failed |

Interest **is** added to cash, so equity is economically correct. But the
cumulative figure remains **separately readable at all times**, so the November
review can compute:

```
strategy return = total return − cash_interest_accrued
```

This is **not optional**. An interest credit that could not be subtracted back
out would flatter every book invisibly and would make the leaderboard *less*
honest than the 0% model it replaced.

**2.6 Negative cash accrues nothing.** Several sleeves carry a small negative
cash balance (a rounding residue of charged costs). A negative balance is not
idle capital: it credits **zero**, and **no borrow charge is modelled**.
Inventing a funding cost would be a *different* rule that the funder did not
direct. (This matches the funder's own $164,004.69 measurement, which counts
positive cash only.)

**2.7 Uniform.** Every book that holds a cash balance accrues, at the same rate,
by the same formula, on the same clock. **No strategy is advantaged over
another.** Frozen (killed) sleeves accrue too, and their recorded equity is
advanced by the same amount so cash and equity never disagree.

---

## 3. T-bills, NOT bonds — and why that is a rule, not a preference

The instrument is fixed at **short-term Treasury bills (13-week, `^IRX`)**.
Longer-dated Treasuries are **prohibited** as the rate source.

A bond yield is compensation for **duration risk** — the price of a 10-year note
moves violently with rates, and a book "holding bonds" would be carrying an
interest-rate position. Crediting a bond yield to idle cash would therefore
**bolt an unintended macro bet onto every book on the desk**: each sleeve would
be implicitly long duration, its P&L would acquire a rates factor it never
registered, and the November comparison would be contaminated by a bet nobody
chose. It would also be a *free lunch* on paper — the yield credited without the
mark-to-market loss that a real bondholder eats when rates rise.

A 13-week bill is the closest available proxy to genuinely risk-free overnight
cash: near-zero duration, near-zero credit risk, and the actual instrument a
cash-holding desk would use. **The point of this amendment is to remove a
distortion, so the instrument used to remove it must not add one.**

---

## 4. Scope and implementation

| File | Change |
|---|---|
| `cash_yield.py` | **new** — shared module: `current_rate()`, `accrue()` |
| `tournament.py` | insertion in the shared per-sleeve loop in `tick()` |
| `growth_book.py` | insertion at the top of `tick()` |
| `value_book.py` | insertion at the top of `tick()` |

**Frozen-code compliance.** Every insertion is an **addition**. **Zero lines
were deleted from any existing tick function**, and not one byte of any
`tick_*` sleeve function, of `rebalance()`, or of `set_holdings()` was changed.
In `tournament.py` the call sits in the shared loop *around* the sleeve
functions, so all 17 sleeves accrue without any sleeve body being touched.
Verified byte-for-byte: removing the inserted blocks reproduces the original
files exactly.

**Structural safety of `accrue()`.** The function receives a **state dict and
the cash NUMBER** — never a ledger root, never a price map. It writes only its
own `cash_yield_*` bookkeeping keys, and on **every** call asserts on exit that
`holdings`, `positions`, `entry_px`, `cash`, `equity`, `costs`, `costs_paid`,
`units`, `peak` and `frozen` are untouched, restoring and screaming if they are
not. It cannot trade. The **caller** adds the returned interest to cash.

---

## 5. What this amendment does NOT do

- It does **not** change any entry rule, exit rule, sizing rule, cost model,
  rebalance cadence, kill rule or drawdown gate.
- It does **not** touch a single holding, unit count or entry price.
- It does **not** retro-credit one second of interest.
- It does **not** model borrowing, leverage or a funding charge.
- It does **not** introduce duration, credit or FX exposure.
- It does **not** alter `kraken_paper_bot.py`, any engine or supervisor, the
  carry executors, or anything OANDA.

---

## 6. Kill / revision criteria

This rule is frozen. It is revisited only if:

1. `^IRX` is discontinued or its quoting convention changes — then the
   fail-safe accrues zero until a fresh amendment names a replacement.
2. The error marker persists for more than 48h, indicating a dead rate feed.
3. The funder directs a change, in which case a **new** amendment with its own
   clock is written; this one is never edited in place.

Any widening — a different instrument, a duration-bearing rate, a borrow charge,
or any retroactive application — requires a fresh direction and a new amendment.

---

## 7. Funder sign-off

- [x] **APPROVED** by (funder): **Brett Taylor**   Date: **2026-08-27**
      Direction: credit idle cash at the live short-term T-bill rate, uniformly
      across every book; given explicitly as **"GO FOR IT"**.
- [x] Scope confirmed: all `tournament.py` sleeves, `growth_book.py`,
      `value_book.py`. Cash bookkeeping only.
- [x] Instrument confirmed: **13-week T-bills (`^IRX`) only — NOT bonds.**
- [x] Forward-only confirmed: no retroactive credit, ever.
- [x] Separability confirmed: `cash_interest_accrued` tracked per book so
      strategy return can always be split from interest return.
- [x] No risk control loosened; no trading rule changed; this amendment only
      ADDS a cash bookkeeping credit.
- [x] PAPER ONLY. Public data only. No credentials. No real orders.

**What is attested, precisely:** that the funder directed this rule on
2026-08-27, as relayed to the implementing agent ("GO FOR IT"), and that the
implementation matches §2 as written. What is **not** claimed: a countersigned
document, a spec hash sealed in the funder's presence, or any approval beyond
the scope above.

---

## 8. Implementation & verification record (2026-08-27)

**Rate fetched at implementation:** `^IRX` = **3.6900%** per annum, from the
settled bar dated **2026-08-26**, observed **2026-08-27T08:46:09Z**. This is the
same 3.690% the funder quoted. At that rate, $100,000 of idle cash earns
**$10.1096 per day**.

**Baseline tick (tick 1), 2026-08-27T08:47:29Z–08:47:58Z.** All 19 books had
`cash_yield_since` stamped and credited **exactly $0.00** — the forward-only
rule working as designed. No cash moved. No error marker was written.

**Accrual tick (tick 2), 2026-08-27T08:49:50Z–08:50:21Z**, ~140s after baseline.
Interest credited on the 9 books holding positive cash; **all 19 hand-checks
matched `cash * 3.690% * elapsed/365d` to 0.0e+00**:

| Book | Cash | Elapsed | Interest credited |
|---|---|---|---|
| capacity_probe | $70,025.67 | 140.0s | $0.011475185 |
| rally_rider_x | $58,317.37 | 140.2s | $0.009568054 |
| dip_rider | $10,000.00 | 140.0s | $0.001638704 |
| cef_discount | $9,975.00 | 140.0s | $0.001634058 |
| value_book | $9,045.94 | 138.2s | $0.001462277 |
| covered_call_etf | $3,424.43 | 141.0s | $0.000565119 |
| rally_rider | $2,725.77 | 140.2s | $0.000447126 |
| claude_discretionary | $490.50 | 140.2s | $0.000080440 |
| rally_rider_hold | $18.23 | 140.2s | $0.000002990 |
| *8 books at cash ≤ 0 (incl. growth_book)* | ≤ $0 | ~140s | **$0.000000000** |
| **Desk total** | | | **$0.026873954** |

Idle (positive) cash now earning: **$164,022.95** → **$16.58/day**,
**$6,052.45/yr** at 3.690% — matching the funder's ~$6,053 measurement.

**Integrity, verified twice** (against the pre-edit snapshot and against the
post-baseline snapshot): **0 violations**. `holdings`, `entry_px`,
`entry_px_usd`, `units`, `costs`, `costs_paid`, `rebalances`,
`last_rebalance_date`, `frozen` and `start` were byte-identical for all 19
books. Every book's cash moved by **exactly** its `cash_interest_accrued`, to
within 1e-9. Function-body hashing (qualified names, exact source segments)
confirms **all 17 `tick_*` sleeve functions, `set_holdings`, `mark`,
`rebalance`, `equity_of`, `price_book` and `current_picks` are byte-identical**;
the only changed function in each file is the module-level `tick`, and removing
the inserted block reproduces the original file byte-for-byte.

**Fail-safe, exercised end-to-end** on an isolated cache (live cache untouched):
fetch failure with a 3h cache → serves cache with a loud WARN; fetch failure
with a 30h cache → **REFUSED**, zero accrued, marker written, cumulative figure
unchanged; no cache at all → zero + marker; a 99% rate → rejected by the sanity
gate; and a 2h outage followed by a good tick credited **1 second**, not 2 hours
— proving a failed period is **forgone, not deferred**.

**Where to look:**

- rate cache — `reports/cash_yield_rate.json`
- error marker (absent when healthy) — `reports/cash_yield_error.json`
- per-book fields — `reports/tournament/<sleeve>.json`,
  `reports/growth_book.json`, `reports/value_book.json`
- live diagnostic — `python3 cash_yield.py`

All stamps are UTC.

### Post-review fix — bad rates may never be masked by the cache (2026-08-27)

Hostile review returned **PARTIAL**: arithmetic PASS (19/19 books hand-recomputed,
relative diff exactly 0; the 100x percent-vs-fraction trap cleared) and
forward-only PASS ("the strongest part of the build" — every backdating vector
credited exactly $0.00). But the **fail-safe FAILED whenever a fresh cache
existed**, which was the live state: a live rate of −1.0 or 99.0, or a fetch that
raised, all quietly served the cached 3.69% and credited interest, logging a
soft WARN with no error marker.

**Fix:** a new `RateQualityError` separates a **source that answers with garbage**
(bad value, NaN, feed dead >7d) from a **transport hiccup**. A quality failure now
accrues ZERO, prints `CASH-YIELD ERROR` and writes the marker — the cache is
never allowed to paper over a broken source. A transport failure may still serve
a cache younger than 24h, which is what a cache is for.

Verified after the fix, with a fresh cache deliberately present: live rate −1.0,
99.0 and a 30-day-dead feed each accrue exactly 0.0 with marker + state error;
a simulated network error still serves the 2h-old cache with a WARN.

This is the same failure class that left the Kraken engine blind for four days —
a fallback that hides an outage behind a plausible number.

### Disclosed conservatism (from the same review)
`^IRX` is a **discount-basis** quote and T-bills accrue **ACT/360**; this desk
applies **ACT/365** to the discount rate, so it **UNDER-credits by roughly 3–4%
relative**. Left as-is: erring toward under-crediting is the correct direction
for an amendment that adds return to every book.

---

## CM-068 — extension to the seven uncovered books (2026-09-03, funder-directed)

Funder instruction: "WIRE THE CASH YEIALD INTO THOSE 12 BOOKS."

Nine tournament sleeves had accrued since 2026-08-27. Eight further books held
**$325,464.10 of idle cash earning exactly zero**. Seven are now wired; the
eighth is deliberately not (below).

| book | idle cash | cash containers |
|---|---|---|
| kraken_floor | $88,875.56 | 3 desks |
| replica_floor | $85,844.37 | 3 desks |
| fundamental_book | $65,478.96 | top level |
| fundamental_spec_book | $22,018.75 | top level |
| carry_newnames_probe | $10,000.00 | top level |
| cross_asset_floor | $9,555.49 | 2 desks |
| coinspot_floor | $3,571.70 | 2 desks |
| **wired total** | **$285,344.83** | **= $10,763/yr at 3.772%** |

**NOT wired: `kraken_paper_bot.py` (Kraken Engine), $40,119.27 = $1,513/yr.**
It is a PROTECTED file — never edited without explicit funder authorisation.
That cash is named here as still earning zero rather than quietly swept in.

### Terms (unchanged from the original amendment)
Accrual is **forward-only from the first tick — no retroactive credit**. A
missing, absurd or >24h-stale rate accrues **ZERO, loudly**. Interest is kept in
its own `cash_interest_accrued` field per container, so rate income can never be
mistaken for trading skill — the attribution study found ZERO skill books in 32,
and this must not become the thirty-third.

Accrual is **per container**, not per book: each desk keeps its own clock, so a
desk added later cannot inherit another desk's elapsed time and back-bill it.

### Verification performed
- Second-tick arithmetic checked against hand calculation: accrued $9.184619 vs
  expected $9.184620 on $88,875.56 for 24h. (First check FAILED — my expected
  value was 100x out because `current_rate()` returns PERCENT, not a fraction.
  The code was right; the check was wrong.)
- All 7 ticked live: every cash container stamped a baseline, **zero position
  changes**, no `cash_yield_error` on any book.
- Books with zero/negative cash correctly accrue nothing (`shareholder_yield`).

Implementation: `cash_yield_books.py` (`accrue_book`), called from each book's
tick immediately after ledger load. Backups: `/tmp/cy_backup/`.

---

## CM-070 — never pay T-bill on short proceeds (2026-09-03)

Found by the 632-position audit, re-derived independently before fixing.

`set_holdings` computes `cash = equity - sum(units * px) - cost`. For a
DOLLAR-NEUTRAL long/short sleeve the long and short legs cancel in that sum, so
`cash` lands at roughly full equity while half the book is a short. CM-068 then
paid T-bill on that figure — crediting interest on **short proceeds**, while the
tournament charges **no borrow** on the short leg.

Free money in the flattering direction, which is the exact failure mode this
desk exists to catch.

**Measured, not asserted.** `cef_discount` is the only sleeve currently short:

| | |
|---|---|
| cash line | $9,982.12 |
| short market value | −$5,000.00 |
| yield-eligible cash | **$4,982.12** |
| interest wrongly credited | **$188.60/yr** |

On a $10,000 sleeve that is 1.89%/yr, and with the omitted borrow (~1.5%/yr at
the 3% elevated tier) it is ~3.4%/yr against a claimed ~7%/yr edge. **Roughly
half the "edge" was bookkeeping.**

### Fix
`cash_yield_books.yield_eligible_cash(state)` subtracts the absolute market
value of short holdings and floors at zero. `tournament.py` accrues on that
instead of the raw cash line. It can only ever REDUCE credited interest — a
tightening, never a loosening. Books with no `holdings` dict (the seven wired in
CM-068) are unaffected and verified unchanged.

### NOT fixed here — flagged, not silently left
The tournament charges **zero borrow** on short legs. Fixing that changes the
economics of a pre-registered sleeve, so it is the funder's call, not mine.
Until it is charged, every dollar-neutral sleeve's return is overstated by the
borrow it never paid. `fundamental_borrow.py` already has the tier model
(liquid 0.50% / elevated 3.00% / hard 15.00%) if it is wanted.
