# KILL THRESHOLD AMENDMENT — book-level kill tightened from −30% to −18%

**Status:** ACTIVE. **Dated 2026-08-28.** Append-only: nothing in any existing
registration has been edited. No entry, exit, sizing, cost or scoring rule of any
book is changed by this amendment. **PAPER ONLY throughout.**

**Supersedes:** the number `−30%` wherever it appears as the book-level kill —
`FIRM_RISK_AMENDMENT.md` §3, `TOURNAMENT_PREREGISTRATION.md` lines 33, 54, 68,
97, 154, 213, 233, 550, 611, and `book_kill.py`. Those documents are left as
written; this file is the amendment of record and `book_kill.py` is the single
place the number lives in code.

---

## 1. BASIS

Funder direction, 2026-08-28: *"reduce the kill to 18%"*.

The rule is otherwise unchanged, including the line that matters most:

> A book FREEZES when its equity is **18%** or more below its own peak equity.
> A FROZEN BOOK OPENS NO NEW POSITION.
> **A FROZEN BOOK STILL EXITS NORMALLY, ALWAYS, WITHOUT EXCEPTION.**

This is a **tightening**. It removes risk-taking capacity and adds none, so it
does not engage the standing rule against loosening a control.

---

## 2. THE HONESTY TEST: DOES ANYTHING FREEZE TODAY?

Changing a pre-registered threshold *after* 23 days of watching the results is
the exact move pre-registration exists to prevent. The test of whether an
amendment is opportunistic is whether it re-classifies anything already on the
board. Measured immediately before the change:

| Source | Deepest drawdown on the desk | Freezes at −18%? |
|---|---|---|
| `reports/book_peaks.json` (5 books) | Venue Replica **−3.22%** | No |
| `reports/tournament.json` (40 entries carrying a `dd_pct`) | `lowvol_crypto` **−4.04%** | No |
| Firm drawdown monitor (deepest listed) | Maker Calibration Probe **−4.21%** | No |

**Nothing freezes. Nothing changes state.** The deepest drawdown anywhere on the
desk is −4.21%, which is 13.8 percentage points clear of the new threshold. The
amendment is therefore purely forward-looking: it cannot retroactively kill a
book that has already lost money, and it cannot be read as picking a number that
flatters or punishes any result now on the board.

### The one number that looks close and is not

`replica:Mean-Reversion` shows **−18.67%**, and that is *P&L since inception*,
not drawdown. The kill measures a **floor's pooled equity against its own peak**,
and that desk sits inside the Venue Replica floor, whose drawdown is −3.22%. An
−18% kill does **not** reach it, and would not have at −30% either. See §4.

---

## 3. WHAT CHANGED IN CODE

One constant, in the one place the rule lives:

```
book_kill.py:  KILL_DD_PCT = -30.0   ->   KILL_DD_PCT = -18.0
```

Everything that enforces the kill imports it from there rather than copying it,
so this single edit moves the threshold for every book at once and none can
drift: the four paper floors (`kraken_floor`, `replica_floor`, `coinspot_floor`,
`cross_asset_floor`), the TFO Fundamental Desk and its spec sleeve, and every
tournament sleeve via `tournament.py` `mark()`. The `RULE` string, the banner,
`kill_events.jsonl` and the `book_risk` subtree all read the constant, so the
new number propagates into the ledgers and the audit log without further edits.

**Not changed by this amendment:**

- Exits. Every gate remains applied at the ENTRY site only.
- The firm-wide monitor's own levels: warn **−10%**, halt-new-entries **−20%**,
  never force-liquidate. See §4 for the interaction.
- Un-freezing, which stays a funder decision and never an automatic consequence
  of equity recovering.
- Peaks. No peak is re-seeded, re-based or back-filled by this change; a book's
  recorded history is exactly what it was before.

---

## 4. TWO INTERACTIONS THE FUNDER SHOULD KNOW

**(a) The book kill is now tighter than the firm halt.** Book-level freeze at
−18% now bites *before* the firm-wide halt-new-entries level of −20%. That is
coherent — a single book failing should stop that book long before the whole
firm is in trouble — and it is the ordering you would design deliberately. It is
recorded because it is a change in which control fires first, and that was not
true at −30%. If the funder wants the firm level moved in step, that is a
separate direction; it has not been assumed here.

**(b) The kill is per FLOOR, not per DESK.** A floor pools several desks and the
drawdown is measured on the pooled equity. Venue Replica's Mean-Reversion desk is
−18.67% since inception while the floor it lives in is −3.22% off its peak, so
the losing desk is masked by its two profitable siblings and no kill of any
threshold reaches it. Tightening 30 → 18 does not address this and does not
claim to. Per-desk kills would be a genuine rule change with real consequences
(three of eleven desks would come into scope immediately) and are **not** made
here — they need their own direction and their own registration.

---

## 5. AUDIT

- `reports/kill_events.jsonl` records every freeze with the threshold in force at
  the time (`kill_drawdown_pct`), so events either side of this amendment remain
  distinguishable forever.
- Each book's `book_risk` subtree carries `amendment`, `rule` and
  `kill_drawdown_pct`, refreshed every tick.
- Pre-change measurements in §2 were taken from the live artefacts at
  2026-08-28T20:15Z and are my own reading, not a quotation.
