# FIRM RISK AMENDMENT — book-level kill rules and the firm-wide drawdown monitor

**Status:** ACTIVE. **Dated 2026-08-27.** Append-only: nothing above any line in
any existing registration has been edited, and no entry, exit, sizing, cost or
scoring rule of any existing book has been changed by this amendment.

**Scope:** `kraken_floor.py`, `replica_floor.py`, `coinspot_floor.py`,
`cross_asset_floor.py` (book-level kill), and every book on the desk (firm-wide
monitor). **PAPER ONLY throughout.**

---

## 1. BASIS

Funder direction, 2026-08-27: *"GO FOR IT FINISH IT"* — complete the risk layer
following the blind-spot review of the desk.

**One honesty note on the basis, recorded because it matters more than it
flatters.** The direction referenced a blind-spot audit. The audit file on disk,
`reports/BLIND_SPOT_AUDIT.md` (compiled 2026-08-27 ~19:45), contains 15 findings
and **none of them is the risk-coverage gap, the missing kill rules, or the
absent firm drawdown**; a text search of it for "risk engine", "gather_positions",
"kill", "firm-wide" returns nothing on those subjects. Every gap and every number
in this amendment was therefore **re-derived from the live artefacts today** and
is stated as my own measurement, not as a quotation from that document. Where my
figure differs from a figure quoted in the direction, my measured figure is the
one recorded here.

### The three gaps, as measured 2026-08-27

| # | Gap | Measured |
|---|---|---|
| 1 | `risk_engine.gather_positions()` read **4 ledgers** — growth_book, value_book, kraken_floor, replica_floor — and published the result as the **"FIRM-WIDE RISK REPORT"**. | Those 4 held **40 positions / $177,286**. The firm actually holds **122 positions / $484,712** across **27 books**. The report covered **36.6%** of live exposure; **63.4% was unmeasured**, silently. |
| 2 | Floor positions were valued at **entry cost** (`val = sign * (p.get("cost") or 0.0)`) while a live `mark` sat in the same record. | On the two floors that report read: cost **$86,391** vs mark **$102,840** — **$16,449 (19.0%)** of misvaluation. Across all four floors: **$16,449 (18.1%)**. |
| 3 | Four paper floors holding **$247,110** of book equity across **11 desks** had **no book-level kill of any kind** — no peak, no drawdown, no freeze, nothing that could stop a book losing continuously. There was **no firm equity series, no firm peak, no firm drawdown and no aggregate trigger anywhere on the desk.** | Firm equity, first ever measurement: **$811,371** across 27 books. |

Gap 1 and gap 2 are **DEFECT FIXES** — code that did not do what its own
docstring said. They needed no rule change and got none. Gap 3 is a **RULE
CHANGE**, and this document is its registration.

---

## 2. DEFECT-FIX / RULE-CHANGE CLASSIFICATION, PER BOOK

The test applied: *does this book's own registration already promise a kill?*
Read: `TOURNAMENT_PREREGISTRATION.md`, `KrakenQuantDesk/HANDOVER.md`,
`KrakenQuantDesk/reports/COINSPOT_FLOOR_PREREGISTRATION.md`,
`LIQUIDITY_SCREEN_AMENDMENT.md`, `README.md`.

| Book | Kill promised in its registration? | Therefore |
|---|---|---|
| Tournament sleeves (18) | **Yes** — "Sleeve kill: −30% drawdown freezes a sleeve (state preserved, reported)". Implemented since 2026-08-06 in `tournament.py` `mark()`. | Already compliant. **Untouched by this amendment.** |
| `kraken_floor.py` | **No.** HANDOVER §8 and the liquidity amendment cover entries and costs only; the "kill-switch / daily-loss limit" line in HANDOVER §1 refers to the **live engine** (`kraken_paper_bot.py`), not this floor. | **RULE CHANGE**, funder-directed 2026-08-27. |
| `replica_floor.py` | **No.** | **RULE CHANGE**, funder-directed 2026-08-27. |
| `cross_asset_floor.py` | **No.** *Found during this work — it was not in the direction's list of three, and leaving it out would have rebuilt the exact defect being fixed on a fourth book.* | **RULE CHANGE**, funder-directed 2026-08-27, **flagged here for the funder as an addition to the named scope.** |
| `coinspot_floor.py` | **Yes, and it was never implemented.** `COINSPOT_FLOOR_PREREGISTRATION.md` §"Kill criteria" promises: 30-day P&L below −10% → halt entries; a round trip whose realised cost drag is below the measured spread → all numbers void; tradeable universe empty 5 consecutive ticks → stop. **None of the three existed in code.** | The −30% rule added below is a **RULE CHANGE**. It **does not discharge** the book's own three criteria, which remain **OUTSTANDING** — see §7. |

