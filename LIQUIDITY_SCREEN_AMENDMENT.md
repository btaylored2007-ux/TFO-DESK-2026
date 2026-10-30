# Liquidity Screen — Amendment (ACTIVE, dated 2026-08-25)

**Status:** ACTIVE. Applies to `kraken_floor.py` and `replica_floor.py` — the
Breakout, Mean-Reversion and Micro-Cap Momentum strategy books on both crypto
floors. PAPER ONLY: no broker, no credentials, no real orders.

**Its own clock.** This is a NEW amendment with its OWN record, opened
2026-08-25. It does **not** retroactively change either floor's existing
pre-registered track record, and it does not edit the floors' existing
registrations — those documents carry only an append-only pointer to this one.
Everything the floors did before 2026-08-25 stands exactly as it was recorded.

---

## 1. Basis

**Funder direction, 2026-08-25.** The funder asked to reduce the desk's illiquid
exposure. Hand-trimming the frozen books was **refused** — selling positions out
of a pre-registered forward test destroys the very record the test exists to
produce, and would have made every number those books have published
uninterpretable. The funder was given the alternatives and chose **option 2**:
a *registered rule* barring NEW entries below a liquidity floor, with existing
positions left alone to run to their own registered exits.

*(Provenance: the direction was given in session and relayed to the implementing
agent as "option 2". This document records it as directed; it is not a
counter-signed instrument, and §7 states exactly what was and was not attested.)*

**The data defect this rule had to be designed around.** The paper engine's
`/state` universe reports `vol_musd = 0.0` for **every** symbol. Verified
2026-08-25 against the live engine on :18799:

```
universe entries: 10
  XBT/USD  vol_musd = 0.0      DOGE/USD vol_musd = 0.0
  ETH/USD  vol_musd = 0.0      ADA/USD  vol_musd = 0.0
  SOL/USD  vol_musd = 0.0      LINK/USD vol_musd = 0.0
  XRP/USD  vol_musd = 0.0      AVAX/USD vol_musd = 0.0
                               DOT/USD  vol_musd = 0.0
                               LTC/USD  vol_musd = 0.0
```

The same field read **63.3 for XBT on 2026-08-08**, and the universe has shrunk
from its former breadth to these 10 entries. So the engine's volume field is
**not a usable liquidity source**: a screen built on it would either refuse
every symbol (reading 0 literally) or — worse — pass every symbol, if a reader
were ever tempted to treat 0 as "unknown, therefore fine". That temptation is
precisely the failure mode this amendment is written to be immune to.

A visible second-order consequence of the same defect, noted here as
**pre-existing and NOT caused by this amendment**: `kraken_floor.universe()` and
`replica_floor.kraken_liquid_universe()` filter the engine universe with
`v >= 1.0`, so with every reading at 0.0 they currently return an **empty**
liquid candidate list. The liquid desks therefore have no candidates at all
until the engine's volume field is repaired. That is an engine data bug to be
fixed on its own ticket; this amendment neither creates nor cures it.

---

## 2. The frozen rule

> **NEW ENTRIES ONLY.** A candidate symbol is REFUSED entry when its measured
> 24-hour USD volume is below **$5,000,000**.
>
> **Existing positions are NEVER force-closed, resized, or otherwise altered by
> this rule.** Every open position exits by its own registered rules — stop,
> channel exit, z-score reversion, max-hold — exactly as before. A position
> already held in a name that would now fail the screen simply keeps running to
> its own exit.
>
> **FAIL CLOSED.** If the volume for a symbol cannot be determined — API error,
> symbol absent from the venue's pair list, a zero or `None` reading, an
> unusable cache, or the screen module itself unavailable — the entry is
> **REFUSED** and logged loudly. A volume that cannot be measured is never
> assumed liquid. **A zero reading means NO DATA, not liquidity.**

**Constants** (`liquidity_screen.py`):

| Constant | Value | Meaning |
|---|---|---|
| `MIN_24H_USD_VOLUME` | `5_000_000.0` | the floor; below it, a new entry is refused |
| `CACHE_TTL_S` | `300` | a reading this fresh is reused without a refetch |
| `MAX_CACHE_AGE_S` | `900` | 15 min — beyond this a reading is dead; refetch, and REFUSE if the refetch fails |
| `PERP_FRESH_S` (dashboard) | `900` | display-side: a mark older than this is never labelled live |

**Cache semantics, stated exactly:**

* age ≤ 300 s → reuse the cached reading (this is what stops the screen
  hammering a public API once per candidate per tick).
