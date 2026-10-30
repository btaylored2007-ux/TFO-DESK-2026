# TFO FUNDAMENTAL DESK — PRE-REGISTRATION

**Sealed 2026-08-27. FROZEN. Never edit this document to fit a result.**

Funder-directed 2026-08-27 (Brett). PAPER ONLY — no broker is connected anywhere
and none may be added.

---

## 1. WHAT IS BEING REGISTERED

A **NEW $500,000 paper book** of global long/short equity positions, opened from
the global fundamental value screen of 2026-08-27.

It is **additional capital**. It did not resize, reweight or draw from any of the
firm's other 27 books. Firm equity was **$811,357** at 2026-08-27T10:15:33Z; this
book takes the firm to **~$1.31m across 28 books**.

**Book opened:** 2026-08-27T12:14:49Z · **53 positions** (7 long / 46 short) ·
**$422,105 deployed · $77,895 cash.**

---

## 2. STANDING OF THE EVIDENCE — stated before any outcome exists

This book rests on a **cross-sectional fundamental screen with a dated catalyst
overlay**. It has run:

- **NO backtest**
- **NO search-deflation**
- **NO purged/embargoed cross-validation**
- **NONE of the three kill-tests**

**It carries no validated edge and must never be described as if it does.**

It cannot be backtested honestly: Yahoo financials are as-restated-today rather
than point-in-time, and the universe is today's constituents. Backtesting would
import look-ahead bias and survivorship, exactly as `tools/value_screen.py` warns
in its own docstring. **The forward record IS the test.** That is why this
document exists and why it is sealed before any outcome is known.

**Its honest claim:** 326 names screened by a disciplined method with every
reject retained, producing 8 long and 51 short opinions, each carrying a dated
reason and a named disconfirming condition.

### 2.1 Registration path — a documented exception

The Exploration Mandate's default path is: validate in `quant_validation.py`,
then one pre-registered $10k probe per survivor. **This book does not take that
path.** It registers as a **funder-directed documented exception on the
`rally_rider` precedent** (live 2026-08-20 as a documented survivors-only
exception). The departure is recorded here rather than implied.

### 2.2 Multiple-testing cost, which travels permanently

**326 screened · 59 directional · 8 undervalued.**

That is a large search surface and it raises the deflation bar for **every other
candidate the firm tests from here**. This count must be logged with every future
claim touching this dataset.

### 2.3 The naked-short departure, recorded as a departure

**46 of 53 open positions are shorts**, $142,105 gross. The firm's own 14-year
shorts study found the same signal expressed as **CASH beat expressing it as
SHORT by ~15pp/yr**, and the firm has not run naked shorts outside a registered
rule. This book does. **Funder-directed, deliberate, and recorded so the forward
record measures it honestly.**

---

## 3. THE TRADING RULE — original to this registration, not an amendment

Funder-directed 2026-08-27, **before this document was sealed**. It is therefore
part of the original registration rather than a rule fitted to a result. That
distinction is the reason it was built first and sealed second.

> *"Exit fill of MIN 5% over fill and exit cost, and then scaled if it is
> rallying."*

**Clause 1 — the 5% NET gate.** No exit is taken unless the fill clears entry
cost, exit cost **and modelled borrow**, and leaves 5% on top. Net, not gross: a
position up 5.0% gross nets 4.70% on the modelled 30bps round trip and is
**REFUSED**. The book only ever trades when trading pays for itself.

**Clause 2 — scale while it rallies.** Clearing the gate does not close the
position. A ladder releases **25% at +5%, +10% and +15% net**, and a rung fires
**only on a NEW net-gain high** — never on a retrace through a level already
taken. The final 25% runs until net gain falls **5 percentage points off its own
peak**, then comes off.

Applies symmetrically to shorts.

**Frozen constants** (`fundamental_exits.py`): `MIN_NET_GAIN 0.05` ·
`ENTRY_COST_BPS 10` · `EXIT_COST_BPS 10` · `SLIPPAGE_BPS 5` (per side) ·
`ROUND_TRIP_BPS 30` · `LADDER (0.05,0.25) (0.10,0.25) (0.15,0.25)` ·
`RUNNER_FRACTION 0.25` · `RUNNER_GIVEBACK 0.05`.