---

## 3. THE BOOK-LEVEL KILL RULE (frozen)

Implemented once, in `book_kill.py`, and **imported** by all four floors — never
copied — so the floors and the tournament can never drift on what "killed"
means.

> **A book FREEZES when its equity is 30% or more below its own peak equity.**
> **A FROZEN BOOK OPENS NO NEW POSITION.**
> **A FROZEN BOOK STILL EXITS NORMALLY, ALWAYS, WITHOUT EXCEPTION.**

- **Threshold:** −30.0% (`book_kill.KILL_DD_PCT`) — deliberately the *same
  number* the tournament sleeves have used since 2026-08-06
  (`tournament.py:124-126`), so the whole firm is judged on one rule.
- **Equity, per book:** cash + Σ entry cost + marked unrealised, summed across
  that book's isolated desks. CoinSpot marks at the **bid** and the cross-asset
  floor at the **exitable side** — the price the position could actually be
  closed at — because a kill rule fed by unrealisable spread is a kill rule that
  never fires.
- **Peak:** tracked per book, never lowered. Stored in a **sidecar**
  (`reports/book_peaks.json`) *and* mirrored into the book's own ledger under
  `book_risk`. The sidecar exists because two of the four floors run as
  long-lived `--loop` daemons that rewrite their entire ledger from an in-memory
  dict every hour; a peak kept only in the ledger would be erased on every such
  write, the drawdown would reset to ~0 forever, and the kill could never fire.
  Where the two copies disagree the **higher** peak wins, so a lost file can only
  forget history — never shrink a drawdown already recorded.
- **Peak seeding, honestly:** `replica_floor` seeds from `replica_equity.csv`, a
  real recorded series running since 2026-08-06. The other three floors have no
  equity history on disk, so their peak **starts at the equity observed on first
  run** and `peak_source` says exactly that. Their drawdown is measured from
  2026-08-27 forward. Back-filling a peak nobody recorded would manufacture a
  drawdown that never happened.
- **Un-freezing is a funder decision.** A frozen book stays frozen even if its
  equity recovers. Nothing in the code un-freezes a book.
- **Loud and machine-visible:** a banner on stdout, `frozen` /`frozen_reason` /
  `frozen_at_utc` in the ledger and the sidecar, an append to
  `reports/kill_events.jsonl` (which survives the ledger being overwritten), a
  `BOOK RISK` line in every floor's `--status`, and a row in
  `reports/firm_risk_state.json`.
- **Fail-closed on entries, never on exits:** if `book_kill` cannot be imported
  or a book cannot value itself, **NEW entries are refused** and the reason is
  printed — matching the liquidity screen's existing convention. Exits are
  unaffected in every failure mode, because the gate is applied **only at the
  entry site**, after every exit for that desk has already run.

### Why exits are never blocked

A kill that blocks exits does not reduce risk. It traps the book in exactly the
positions that caused the drawdown and removes the only mechanism that can end
it. Every gate added by this amendment sits below the exit loop and above the
entry loop; no function in `book_kill.py` writes a position, a unit count or a
cash balance.

---

## 4. THE FIRM-WIDE DRAWDOWN MONITOR (`firm_risk.py`)

Runs every tick (`desk_tick.sh`, immediately after `risk_engine.py` and **before
the floors**, so they read a fresh flag).

- **Firm equity** = the sum of every book's own marked equity, via
  `book_coverage.py` — the same coverage `risk_engine` uses, so the two can never
  disagree about what the firm holds.
- **Series:** appended to `reports/firm_equity.csv` every tick, carrying the
  headline total, the live-only total, the stale total, the peak, the drawdown
  and the level — so either series can be rebuilt from the file.
- **Peak:** first observation is 2026-08-27; `peak_source` records that. Firm
  drawdown is measured from that date forward, for the same reason as above.
- **State:** `reports/firm_risk_state.json`, read by every book before opening.
  A missing or >6h-stale state is reported **UNAVAILABLE / STALE** and does
  **not** halt: a firm-level halt is a firm-level judgement and must never fire
  because a file was late. It is printed loudly so "unavailable" can never read
  as "clear".

### The two levels (frozen)

| Level | Trigger | What happens | What does NOT happen |
|---|---|---|---|
| **WARN** | firm drawdown ≤ **−10%** | Loud banner on the tick, amber banner on the desk report, `level: "WARN"` in the payload. | **Nothing is blocked.** No entry is refused, no position is touched. |
| **HALT NEW ENTRIES** | firm drawdown ≤ **−20%** | `new_entries_halted: true`. Every book checks it before **OPENING** a position; refusals are printed, recorded in the ledger and appended to `kill_events.jsonl`. Red banner. | **No position is closed. No book is liquidated. Exits run exactly as before.** |