* 300 s < age ≤ 900 s → refetch. If the refetch **fails**, the still-inside-15-
  minutes cached reading may be used once more and the record is flagged
  `cache_fallback` with its age, so no refusal or admission ever rests on an
  unlabelled stale number.
* age > 900 s, or no entry at all → refetch, and **REFUSE if the refetch
  fails**. No reading older than 15 minutes is ever acted on.

Every cache entry carries a timestamp (`ts` epoch + `utc` string). A reading
without a timestamp is treated as absent.

---

## 3. What this amendment does NOT do

* It does **not** close, trim, resize, re-price, or re-date any existing
  position on either floor, in any code path, under any failure mode.
* It does **not** loosen any risk control. It **only ADDS a veto**. Every stop,
  hold limit, position cap (`MAX_POS = 8`), allocation fraction, cost model and
  exit rule on both floors is byte-for-byte unchanged.
* It does **not** change entry *signals*. A signal that fires still fires; the
  screen sits downstream of it and may only refuse to act on it.
* It does **not** touch the paper engine (`kraken_paper_bot.py`), its
  supervisors, the carry executors' economics, OANDA, or any secret.
* It does **not** alter either floor's historical record.

**Structural guarantee.** The screen is *unable* to alter a position, not merely
instructed not to. `liquidity_screen.check()` takes a **symbol string**.
`liquidity_screen.record()` takes the caller's own `liquidity_screen`
**subtree** plus a record dict. Neither function is ever handed a ledger root, a
positions map, a unit count or a cash balance, so there is no reference through
which it could reach one. The floors call the screen from a single site — after
`entry_signal()` returns a side, before any sizing — and the only action
available to it there is `continue`.

---

## 4. The independent volume source

**Kraken's PUBLIC ticker endpoint.** No credentials, no authenticated call, no
order path:

```
https://api.kraken.com/0/public/AssetPairs      -> wsname -> pair-key map
https://api.kraken.com/0/public/Ticker?pair=... -> per pair: c[0] last, v[1] 24h base volume

24h USD volume = v[1] * c[0]
```

The `AssetPairs` step exists because a batched ticker response is keyed by
Kraken's canonical pair key (`XXBTZUSD`), not by the symbol asked for
(`XBTUSD`); mapping through `wsname` is the only reliable way to read a batched
response back. A symbol absent from `AssetPairs` is REFUSED, never guessed at.

This is the *same* measure the floors' own `micro_universe()` already used to
build the micro-cap hunting ground, so the screen and the universe builder speak
one language.

**Verification, 2026-08-25:**

| Symbol | Measured 24h USD volume | Against the $5M floor |
|---|---:|---|
| XBT/USD | $339,759,480 | ALLOW |
| ETH/USD | $90,165,943 | ALLOW |
| COTI/USD | $226,352 | REFUSE |
| AKT/USD | $291,963 | REFUSE |
| NOTAREAL/USD (not a listed pair) | — | REFUSE (fail-closed) |

---

## 5. Measured impact (honest, at 2026-08-25)

Against the micro-cap desk's live hunting ground (12 names, built by
construction to sit in the $0.5–5M/day band):

**0 of 12 clear the floor. 12 of 12 are refused** — measured range $0.58M
(BONK/USD) to $1.71M (BNB/USD).

So the practical effect of this amendment is that **Micro-Cap Momentum is
closed to new entries** for as long as its universe definition sits below the
floor. That is the intended consequence of the funder's direction, stated
plainly rather than discovered later.

The eight micro-cap names currently **held** all measure below the floor too —
CTSI $0.007M, CHIP $0.10M, AAVE $4.48M, EURC $0.96M, FARTCOIN $2.25M,
BNB $1.71M, BIO $0.086M, DRV $0.41M. **None of them is closed by this rule.**
They run to their own registered exits. The desk's illiquid exposure therefore
decays as those positions exit naturally, and cannot be replaced — which is
exactly what "reduce illiquid exposure without destroying the track record"
means.

---

## 6. Where a refusal is recorded

Every refusal appends a record to the **floor's own ledger**, under a
`liquidity_screen` subtree (additive; no existing key is touched), and prints a
line to the tick output:

```json
{
  "symbol": "COTI/USD",
  "allowed": false,
  "vol_usd_24h": 226352.48,
  "min_usd_24h": 5000000.0,
  "reason": "REFUSED: 24h volume $226352 is below the $5000000 liquidity floor",
  "floor": "Kraken Floor",
  "desk": "Micro-Cap Momentum",
  "utc": "2026-08-25T06:06:04Z",
  "source": "kraken_public_ticker (24h base volume x last price)",
  "amendment": "LIQUIDITY_SCREEN_AMENDMENT.md (funder-directed 2026-08-25)"
}
```