**Changing any constant above is a RULE CHANGE and must be registered before it
runs, never after a result is known.**

### 3.1 Borrow, modelled — and inside the gate

`fundamental_borrow.py`. Tiered, charged daily on the short's marked value, and
**subtracted inside `net_gain()`** so the 5% gate tests the right number:

| Tier | Rate/yr | Applies to |
|---|---|---|
| liquid | 0.50% | US developed large cap, deep lending pool |
| elevated (default) | 3.00% | non-US, unclassified, or crowded |
| hard | 15.00% | MARA, CLSK, KEEL, BKKT, CLF, STLA |

**These are MODELLED tiers, not quotes.** No borrow was located and no
securities-lending desk was consulted. Deliberately conservative: a too-cheap
assumption makes the gate too easy to clear, which is the failure mode that
matters. Unclassified names default to *elevated*, never to *liquid*.

**Why it is not a rounding error:** at 15%/yr over the 182-day clock the hard
tier accrues **~7.5% — larger than the entire 5% gate.** Without it, on precisely
the positions where the thesis is most aggressive, a loser could book as a winner
and the gate would never see it.

**`borrow_doubtful` — KEEL, BKKT.** Availability, not price. A short that cannot
be borrowed is not an expensive position, it is **not a position**. Flagged in
the ledger and on the dashboard, never auto-closed, and never presented as
executable.

---

## 4. THE THREE EXIT MECHANISMS — deliberately separate

1. **Trading rule** (`fundamental_exits.py`) — mechanical, automatic, no
   judgement. §3 above.
2. **Book kill** (`book_kill.py`, imported never copied) — freezes NEW entries at
   **−30%** drawdown from the book's own peak. **It never force-liquidates.** A
   frozen book still exits.
3. **Thesis break** (`fundamental_close.py`) — the pre-registered `KILLS IT`
   condition in a research block has been met. **Requires a reading of the actual
   disclosure and a QUOTED FIGURE from a NAMED, DATED PRIMARY SOURCE.** The tool
   refuses a reason with no digits in it. `CANNOT VERIFY` never closes a
   position; it escalates.

**An automated rule must not be what decides a research thesis has failed.** The
calendar is machine-kept (`fundamental_watch.py`); the judgement is not.

---

## 5. THE HYPOTHESIS BEING TESTED

Over **182 days from 2026-08-27**:

> The 7 `undervalued` longs outperform the 46 `SHORT CANDIDATE` shorts, net of
> modelled costs including borrow, with the book's total return measured against
> cash held at 0%.

**Kill:** −30% book drawdown (freezes new entries; never force-liquidates).
**Review date:** 2027-02-25 (182 days). **No verdict before review.**

---

## 6. WHAT IS DELIBERATELY NOT IN THE BOOK

**Two positions HELD, not opened** — their own research desks said do not act
until a named document is read. Capital stays cash:

- **0688.HK** $3,947 — H1 2026 released 2026-08-26, figures unobtainable.
- **1301.TW** $3,947 — reported P/B 0.699 unreconciled against 1.029 computed
  from the same provider's own balance sheet.

**Nine cross-desk conflicts at $0** — two desks reached opposing calls on one
ticker. Held out rather than resolved by file order: **PYPL** (undervalued/LONG
vs value_trap/SHORT), ADM, APA, EOG, OXY, PSX, COP, CF, NEM.

**Fifteen LONG calls at $0** — a desk flagged LONG CANDIDATE while verdicting
`fairly_priced`. Catalyst calls, not undervaluation claims; the sizing rule funds
only `undervalued`: ASML.AS, SIE.DE, BNP.PA, GLE.PA, 0700.HK, 000660.KS,
2330.TW, 2317.TW, B, BARC.L, BA.L, REL.L, AIG, AIZ, CVS.

**Execution feasibility was never assessed** on any short — no borrow located, no
short interest, no options chain, no implied volatility.

---

## 7. SEALED ARTEFACT HASHES (SHA-256)