### Capital events are flagged, never suppressed

Firm equity is a sum over books, so **adding** a funded book raises it and
**removing** one lowers it — neither is performance. The removal direction is the
dangerous one: it would print as a firm drawdown and could trip the halt on a
bookkeeping change. Every observation therefore carries a `capital_event` flag
(and the names and dollar amount of what was added or removed), in the state
file, in the CSV and on the tick. It is flagged and **not** suppressed, because
suppressing a step would also hide a real loss that happened to coincide with
one.

This is not hypothetical: it was **observed live while this amendment was being
written**, when another session appended a new sleeve to the tournament and firm
equity stepped from **$811,362 to $849,966 (+$38,604)** between two consecutive
observations. The coverage glob picked the new sleeve up automatically — which is
the design working — and the step is bookkeeping, not a gain.

### *** THE NEVER-FORCE-LIQUIDATE PRINCIPLE ***

**This monitor never force-liquidates. Not at −10%. Not at −20%. Not at any
level. There is no threshold at which it sells.**

The reasoning, recorded so a future session cannot "improve" it away:

1. **It would destroy 27 track records at once.** Every book here is a
   pre-registered forward test whose entire value is that its rules were fixed in
   advance and its result was allowed to happen. Auto-closing them on a firm
   drawdown trigger would end every one of those experiments simultaneously, and
   the evidence they were built to produce would be permanently unavailable.
2. **It converts the experiment into discretion, at the worst moment.** A
   drawdown trigger fires precisely when markets are dislocated. Liquidating
   there substitutes one large, unregistered, undiversified timing decision for
   twenty-seven registered ones — and the desk's own record (~1,040+ hypotheses
   killed) is that its timing decisions do not beat its rules.
3. **It is unnecessary to stop the bleeding.** Halting new entries stops new risk
   immediately. Existing positions continue to exit on the rules each book
   already registered — rules that were written when nobody was panicking.
4. **Closing is a funder decision.** It requires the whole picture, including
   things no monitor can see. The monitor's job is to make the number impossible
   to miss; the decision stays with a human. `force_liquidate: false` is written
   into every published state file, and `NEVER_LIQUIDATE = True` is a module
   constant so a grep finds it.

---

## 5. WHAT CHANGED IN CODE

| File | Change |
|---|---|
| `book_coverage.py` | **NEW.** The single enumeration of every book with live capital. Tournament sleeves are **globbed**, so a new sleeve is covered the moment it writes a ledger — coverage cannot silently fall behind again. Read-only; writes nothing. |
| `book_kill.py` | **NEW.** The book-level kill rule, once, imported by all four floors. |
| `firm_risk.py` | **NEW.** Firm equity / peak / drawdown, the equity series, and the two triggers. |
| `risk_engine.py` | Coverage rebuilt onto `book_coverage`; positions valued at **mark**, cost only as a declared and totalled fallback; positions with no stored mark **re-marked from the price panel the report already downloads**; the **COVID and 2022 stress replays the docstring had always promised are now actually run**, on a separate long-history panel, with `covered_pct` published for each. No threshold moved. |
| `kraken_floor.py`, `replica_floor.py`, `coinspot_floor.py`, `cross_asset_floor.py` | Kill-rule import (fail-closed on entries), `floor_equity()`, `kill_check()`, `kill_entries_allowed()`, one gate above each entry loop, a `BOOK RISK` line in `--status`. **Not one byte of any entry-signal, exit-signal, sizing or cost function was changed.** |
| `desk_tick.sh` | One appended step: `run firm_risk.py`, positioned after `risk_engine.py` and before the floors. |
| `make_desk_report.py`, `command_server.py` | Firm drawdown banner on the desk report; `firm_risk` added to the dashboard payload. Display only. |

---

## 6. VERIFICATION PERFORMED (2026-08-27)

- `ast.parse` clean on all eight edited/new files.
- **Coverage:** 4 ledgers / 40 positions / $177,286 → **27 books / 122 positions
  / $484,712**. Published `gross_book_usd` (netted by ticker) $156,671 →
  **$422,560**.
- **Mark-vs-cost:** the two floors the old report read moved **+$16,449
  (+19.0%)**; whole firm including the panel re-mark **+$29,670 (+6.12%)**; 121
  of 122 positions re-marked, **$9,976 still at cost** and named
  (`capacity_probe:PF_SPCXXUSD`, no price series anywhere).
- **Kill reachability**, on scratch copies of all four floors, live ledgers
  untouched: peak forced so equity sits at **−31%**, then a real tick against a
  synthetic crash feed. All four **froze**, all four **refused every new entry**,
  and all four **still executed exits** (kraken 15 exits 17→2 positions; replica
  10 exits 11→1; coinspot 5 exits 5→0; cross-asset 3 exits 5→2).