```
LIQUIDITY SCREEN REFUSED ENTRY: COTI/USD [Kraken Floor / Micro-Cap Momentum]
  REFUSED: 24h volume $226352 is below the $5000000 liquidity floor
```

Screen-level failures (AssetPairs down, a ticker batch failing, an unreadable
cache) additionally persist an error marker to
`KrakenQuantDesk/reports/liquidity_screen_errors.jsonl`. Nothing here fails
silently. All stamps are UTC.

---

## 7. Funder sign-off

- [x] **APPROVED** by (funder): **Brett Taylor**   Date: **2026-08-25**
      Direction: reduce illiquid exposure; hand-trimming the frozen books
      declined; **"option 2"** — a registered NEW-ENTRY liquidity floor with
      existing positions left untouched — selected explicitly.
- [x] Scope confirmed: `kraken_floor.py`, `replica_floor.py`; entries only.
- [x] No risk control loosened; this amendment only ADDS a veto.
- [x] PAPER ONLY. Public data only. No credentials. No real orders.

**What is attested, precisely:** that the funder directed this rule and selected
option 2 on 2026-08-25, as relayed to the implementing agent. What is **not**
claimed: a countersigned document, a spec hash sealed in the funder's presence,
or any approval beyond the scope written above. Any widening of this rule — a
different floor, a different venue, or any application to *existing* positions —
requires a fresh direction and a new amendment with its own clock.

---

## 8. Implementation & verification record (2026-08-25)

**Files:**

| File | Change |
|---|---|
| `KrakenQuantDesk/liquidity_screen.py` | NEW — the screen. Shared by both floors. |
| `KrakenQuantDesk/kraken_floor.py` | +76 lines, **0 deletions** — import shim, `liquidity_ok()`, `record_refusal()`, batched prefetch, one veto at the entry site. |
| `MultiVenueDesk/replica_floor.py` | +78 lines, **0 deletions** — identical wiring, same module. |
| `KrakenQuantDesk/reports/liquidity_screen_cache.json` | NEW — timestamped volume cache. |
| `KrakenQuantDesk/reports/liquidity_screen_errors.jsonl` | NEW — persisted error markers. |

Both floors' edits are **pure insertions** — the pre-existing logic is
unchanged, line for line.

**Scratch-harness proof** (scratch ledgers only; live state hashed before and
after and proven byte-identical). All five cases pass on **both** floors:

| Case | Setup | Result |
|---|---|---|
| A | XBT/USD, ~$339M/24h | **ALLOWED** — position opens |
| B | COTI/USD, ~$0.23M/24h | **REFUSED** — no position opens, refusal recorded |
| C | every public API call raises; cache wiped | **REFUSED** (all) — nothing opens |
| D | screen module absent entirely | **REFUSED** (all) — nothing opens |
| E | an illiquid name already HELD, another refused in the same tick | held position **still open, entry/units/cost identical, nothing closed** |

**First live tick after the amendment (honest):** both floors ticked cleanly and
reported *"(no entries/exits this tick)"*. **No refusal fired, because no entry
was due** — all three desks on the Kraken floor sit at `MAX_POS = 8`, so the
entry loop breaks before a candidate is ever screened, and the liquid candidate
list is empty for the engine-defect reason in §1. The screen is proven by the
harness above, not by that tick. It will first bite in production when a slot
frees.

**Non-disturbance:** every open position, cash balance, realized P&L and
closed-trade count on both floors was byte-identical before and after the live
runs — 24 open positions on the Kraken floor, 20 on the replica, all intact.


---

## POINTER — Firm risk amendment, 2026-08-27 (append-only, nothing above edited)

A second entry-side veto now sits alongside the liquidity screen on the same two
floors (plus `coinspot_floor` and `cross_asset_floor`): the **book-level kill**
(freeze at −30% drawdown from the book's own peak) and the **firm-wide halt**
(new entries refused firm-wide at −20% firm drawdown).

Same shape as this amendment, deliberately: **NEW ENTRIES ONLY**, fail-closed,
recorded and printed. No existing position is closed, resized or altered by
either gate, and **exits are never blocked** — the outcome both amendments must
not cause. The firm-wide monitor **never force-liquidates at any level**.

**Full registration:** `FIRM_RISK_AMENDMENT.md`
**Implementation:** `book_kill.py`, `firm_risk.py`, `book_coverage.py`