Fixed before any outcome exists. If any of these change, the forward record is
no longer measuring what was registered.

```
aggregate.json          fff1fc5dbe314defca4d99cff4163319dfe5f2f92c345f9723ab515af27c8def
panel.json              2f7832c7339463e5f1cf1e4a332e7efb320d611cc07c33a437302cbe86241b71
aggregate.py            c62a0b29961992fb626e7d31e7f06170bbff4820feb15d1ec705b3ffacc6d5e1
blocks_*.md (concat)    d0b59a8f46d4bccacce477224e0fc54d385eadd17143d24202cb171808a55408
fundamental_book.py     d6925410c92cb85cedd397ff37b35a1219b8c495fd5f6ca71589f39b66135c7c
fundamental_exits.py    3182d7e0957f3058c2effc271bad3ed6b6c3a33c9ab6dc803d3c0a0c6d1c6da9
fundamental_borrow.py   2407646771e0a0950307a77906d4806c001446a3cf2d52def1e1575013c70144
```

**Ledger at seal:** 53 positions · cash $77,894.65 · equity $500,031.79 ·
opened 2026-08-27T12:14:49Z.

---

## 8. OPERATIONS

| | |
|---|---|
| Engine | `fundamental_book.py` (marks, accrues borrow, runs the rule) |
| Rule | `fundamental_exits.py` · Borrow `fundamental_borrow.py` |
| Calendar | `fundamental_watch.py` → `reports/fundamental_watch.json` |
| Thesis exit | `fundamental_close.py` (evidence-gated) |
| New entries | `fundamental_open_new.py` (USD-converted or it refuses) |
| Dashboard | `http://127.0.0.1:18820` — launchd `com.tfo.fundamental-dashboard` |
| Ledger | `reports/fundamental_book.json` · series `fundamental_equity.csv` |
| Tick | hourly via `desk_tick.sh`, **before** `risk_engine` and `firm_risk` |
| Coverage | registered in `book_coverage.py` as `_load_fundamental` |
| Review agent | scheduled `tfo-fundamental-desk-review`, 08:00 & 17:00 weekdays |
| Entry agent | scheduled `tfo-fundamental-desk-entries`, 09:30 weekdays |

**Known defect, recorded permanently:** the first build sized positions as
`notional_usd / local_price`, treating a yen price as dollars — Denso opened at
21 units for a claimed $40,000 position worth ~$260. Every non-USD name was wrong
the same way. That is **§3(c) of the desk's own data-hazard catalogue, walked
into by the engine built to act on it.** Fixed before the book went live; the
mis-sized ledger is quarantined at `reports/fundamental_book.MISSIZED-do-not-use.json`
rather than deleted.

---

*Sealed 2026-08-27. PAPER ONLY. Nothing in this document is investment advice.*

---

# AMENDMENT 1 — CAPITAL INJECTION. Sealed 2026-08-28T04:05Z.

**Funder-directed 2026-08-28 (Brett): "RUN IT IN THE MAIN DESK AND ADD ANOTHER $100K".**

## What changes

**$100,000 of NEW capital is added to the book.** Contributed capital rises from
**$500,000 to $600,000**. Firm equity rises by $100,000.

## What does NOT change — every rule stands

The trading rule (§3), its frozen constants, the borrow model (§3.1), the three
exit mechanisms (§4), the −30% kill, the 182-day clock and the **2027-02-25 review
date** are all unchanged. **No constant was touched.** This amendment adds capital
and nothing else. It is recorded BEFORE the capital is deployed and before the
name it funds is opened.

## THE ACCOUNTING INVARIANT — the reason this section exists

**A capital injection must NEVER be readable as a gain.** Equity moves from
~$500,334 to ~$600,334 the instant the money lands. On any equity chart that is a
**+20% step**, and there is no performance in it whatsoever.

This is the single most common way a track record gets flattered, and this
operation has already shipped one accounting bug of exactly this family — the
first scale-out credited only P&L and not proceeds, silently destroying the
capital that had been in the position. So the handling is stated explicitly and
enforced in code:

1. **`contributed_capital_usd`** is the return denominator, NOT `seed_usd` alone.
   Return = `equity − contributed_capital_usd`. It rises with every injection, so
   an injection produces **exactly zero** measured return.
2. **`capital_events[]`** records every injection with its UTC timestamp, amount
   and the directive that authorised it. The ledger can always be reconciled.
3. **The equity CSV carries the event on its row.** A reader of the raw series
   sees a labelled step, never an unexplained jump.
4. **Drawdown is measured from the post-injection peak.** The −30% kill therefore
   protects the LARGER book, which is the conservative direction.

**If any future report shows a ~20% gain dated 2026-08-28, it is this injection
mis-stated, and it is wrong.**

## What the $100,000 funds

**JDO.AX (Judo Capital Holdings, ASX)** — the single `undervalued` verdict from a
**3,665-name global below-tangible-book / rising-ROE screen**, and the first main-
book entry from Hunt 7.

- P/TB **0.653** (tangible book computed net of A$42.7m intangibles; 2.4% of equity)
- ROE rising **4.97 → 4.47 → 5.12 → 6.27%**, corroborated by revenue A$345m → A$509m
  and cost-to-income 52.4% → 45.3%
- **10.3rd percentile** of its own P/TB history (n=39 monthly, 3.2y)
- Catalyst: FY27 guidance reaffirmed **2026-08-18** (PBT A$210–220m, +25–31%;
  ROE "circa 8%"); AGM **2026-10-20**
- Gate measured **IN SESSION** (ASX REGULAR, 2026-08-28): spread 0.496%, ADV
  US$7.16m, position 0.11% of ADV, cap US$817m, no hazard flags
- **KILLS IT:** 90+DPD/impaired above 2.90% of GLA; another impairment revision
  like June 2026's; CET1 below the 11–12% range (fell 70bps to 12.4% while GLA
  grew 18%)

**Confidence: LOW**, and the search cost travels with it. The percentile window is
**~4 years, not 10** — the desk refused to extrapolate book backwards to fake a
longer series. It does not pass a strict 4-year monotone ROE test (FY24 dipped).

## Multiple-testing cost, updated and permanent

Hunt 7's below-book desk screened **3,665 names for ONE undervalued verdict**, on
top of the 4,306 already logged. **That is a very large search surface and it
raises the deflation bar for this name and every future claim from this dataset.**
A 1-in-3,665 survivor is exactly the shape most likely to be noise wearing a
story. It is funded at LOW confidence for that reason.

*Amendment sealed 2026-08-28. PAPER ONLY. The main book's original seal of
2026-08-27 and its 2027-02-25 review date stand unchanged.*

---

# AMENDMENT 2 — ARTEFACT HASH RECONCILIATION. Sealed 2026-08-28T04:10Z.

**Not funder-directed. Raised by the operator on noticing §7's hashes no longer
match the files on disk.** A sealed hash whose file has changed is worse than no
hash at all — it looks like a control and is not one. This reconciles the record.

## What §7 sealed, and what is on disk now

| artefact | status |
|---|---|
| `fundamental_borrow.py` | **INTACT** — matches the 2026-08-27 seal exactly |
| `fundamental_book.py` | **CHANGED** |
| `fundamental_exits.py` | **CHANGED** |

## THE ONLY QUESTION THAT MATTERS — verified, not assumed

**Every one of the eight frozen trading-rule constants in §3 was re-read from the
live module and compared against the registration. ALL EIGHT ARE UNALTERED:**

`MIN_NET_GAIN 0.05` · `ENTRY_COST_BPS 10` · `EXIT_COST_BPS 10` ·
`SLIPPAGE_BPS 5` · `ROUND_TRIP_BPS 30` ·
`LADDER (0.05,0.25)(0.10,0.25)(0.15,0.25)` · `RUNNER_FRACTION 0.25` ·
`RUNNER_GIVEBACK 0.05`

**The RULE did not change. The FILES did.** That distinction is the whole of this
amendment, and it is the reason the forward test still measures what was
registered.

## What actually changed, and when