- **Firm triggers**, on scratch state: forced −5.0% → `OK`; −10.5% → `WARN`,
  nothing blocked; −21.0% → `HALT_NEW_ENTRIES`, `new_entries_halted: true`,
  `force_liquidate: false`. Under the halt a healthy, unfrozen book refused 3
  entries, opened **0**, and still executed **15 exits**.
- **Integrity:** holdings / positions / cash on every live book byte-identical
  across the measurement layer.

---

## 7. KNOWN LIMITATIONS — recorded, not buried

1. **CoinSpot's own three kill criteria remain unimplemented.** The −30% rule
   added here is a different rule. The registered −10%/30-day halt, the
   cost-drag voiding criterion and the empty-universe stop are still absent from
   the code, and this amendment does not pretend otherwise.
2. **Two floors run as `--loop` daemons** (`coinspot_floor.py --loop 3600`,
   `cross_asset_floor.py --loop 3600`) started before this amendment. They hold
   the pre-amendment code in memory, so **the entry gate is not enforced in those
   two running processes** until they are next started. The rule is live for
   every fresh invocation (including `desk_tick`, `--status`, and the next daemon
   start), and the sidecar peak store means their drawdown history is tracked
   correctly in the meantime. **No process was restarted to fix this** — that is
   the funder's call.
3. **Three of four floor peaks, and the firm peak, begin today.** Drawdown for
   them is measured from 2026-08-27 forward. Stated, not hidden.
4. **The OANDA book's positions are UNREADABLE.** `oanda_status.txt` is empty (a
   sustained 401) and its ledger's "close" events are a known no-op, so its
   positions contribute **zero measured exposure** and its equity is carried as
   an explicitly **STALE** ledger NAV ($37,548), reported separately in every
   payload. Guessing positions from a ledger known to lie would be fabrication.
5. **The firm drawdown is contaminated by capital events until they are
   netted out.** They are flagged per observation (above), but the series does
   not yet compute a capital-adjusted return. Reading the flag is required before
   acting on any large single-step move.
6. **`command_server.py` is a running process.** The payload change takes effect
   when it is next restarted; it was not restarted here.

---

## 8. SIGN-OFF

Basis: funder direction 2026-08-27, *"GO FOR IT FINISH IT"*, following the
blind-spot review. Rules above are **FROZEN** from this date and carry their own
clock. They change no entry, exit, sizing, cost or scoring rule of any existing
book, and they retroactively change nothing any book recorded before today.

**APPROVED — funder-directed 2026-08-27. Book-level kill at −30% from own peak
(entries only, exits never blocked); firm WARN at −10%; firm HALT-NEW-ENTRIES at
−20%; NEVER force-liquidate at any level.**

Implementation: `book_coverage.py`, `book_kill.py`, `firm_risk.py`.
Evidence: `reports/firm_equity.csv`, `reports/firm_risk_state.json`,
`reports/book_peaks.json`, `reports/kill_events.jsonl`,
`reports/risk_report.json`.

---

## Post-review corrections — CM-044/045/046 (2026-08-27, append-only)

The risk layer built earlier today was reported COMPLETE AND VERIFIED. An
independent hostile review returned **FAIL**. The review was right; this section
records what was wrong and what was done, because a risk layer that overstates
its own coverage is worse than none — it converts an unmeasured book into a
green light.

### CM-044 — "firm-wide" was not firm-wide

Three books holding live capital were absent from the registry entirely:

| book | equity | open pos | why it is live |
|---|---|---|---|
| kraken_paper_bot (the LIVE engine) | $40,274 | 1 (SOL/USD) | pid running; the firm's own `cross_desk_allocator.py:44` carries it as **BOOKS[0]** |
| maker_calibration_probe | $9,742 | 1 short | runs on **every** `desk_tick` |
| kraken_meanrev_desk | $39,996 | 1 (PUMP/USD short) | dormant since 2026-08-05 but still holds the short |

**$90,012 — 9.6% of firm capital — was invisible to the monitor that gates the
halt.** Gross-exposure error was small (the engine is 99.6% cash), but firm
equity, peak and drawdown were wrong by construction, and those are the series
the trigger reads.

Fixed: three new loaders (`_load_engine_bot`, `_load_maker_probe`,
`_load_dormant_desk`). The engine prefers its LIVE HTTP read (:18799/state) and
falls back to the ledger; if BOTH fail the book is marked **unreadable, never
zero** — a zero there silently shrinks firm equity and manufactures a drawdown.
The dormant desk is registered and flagged DORMANT rather than hidden: **retiring
a $40k book with an open short is a FUNDER decision, not the trader's.**

