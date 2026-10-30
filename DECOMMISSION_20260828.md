# DECOMMISSION — OANDA Book and Kraken Mean-Reversion Desk

**Status:** ACTIVE. **Dated 2026-08-28.** Append-only: no existing registration
has been edited. **PAPER ONLY throughout.**

**Basis:** funder direction, 2026-08-28: *"remove both desks entirely"*, following
the report that named exactly these two as the outstanding items needing a human.

---

## 1. WHAT "REMOVED" MEANS HERE

Both books are **out of the firm**: deregistered from `book_coverage.registry()`,
so neither contributes to firm equity, firm drawdown, the risk report, the
tournament board or any dashboard; and the tick no longer runs, reads or reports
either one.

**Their records were archived, not deleted** — `backups/decommissioned_20260828/`
holds both ledgers, the Kraken desk's code, the OANDA ledger and the CLAUDE book
pre-registration. Reader code is gated behind a flag rather than ripped out,
because for the OANDA book it is the only surviving description of what the
practice account held at the moment we stopped looking.

Deleting the underlying data is a separate decision and has **not** been taken.
See §5 for exactly what is still on disk.

---

## 2. KRAKEN MEAN-REVERSION DESK — CLOSED HONESTLY, THEN REMOVED

Dormant since 2026-08-05 12:09:13. $40,000 of registered capital, one open short.

**The loss it was hiding.** Nothing ticked this book, so its marks were frozen and
every reader valued it at COST: cash $35,996 + cost $4,000 = $39,996, i.e. −0.01%.
That was false. The open short was `PUMP/USD` at 0.002422, and PUMP had risen to
0.00479 — the position was **−97.9%**. Removing the book as it stood would have
deleted a real $3,933 loss along with it.

So the position was closed at the live ask FIRST, on the desk's own accounting
(0.10% maker fee per side, 0.05% slippage each way, exactly the constants in
`kraken_meanrev_desk.py`), and only then was the book deregistered:

| | |
|---|---|
| closed at ask | 0.00479400 |
| gross move (short) | **−97.94%** |
| exit fee / entry fee / slippage | $7.92 / $4.00 / $4.00 |
| **realised P&L** | **−$3,933.34** |
| final equity | **$36,062.66** |
| vs $40,000 start | **−9.84%** |

That loss is now in the firm record. The retirement block is written into
`kraken_bot/meanrev_desk.json` under `retired`, and the close appears in
`closed_trades` with its reason.

---

## 3. OANDA BOOK — REMOVED, BUT READ §4 FIRST

Deregistered, and the entire OANDA block is gone from `desk_tick.sh`: no
rebalance, no status snapshot, no reads. `command_server.py` neither reports it as
capital, nor as positions, nor as P&L, nor mirrors its sleeves.

`claude_rebalance.sh`, its six rails and the `HALT` file are **left in place** and
untouched at `/Users/test/Downloads/oanda-desk`. Nothing re-wires them;
re-enabling would be a new funder direction.

---

## 4. THE ONE THING THIS DOES NOT DO

**Removing the OANDA book stopped us MEASURING that exposure. It did not flatten
it.** When the desk was removed, the practice account still held open positions —
the four index longs and the funder-directed gold position of 2026-08-27 — and
this desk could not close them:

- `oanda_status.txt` has been **empty since 2026-08-27 18:12** (sustained 401), so
  the positions are unreadable, not merely unread;
- a logged "close" on that desk is a **known no-op** — the ledger has recorded
  closing UK100/DE30 while the account still held them;
- the last ledger NAV was **A$52,285** (2026-08-26), carried since as explicitly
  STALE.

Anyone who wants that exposure actually flat must do it in the OANDA interface
directly. This document exists so that fact cannot be lost with the book.

---

## 5. STILL ON DISK, DELIBERATELY NOT TOUCHED

The direction was read as removing the two **books** from this firm. The
following are a separate, larger, and largely irreversible question, and were
left alone pending an explicit decision:

| Item | Why it was left |
|---|---|
| `/Users/test/Downloads/oanda-desk/` | Holds the desk's cached bar data (~448k bars) and its tooling. Deleting it destroys a dataset that took real time to build and is useful independently of the book. |
| 11 `com.local.oanda-*` LaunchAgents, plus `com.oanda-desk.collect` | A separate standalone installation (`~/Library/Application Support/OANDA Quant Desk`) with its own dashboards, MCP and research agents. Only `com.local.oanda-command-center` is running; `com.oanda-desk.collect` is failing with exit 127. None is part of this firm's tick any more. |
| `kraken_meanrev_desk.py` | Archived and unregistered; the script is inert because nothing schedules it. |

