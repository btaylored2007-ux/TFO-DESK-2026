# Exposure Dial — Registration (CAP form)

**Spec hash (sha256[:16]): `21e05244c53d4e77`** — sealed 2026-08-15. Editing any frozen
field invalidates this registration. NEVER edit to fit a result.

## Authority & provenance
Funder directed "build the exposure dial" 2026-08-15, selecting menu item 2 of the
risk-overlay study (`reports/research_overlay.md`). The study's corrected replay record
showed the UNCAPPED 10% vol-target levers ~1.5x in the current regime and amplified the
2020/2022 crash replays — therefore ONLY the cap form is registered. The dial is a
RISK-REDUCTION instrument: it can only ever lower risk-taking, never raise it.

## Frozen specification (do not edit)
- **Form:** `scale = min(1.0, target / trailing_firm_vol)` — **de-levers only; scale
  above 1.0 is impossible by construction.**
- **Target:** 10%/yr firm vol. **Estimator:** trailing 63 trading days, annualised.
- **Basis:** the firm-wide all-books panel (audited `overlay_lab.py` inventory: every
  book, A$ converted, static current-composition proxy, capital-weighted).
- **Cadence:** daily (heartbeat-guarded at 20h freshness; `--force` recomputes).
- **Outputs:** `reports/exposure_dial.json` (current reading) and
  `reports/exposure_dial_history.jsonl` (append-only record).

## Powers (exhaustive — nothing else)
1. **Display**: a dial card on the desk report page / war room.
2. **Tighten the autonomous gate**: `autonomous_deploy.py` REFUSES new auto-probes when
   scale < 0.80, or when the dial is stale (>48h) or unreadable — **fail-closed.**
   This only ever blocks deployment; it can never approve one.
3. **Discretionary-book practice**: when scale < 1.0, the head trader reduces
   `claude_discretionary` gross toward the scale at the next supervised session
   (logged in that book's journal, per its own registration).

## Prohibitions (absolute)
- Never resizes, trades, or freezes any frozen-registration book.
- Never levers up. Never places or closes any position mechanically.
- A dial failure changes NOTHING except blocking new autonomous deployments (fail-closed).

## Audit addendum 2026-08-15 (hardening — tighten-only; frozen fields unchanged)
Same-day adversarial audit found a SOFT-failure fail-open: a partial data outage
(yfinance/Kraken dropping columns) would zero-fill missing books, understate vol, and
write a fresh green FULL — leaving the gate open exactly when data disruption and vol
spikes correlate. Hardened immediately: `compute()` marks the reading **DEGRADED** when
any weight-bearing instrument is absent from the trailing panel or mapped capital falls
below 90%; a DEGRADED reading NEVER opens the gate (`_dial_ok` refuses it), and the gate
also refuses any scale outside the registered (0, 1] range. Hard failures (no file /
stale / unreadable) remain fail-closed as originally specified. Every failure mode now
closes the gate; none can open it.

## Honest caveats
Firm vol on the all-books basis (5.94% at registration) sits well under the 10% target,
so the expected steady-state reading is scale = 1.00 (FULL). The dial is insurance for
vol-spike regimes (2008/2020-style), where trailing vol breaching 10% throttles new
deployment and signals de-risking — it is NOT expected to act in calm markets, and a
long record of 1.00 readings is the dial working, not the dial being useless.

## Defect addendum 2026-08-28 (measurement fix; frozen fields unchanged)

**No frozen field is edited.** Form, target (10%), estimator (63d), cadence,
outputs, `GATE_FLOOR` (0.80) and `MAPPED_FLOOR_PCT` (90%) are all untouched. What
changed is that the registered **Basis** — "the firm-wide all-books panel (audited
`overlay_lab.py` inventory: **every book**, A$ converted, ... capital-weighted)" —
was not actually being computed. Two defects in `overlay_lab.py`:

**1. Kraken perpetuals were never routed to a price source.** The sleeve loader
sent a symbol through `crypto_instr()` only when it contained `"/"`, so
`capacity_probe`'s four `PF_*USD` perps went to yfinance as literal tickers and
404'd. `book_coverage.yf_ticker()` has always carried the correct
`PF_SOMEUSD -> KRK:SOME` rule; the two modules simply disagreed, and the one
feeding this dial was the wrong one. Now wired: PAXG, WIF and CRV price from
Kraken public OHLC (721 daily bars each) exactly as every other `KRK:` series
does. `PF_SPCXXUSD` has no Kraken spot pair and is left honestly unmapped.

**2. Unpriceable positions were counted in UNITS, as if units were dollars.** The
no-price fallback was `lost = abs(units)`. `capacity_probe` holds four ~$9,980
clips, but two are sub-dollar tokens (46,911 WIF, 30,586 CRV), so "unmapped
capital" summed to **$77,571 against $39,930 actually deployed** — and that
fiction was the numerator of the 90% floor test. The error is arbitrary in both
directions: PAXG's 2.14 units would have reported **$2** of unmapped exposure on a
$9,995 position. Unpriceable positions are now valued at their stored entry basis,
which is what `risk_engine` has always done — it reports SPCXX at $9,976, and the
lab now reports the same $9,976 for the same position.

**This was a live fail-open, of exactly the kind the 2026-08-15 addendum was
written to prevent.** Unmapped capital contributes no return series, so those
perps were being carried at zero volatility. The mapped-capital floor exists to
catch that, but the number feeding it was not a dollar figure at all. Here it
happened to over-count and so fired conservatively; on a position like PAXG it
would have under-counted and left the gate open.

**Measured effect** (recomputed 2026-08-28, same 63d window):

| | before | after |
|---|---|---|
| mapped capital | 86.7% (fictional numerator) | **94.5%** |
| state | DEGRADED | **THROTTLE** |
| firm vol (63d ann.) | 12.26% | **14.00%** |
| scale | 0.8159 | **0.7145** |
| auto-probe gate | CLOSED (degraded) | **CLOSED (scale < 0.80 floor)** |

The gate is closed either way, but for opposite reasons: it was closed because the
dial could not measure, and is now closed because it can. The firm was carrying
~$30k of memecoin-perp volatility that the dial had been treating as cash, so a
correct reading de-levers harder. That is the instrument working, not a new rule.

**One tighten-only addition.** A position with neither a live price nor an entry
price cannot be sized at all, so it would be absent from the unmapped total rather
than counted in it — a hole of unknown size in the floor test. Such a position now
sets `unvaluable_positions` and forces DEGRADED. Consistent with the 2026-08-15
principle: every failure mode closes the gate; none can open it.