**Structural fix so it cannot recur:** `coverage_audit()` walks both desk trees
for ledger-shaped JSON and loudly names any file no registered book claims. It
deliberately cannot auto-register (that would guess a shape and risk
double-counting). Verified live by planting a canary ledger — it was detected,
then removed. Filters exclude `archive_*`/staging dirs, report artefacts and
files untouched for >7 days: the first version produced **173 false positives**,
and an audit that cries wolf is an audit that gets ignored — the same failure
mode this exists to prevent.

### CM-045 — the monitor's own failure modes

1. **Never "OK" on a partial read.** Previously, total coverage failure published
   equity $0, drawdown 0.00%, **level OK** — the most dangerous line this file
   could print. New level `UNMEASURED`, which is never OK. Verified by simulating
   5 of 31 books failing.
2. **A partial read can no longer set the firm peak.** The cold-start path was
   still exposed after the first fix — an incomplete equity would have been
   sealed in as the peak permanently, and since a peak is never revised downward
   that would understate *every future drawdown for the life of the series*.
   Peak is now left `None` and the level is UNMEASURED. Verified.
3. **Drawdown is no longer computed across different book sets.** The peak is now
   **shifted** by the bookkeeping delta (added minus removed equity) rather than
   reset — resetting would erase a real drawdown whenever a book was added, and
   doing nothing made a removal print as a loss the firm never took (the reviewer
   measured 5 unreadable books printing **−23.53%** and tripping
   HALT_NEW_ENTRIES on nothing but a failed read). Observed working: adding the
   three CM-044 books shifted the peak **+$90,012.49** and the drawdown stayed
   −0.04% instead of booking a phantom gain. `book_equity_usd` is now persisted
   per key — it is the only record of what a removed book was worth.
4. **`firm_equity.csv` header repaired** (10 fields on disk vs 11 written;
   `capital_event` was added after the file existed and `append_csv` only wrote a
   header for a new file). Any DictReader/pandas parse was mis-assigning columns.
   `append_csv` now self-heals a field-count mismatch, preserving existing rows.
5. **Docstring corrected.** It claimed "every book checks it before OPENING a
   position." Only the **four desk floors** call `entries_allowed`. The other 27
   books publish into the firm series but do **not** consult the halt flag.
   *This remains an OUTSTANDING GAP, recorded here rather than papered over.*
6. **Audit log cleaned and bounded.** The builder's own `TEST`/`TEST2` rows had
   been written into the LIVE `kill_events.jsonl`; they were the file's *only*
   contents. Moved to `kill_events_scratch.jsonl` (deleted nothing) and rotation
   added at 2MB × 5 generations — a frozen book appends one refusal per desk per
   tick with no bound.

### CM-046 — the kill was on disk but NOT in force; mark-blind equity

`coinspot_floor` (pid 89647, up since Aug 23) and `cross_asset_floor` (pid 1118,
up since Aug 21) were executing **pre-kill code loaded into memory**: both
ledgers showed `book_risk: null` and neither book appeared in `book_peaks.json`.
On ~$13.6k the −30% freeze could not fire. Neither process is the protected
Kraken engine, its supervisor, or launchd-managed, and both persist atomically
each tick — so they were restarted.

**Non-disturbance proof:** position hashes byte-identical across the restart
(coinspot `fa56822090635ef3`, cross-asset `e9c391816db5a6aa`), 5 positions and
cash unchanged in both. After restart both carry `book_risk`, and **all four
floors now appear in `book_peaks.json`.** The two board processes were also
restarted so the firm-risk panel is served (:18811 → 200; :18812 → 200 on the
tailnet, correctly refusing 127.0.0.1).

Separately, those two floors store no per-position `mark`, so they had been
feeding **mark-blind** (cash + Σcost) equity into the firm number — firm drawdown
could not see unrealised losses there. Now that each floor stamps a marked equity
into its own `book_risk`, the registry prefers it **when fresh (≤6h)**, and keeps
the cost basis with a loud warning when not. Building this surfaced a live
instance of the known WITA-clock trap: `time.mktime` read the UTC stamp as local
time and reported a fresh value as **−58 minutes old**; a negative age passed the
staleness check by accident and would have passed a genuinely stale value too.
Corrected to `calendar.timegm`.

### Honest status after these fixes

The reviewer's two hardest findings stand as PASSED and are unchanged: valuation
is mark-not-cost and reconciles to the cent, and the kill provably freezes
entries while **never** blocking an exit or closing a position — no path to a
forced liquidation was found. Coverage is now 31 books / 133 positions with a
self-audit behind it. **Outstanding and not fixed:** the halt flag is consulted
by the four floors only (item 5 above); `coinspot_floor`'s previously-promised
−10%/30-day halt was never implemented and the −30% rule does **not** discharge
it; and `tour_capacity_probe:PF_SPCXXUSD` ($9,976) has no price series anywhere
and is still carried at cost.