---

## 6. ACCOUNTING CONTINUITY

`firm_risk.capital_event()` already handles a shrinking book set: a removed book
is detected, its last recorded equity is subtracted from the firm peak, and the
change is logged as **bookkeeping, not performance**. Both removals were taken
through that path, so the firm equity series shows no phantom drawdown. The
Kraken desk's −$3,933 was realised BEFORE deregistration precisely so that it
lands as performance, where it belongs, and not as bookkeeping.

---

## 7. TEARDOWN — the second direction, same day

Funder: *"tear them out"*, in reply to §5. Everything in §5 is now removed from the
running system.

**Stopped and removed (12 LaunchAgents + 1 cron job).** All `com.local.oanda-*`
agents and `com.oanda-desk.collect` were booted out and their plists deleted;
the `*/10 * * * *` crontab entry running `oanda-desk/collect.sh` is gone. No OANDA
process runs on this machine. Copies of all 12 plists and the previous crontab are
in `backups/decommissioned_20260828/`.

The vendor's own `UNINSTALL_ON_MAC.command` was deliberately NOT used: it covers
only 4 of the 12 labels, and its `--purge` mode also deletes five Keychain items.

**Quarantined, not destroyed (1.8 GB).** Moved intact to
`~/.tfo-decommissioned-20260828/`:

| | size |
|---|---|
| `oanda-desk/` (incl. the cached bar data) | 205 MB |
| `OANDA Quant Desk/` (the standalone install) | 1.6 GB |
| `kraken_meanrev_desk.py` | 7 KB |

They are out of every path the firm reads and nothing can start them. The final
byte-deletion is left as one command for the funder to run, because it is the only
step in this whole decommission that cannot be undone:

    rm -rf ~/.tfo-decommissioned-20260828

**Keychain: untouched.** Five credential entries remain — "OANDA Quant Desk"
Practice Account ID, Practice API Token, Gmail Address, Gmail App Password and
OpenAI API Key. Removing credentials is the funder's to do:

    security delete-generic-password -a "$USER" -s "OANDA Quant Desk Practice API Token"

**Code that pointed at the removed desk.** Three live consumers were still reading
it and were gated, not left to fail:

- `overlay_lab.py` — dropped the OANDA book from the inventory (29 books now).
  It is DROPPED rather than entered at zero equity: a $0 book is a different
  statement from a book that is not ours.
- `exposure_dial.py` — recomputed clean off that inventory.
- `desk_console.py` (:18900) — both OANDA panels removed. Commenting out the
  source alone left a hard `st["oanda"]` lookup in `render()` that took the whole
  console down with a KeyError on the next restart; the panel is now conditional
  on the source being present, so this cannot recur for any source.

## 8. ONE THING THE TEARDOWN SURFACED — NOT CAUSED BY IT

Forcing a fresh `exposure_dial` computation (its cached reading was 28h old) turned
up **DEGRADED**: mapped capital **86.7%** against a **90% floor**, so the gate for
new auto-probes is CLOSED.

The floor has NOT been touched. It is a risk control and it is failing closed,
which is what it is for. The unmapped capital is:

| book | unmapped | of |
|---|---|---|
| `capacity_probe` | $77,571 | $99,848 |
| `kraken_engine` | $28,818 | $28,818 (99.6% cash) |

`capacity_probe` holds Kraken perpetuals — `PF_CRVUSD`, `PF_PAXGUSD`,
`PF_SPCXXUSD`, `PF_WIFUSD` — which yfinance cannot price at all (404, "possibly
delisted"), so the dial cannot map them to any return series.

**This was already true before the removal.** With the OANDA book's ~$37.6k of
mapped capital added back, mapped capital would be **87.3%** — still under the
floor. The removal cost 0.6pp; the breach is the capacity probe's perps, which
have had no price source since that probe began deploying on 2026-08-24. The last
real dial computation was 2026-08-27T18:05Z, and the ~24h cache is the only reason
it read THROTTLE until now.

Fixing it means giving those perps a price source (the risk engine already prices
Kraken pairs from public OHLC via its `KRK:` pseudo-ticker, which is the obvious
candidate), NOT moving the floor.