**`fundamental_exits.py` — 2026-08-27 22:49, BEFORE this session.** Carries
CM-049, CM-050 and CM-051:
- **CM-049** — arithmetic corrections (rungs release a fraction of the ORIGINAL
  units; tolerance on a subtracted comparison; notional and released short
  collateral both scale).
- **CM-050** — borrow accounting (a released short tranche takes its SHARE of
  accrued borrow; `_entry_epoch` fallback; clock-advance ordering).
- **CM-051** — a per-position STOP, **SHORTS ONLY**, funder-delegated 2026-08-27,
  and a **DIRECTED CLOSE of the hard-tier short book**. This is what closed STLA,
  MARA, CLSK, KEEL, BKKT and CLF to zero units — the hard-borrow and
  borrow-doubtful names the desk could not honestly claim to hold.

**These are ADDITIVE and were separately recorded as CM decisions. None altered a
§3 constant.**

**`fundamental_book.py` — 2026-08-28, by the operator, this session.**
Two REPORTING flags that had gone stale and were being published as fact:
- `borrow_cost_modelled` was written `false` at book creation and never
  refreshed, so firm-wide coverage warned *"43 shorts with NO borrow cost
  modelled — the short book is flattered"* while borrow was in fact accruing
  daily and being subtracted INSIDE the 5% gate. A **false alarm**.
- `search_count` still read 326/59/8 after 22 desks had screened 4,306. A **false
  reassurance** — it understated the multiple-testing cost that travels with
  every claim.

**Neither touches sizing, the rule, the gate or the kill.** Correcting a field that
misreports the book is the opposite of tampering with it; leaving them would have
meant publishing two numbers known to be untrue.

## Re-sealed hashes (SHA-256), 2026-08-28

```
fundamental_book.py     4c16a815c669c9a8a2ebf81d0261f3cd8a83a0da2f8b88f6629343decbdd91fb
fundamental_exits.py    4c49f862ce5ec2a20bbca6c9c3f885955d1479d330577e82460b7b9beef17ef6
fundamental_borrow.py   2407646771e0a0950307a77906d4806c001446a3cf2d52def1e1575013c70144
```

**§7's original hashes stand as the 2026-08-27 record.** These supersede them from
2026-08-28 forward. Any future change to these files must be reconciled the same
way: state what changed, prove the §3 constants are untouched, re-seal.

*Sealed 2026-08-28. PAPER ONLY. The trading rule, the kill, the 182-day clock and
the 2027-02-25 review date are unchanged.*

---

# AMENDMENT 3 — CATALYST-EXPIRY EXIT ("SELL THE NEWS"). Sealed 2026-08-28T09:00Z.

**Funder-directed 2026-08-28: *"We need to trade the rumour and sell the news"* and
*"the rumour is what creates the volume and interest."* Sealed BEFORE it runs.**

## 1. What is added

A **FOURTH exit mechanism**, alongside the trading rule, the book kill and the
thesis break:

> **CATALYST EXPIRY.** When a position's pre-registered dated catalyst has passed
> and a grace period has elapsed, the position is **CLOSED IN FULL** unless it has
> been **RE-UNDERWRITTEN on a NEW dated catalyst**.

**Grace period: 5 TRADING DAYS after the catalyst date.** Rationale: an event may
report after the close, land intraday, or slip a session. Five days distinguishes
a slip from a failure without letting a resolved position drift.

## 2. Why — the evidence, gathered before this was written

Every desk that examined a post-event situation on 2026-08-28 found the move
already gone:
- **Index deletions:** *"the forced selling is over — funds sell INTO the effective
  date."* The window had closed **twelve weeks** earlier.
- **CCP.AX:** *"the 4 Aug −7.0% dislocation HAS ALREADY CLOSED."*
- **JDO.AX** had re-rated **+16.3%**, **EQT +21.5%**, both before being screened.
- **ENG.MC rose +13.92% ON THE DAY** the regulator's draft appeared.
- **Forced BUYING killed more candidates than forced selling created** — ADIG was
  spun and index-added **two days later**.