### CM-047 — the halt flag is now consulted firm-wide (2026-08-27, append-only)

CM-045 item 5 recorded an outstanding gap: `HALT_NEW_ENTRIES` was wired into the
**four desk floors only**. The other 27 books — every tournament sleeve,
growth_book, value_book, the carry desk — published into the firm equity series
but never consulted the flag. A firm-wide halt would have stopped roughly a third
of firm capital and left the rest opening new positions into the drawdown that
triggered it. That gap is now closed.

**Where the gate sits.** At the entry sites, never on the exit path:

| site | books covered | mechanism |
|---|---|---|
| `tournament.set_holdings()` | all weight-based sleeves | target clamped to ≤ current holding |
| 5 direct `holdings[sym] = units` opens | rally_rider, rally_rider_hold, dip_rider, rally_rider_x, capacity_probe | `_firm_halt_blocks_entry()` → skip |
| covered_call underlying purchase | covered_call_etf | skip with a stated reason |
| `growth_book.rebalance()` / `value_book` | both books | `_firm_halt_blocks()` clamp |
| `carry_paper_desk` entry rule | carry book | `ENTRY_REFUSED_FIRM_HALT` ledger event |

**Semantics — the halt can never close a position.** A target may not EXCEED
what is already held. New names refused (have = 0), add-tos refused, **reductions
and full exits pass untouched** — an exit simply omits the ticker from `targets`,
which the clamp never inspects. Freed capital falls to cash.

**Fails OPEN, deliberately.** If the gate raises, the state file is missing, or
the state is stale, trading is NOT halted — a firm-level halt is a firm-level
judgement and must never fire because a file was late. Every such case prints
loudly so "unavailable" can never read as "clear". Result cached 60s so the state
is not re-read and re-printed 14 times per tick.

**Verified, not asserted:**
- Clamp semantics on `set_holdings`, growth_book and value_book — new name
  REFUSED, add-to REFUSED, reduction ALLOWED, full exit ALLOWED (2 holdings → 0).
- Control with no halt: a new name opens normally.
- Fail-open: with the gate raising, entries still pass in both code paths.
- Real state file drives it end-to-end (cache bypassed): HALT → halted; OK,
  STALE (2 days), UNMEASURED-not-halted, and MISSING file → all correctly NOT
  halted.
- **Non-disturbance:** a live `tournament.py` tick with no halt active changed
  holdings/cash on **0 of 21 books** and printed no HALT-REFUSED or ERROR line.
  The gate is inert until it is needed.

Refusals are recorded on the book itself (`halt_refusals`, last 50) so a halt
leaves an audit trail rather than a silent absence of trades.

**Still outstanding after this:** `coinspot_floor`'s promised −10%/30-day halt is
still unimplemented and the −30% rule does not discharge it; the Kraken engine
(`kraken_paper_bot.py`) is PROTECTED and was not modified, so it is covered by
the firm series but does not consult the halt — changing that needs explicit
funder authorisation; `tour_capacity_probe:PF_SPCXXUSD` ($9,976) still has no
price series and is carried at cost.

### CM-048 — the coverage audit earned its keep on its first live run

`coverage_audit()` was wired into every `firm_risk` tick (it had been written but
never CALLED — an audit that is never called is not a guard, it is a comment).
On its first live run it flagged one unclaimed ledger:

    UNCLAIMED ./reports/fundamental_book.json   cash~77,894.68  entries=53

That is the **TFO Fundamental Desk — a $500,000 funder-directed global long/short
book** (registration `reports/research_global_value_screen_2026-08-27.md`), opened
2026-08-27 by a parallel research run. It is **larger than every other book in the
firm combined**, and the firm risk monitor could not see it. The hostile review
that produced CM-044/045/046 did not catch it either — it did not exist yet, which
is precisely the case a static list can never cover and a walking audit can.

Registering it surfaced two further defects, both of the same family this whole
amendment is about — **a missing number silently becoming a zero**:

1. **Concurrent-edit collision.** The desk that built the book had added its own
   `_load_fundamental` to `book_coverage.py` while I was adding mine. Python
   binds the LAST definition, so *mine* was live — and mine was written from an
   older snapshot and would have applied an FX conversion to positions the book
   already publishes in USD, double-counting every currency move. Theirs is
   authoritative and was kept; mine was removed; only its absent-file handling
   was merged in.
2. **A silent zero on a $500k book.** `float(d.get("equity") or 0.0)` turned "not
   computed yet" into "$0". Observed live at 12:14:49Z mid-rebuild: 53 positions
   open, no `equity` field, book reported at **$0**. That understates firm equity
   by the whole book — and worse, because a book already in `book_keys` is not
   treated as a capital event when its value later appears, the correction would
   have printed as a **+$500,000 PERFORMANCE gain** on the firm series. Missing
   equity is now an ERROR (book UNMEASURED, never zero), which also means the
   book only enters `book_keys` once it has a real value, so its arrival is
   correctly booked as a capital event. Confirmed: `added ['fundamental_book']
   (+$500,004.68) — bookkeeping, not performance`, peak shifted, drawdown
   unmoved.

Also: an "absent" ledger is now distinguished from an "unreadable" one. The book
was quarantined mid-session by its own desk as `fundamental_book.MISSIZED-do-not-use.json`
after a sizing defect, and during that window registering a non-existent path
would have pinned the entire firm at UNMEASURED. Absent → excluded with a loud
notice, picked up automatically on arrival. Quarantine markers
(`do-not-use`, `missized`, `.quarantine`, `.broken`) are excluded from the audit —
a ledger someone deliberately renamed is a DECISION, not an uncovered book.

**Carried forward for the funder, not fixed by me** (this book belongs to another
desk's registration): it holds **46 shorts with `borrow_cost_modelled: false`**, so
the short book's P&L is flattered by an unmeasured carry cost; **2 positions are
HELD, not opened**; and its own registration states plainly that it has run **no
backtest, no deflation, no CV and none of the three kill-tests**, and that 51 of
its positions are naked shorts against the firm's own 14-year finding that the
same signal expressed as CASH beat SHORT by ~15pp/yr. Firm equity is now
**$1,440,334** — this one book is 35% of it.

### CM-057 — the last six unscoreable books, baselined (2026-08-30, append-only)

Six books holding **$148,348 — ~10% of firm equity — had no recorded start**, so
their P&L was not merely wrong, it was **unstateable**. The firm total was being
reported over a partial set and quietly excluded them.

Baselines recovered from AUTHORITATIVE sources, never inferred from equity —
inference would be circular, handing a losing book a smaller "baseline" so it
appears flat:

| book | start | source |
|---|---|---|
| lowvol_crypto, fng_momentum, equal_weight_all, spy_trend_cash | $10,000 each | `tournament.py START = 10000.0`, no `SLEEVE_START` override |
| Growth Book | $50,000 | `GROWTH_BOOK_PREREGISTRATION.md` §5 — "Start: $50,000" |
| Value Book | $50,000 | `VALUE_BOOK_PREREGISTRATION.md` §4 — "Start $50,000" |
| Kraken Floor / Venue Replica | $120,000 each | `START_CASH 40000.0 x 3 desks` in each floor module |

They lacked the field because the four sleeves **predate** the `start` key that
`tournament.load_sleeve` now writes, and the two weights books never carried one.

**The ledgers were NOT mutated.** They are registered artefacts; a baseline the
aggregator needs belongs in the aggregator. `DOCUMENTED_START_USD` fills
`start_usd` ONLY where the ledger supplied none — a book that states its own start
always wins — and every filled value carries a `start_source` string naming its
provenance.

**The firm can now be scored in full for the first time: 32 of 32 books, 0
unscoreable.**

    FIRM TOTAL   start $1,537,209  ->  equity $1,530,307  =  -$6,901.46  (-0.45%)
    ex-punt (the $10,000 written off by design)          =  +$3,098.54  (+0.20%)

This REVISES the earlier −$15,249 (−1.09%) figure, which was computed over 26
books while the other six sat outside the total. The revision is favourable, and
that is worth flagging precisely because it is: the four sleeves that had no
baseline include the firm's two best percentage performers (**lowvol_crypto
+14.10%, fng_momentum +11.97%**) and Growth Book at **+12.46%**. **An incomplete
total was understating the firm — the opposite of the flattery this desk usually
has to strip out — and it was wrong for the same reason either direction is wrong.**

### CM-058 — the Kraken Floor was already maker, at a rate it does not hold (2026-08-30)

**Funder-directed: "apply the maker fix to the kraken floors as well." The
investigation found the opposite of what the instruction assumed, so the
correction runs AGAINST the book rather than for it.**

**`kraken_floor.py` was never a taker book.** Its header has always said "all
maker-fee economics" and line 65 read `MAKER, SLIP = 0.0010, 0.0005` — **10 bps
maker**. Measured live from Kraken `AssetPairs` 2026-08-30, the base maker rate is
**25 bps**; 10 bps requires **$250,000/30-day** volume as a maker (or $10m as a
taker), and this floor has traded **$40,281** in its life. It does not hold the
tier it was charging itself.