And the sharper risk, found independently by three desks: **the catalyst and the
damage can be the same event.** GL1.AX's FID commits construction capital beyond
its cash; 2517.HK's only dated forward event is an EGM to issue 5% new shares;
2197.HK's readout *"plausibly gets monetised through a discounted placing."*
**A position held THROUGH its catalyst can be right about the event and still lose
on the raise that follows it.**

## 3. THE NET GATE MUST NOT BLOCK IT — this is the load-bearing clause

**Catalyst expiry is a REASON-EXPIRED exit, not a PROFIT exit.** It is the same
class as the thesis break (`fundamental_close.py`), which already exits regardless
of P&L.

**§3's 5% net gate governs the PROFIT LADDER. It MUST NOT trap a position whose
reason for existing has expired.** A position may therefore be closed by this
mechanism **at a loss, or below the gate, and that is correct** — the alternative
is holding a thesis that has already resolved because the price has not yet paid.

**The firm-wide invariant is unchanged and reinforced: a frozen book must always
still be able to EXIT — and so must a RESOLVED one.**

## 4. Re-underwriting — the only way to keep a position past its date

A position may be retained ONLY by recording a **NEW dated, sourced catalyst** on
it, as a new thesis with its own `KILLS IT`. **Re-underwriting is an ENTRY-GRADE
decision and carries entry-grade evidence.** "It should keep going" is not a
catalyst; §4.4 already forbids it.

**A catalyst position whose catalyst has passed and which has not been
re-underwritten is a hold-and-hope. This desk does not hold and hope.**

## 5. What does NOT change

The trading rule's constants (§3), the borrow model (§3.1), the −30% book kill,
the thesis-break mechanism, the 182-day clock and the **2027-02-25 review date**
are all UNCHANGED. **No constant in §3 is touched.** This adds a mechanism; it
alters none.

## 6. The machine keeps the calendar; it does not judge the thesis

`fundamental_watch.py`'s constraint stands verbatim: *"This file never decides a
thesis has failed."* **A catalyst EXPIRING is a FACT — a date has passed, and a
machine may act on it. What HAPPENED at the event is a JUDGEMENT, and only a desk
reading the disclosure may rule on that.** This amendment automates only the fact.

## 7. Volume trend — recorded from this date

Per the funder's lead rule, the **10-day vs 3-month average volume ratio** is
measured on every candidate and every open position and recorded at entry.
**>1.15 building · 0.85–1.15 steady · <0.85 fading.** It is a RECORDED SIGNAL and
a required disclosure, **not** a gate — it must never override the value test,
because volume without undervaluation is momentum and this firm's record on
momentum is ~1,040 killed hypotheses with no validated edge.

*Sealed 2026-08-28. PAPER ONLY. The 2026-08-27 seal, the trading rule and the
2027-02-25 review date stand.*

## Amendment 3 — RE-SEALED ARTEFACT HASHES (SHA-256), 2026-08-28T09:20Z

Implementation complete and tested. **The eight §3 trading-rule constants were
re-verified UNALTERED after the change.**

```
fundamental_book.py       0f8e6b282ed5e0a5d04ee256d1d8f8c7bec87e926b024bddd1b048f97ad6829d
fundamental_exits.py      7ccce060364305acef778aa38eca1de92c6a1fa7a0a2ccfc1b48ecdb75aa1881
fundamental_liquidity.py  07d2748f2a63d335488a39a017830b900f180f4f4d261363f538fda1a958be44
```

**Tested before going live:** the 5% gate does NOT trap a resolved position (long
and short both close at a loss when the catalyst has passed); a FUTURE catalyst
correctly holds; an unparseable or absent date NEVER expires a position; and
equity is CONSERVED on the close — $88.00 of position became $86.46 of cash with
zero residual, the $1.54 difference being exactly the modelled exit cost.

**`catalyst_expired` was added to ALL THREE action allow-lists** — `apply()`'s
guard, its full-release branch, and `fundamental_book.py`'s cash-credit guard.
Omitting the third would have repeated CM-051 exactly: the position closes, the
credit is skipped, and the released collateral evaporates. That bug cost $17,079
once and the code comment warning about it is why it did not happen twice.