**This is the CM-042 error in a second book** — assuming a fee tier we do not
have. There was no cheaper execution to switch to here; there was an over-cheap
assumption to correct.

**Corrected `MAKER 0.0010 → 0.0025`.** Fees on traded notional to date rise from
**$40.28 to $100.70**, and the floor's equity fell $115,314.64 → $115,270.13 on
the first tick under the new rate. Positions are byte-identical across the change
(units hash `282558442675`) — this is a cost correction, not a trading change.

**Still optimistic, and recorded as such:** the floor assumes every maker order
FILLS. It models no queue, no misses, no adverse selection. `maker_fill_probe.py`
is measuring precisely that, and its reading may force a further correction in the
same direction.

### `replica_floor.py` deliberately NOT changed

The replica exists to answer *"what would these strategies do at ANOTHER venue's
costs?"* — it runs `venue_costs` with `ACTIVE_VENUE = "robinhood"` (crypto
spread 0.35%, crypto_alt 1.50%, zero commission), against the Kraken floor as the
control. Everything else is deliberately identical so **any difference in outcome
is attributable to venue rather than strategy**.

Applying Kraken maker fees to it would destroy the only comparison the book
exists to make. Its costs are not wrong; they are a different venue on purpose.

### The discontinuity, stated so nobody trips on it later

Unlike the Crypto Rumour-Rider — which was amended at **zero** closed trades — the
Kraken Floor has **~60 closed trades** priced at the old 10 bps. **Its record now
contains a cost-model change dated 2026-08-30.** Any future analysis of this
floor's series must split it at that date rather than treat it as one sample. The
change was funder-directed and is registered here before it ran.

### CM-059 — Mean-Reversion halted: its edge does not survive its own fee (2026-08-30)

**Funder-directed: "fix the kraken floors."** The floors had no bug to fix. They
had an **edge claim invalidated by the fee correction of CM-058**.

Every desk on this floor was validated on **10 bps maker economics**. Correcting
`MAKER` to the measured 25 bps costs **30 bps a round trip**. A desk whose
out-of-sample edge is smaller than 30 bps therefore has **negative expected edge
at the fee we actually pay**:

| desk | OOS @10 bps | fee correction | edge @25 bps | live result |
|---|---|---|---|---|
| **Mean-Reversion** | **+14 bp** | −30 bp | **−16 bp** | **−$5,207** (−$185.50 × 30 trades, win rate 23.3%) |
| Breakout | +94 bp | −30 bp | +64 bp | +$1,484 |
| Micro-Cap Momentum | +94 bp | −30 bp | +64 bp | −$798 |

The live record is not marginally worse than the claim — Mean-Reversion has
realised **−$185.50 per closed trade against a claimed +14 bp edge**, in the
opposite direction, over 30 trades.

**Action: `EDGE_HALTED_DESKS` refuses NEW ENTRIES on Mean-Reversion only.**
Exits, stops and marking are untouched — a halted desk still manages what it
holds, and **nothing was force-liquidated**. Verified: positions byte-identical
across the change (2 / 7 / 4 open, unchanged), `edge_halts` recorded in the
ledger, and the halt prints loudly on every tick.

**The general rule this establishes, worth applying firm-wide:** a strategy
validated at one fee tier is not validated at a worse one. When a cost model is
corrected, **every edge claim resting on the old model must be re-checked against
the new one** — and any whose margin was thinner than the correction should stop
trading, not be quietly carried. Breakout and Micro-Cap survive that test at
+64 bp; Mean-Reversion does not.

**Not claimed:** this does not make the floor profitable. It stops one desk adding
to a loss it has no measured reason to expect to recover.

### CM-067 — SOL/USD and UNI/USD replica shorts: NOT closed, and why (2026-09-02)

An auditor flagged both as INVALID (a short at a venue modelled with max_leverage
1.0). Three adversarial verifiers split **2 REFUTED / 1 CONFIRMED**. The refuters'
argument is decisive and it is one this desk should have applied to itself:

**The rule the auditor invoked — CM-066's venue short gate — was a code comment I
wrote at 18:23 on 2026-09-02, three hours AFTER UNI/USD opened at 15:22.** A position
cannot be retroactively invalidated by a rule written after it. The replica floor
has no frozen preregistration; its own design statement lists "the venue's
treatment of shorts — financing accrues daily where it applies" as one of exactly
three DELIBERATE venue swaps. A modelled short with financing is what the book was
built to carry.

**Decision: both stay open and exit on their registered rules** (stop 8.0%, z_exit
0.5, max_hold 15d). **CM-066 governs FUTURE entries only** — which is the only thing
it could honestly govern. I was about to close both; the verifiers stopped me.
That is the adversarial layer paying for itself.
