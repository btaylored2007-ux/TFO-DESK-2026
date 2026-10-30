# Carry v2 — Proposed Amendment (DRAFT — UNSIGNED, NOT ACTIVE)

**Status:** PROPOSAL awaiting funder sign-off. This document does NOT modify
`carry_paper_desk.py`, the live executor, or the existing carry registration.
Nothing here is active until (a) the funder signs below AND (b) the executor is
pointed at it under the funder's own settings-gated authority — never from chat.

**Basis:** `reports/research_carry_deepening.md` (2026-08-10). PAPER ONLY.

---

## What changes (two levers only — the durable, capacity-robust ones)

1. **Maker execution.** Post passive (maker) orders instead of paying taker.
   Round-trip cost ~32 bps maker vs ~90 bps taker. On a multi-week carry hold
   this is essentially free edge. **Est. +1.15%/yr.** Confidence: highest.
   Risk impact: none material — it *reduces* cost; it does not change what is held
   or when. Fills that don't post simply don't trade (no chasing).

2. **Harvest gate 4% → 2% annualised funding.** Enter when funding clears 2%
   (vs 4%). **Est. +0.45%/yr.** Confidence: high. This is an EDGE-CAPTURE
   parameter, NOT a safety control:
   - The exit rule is UNCHANGED (exit when funding < 0), so turnover does not
     rise and the round-trip cost is paid no more often.
   - Cost margin is PRESERVED/improved: ~5.1× → ~5.9× (maker), still far above
     the 1× break-even. The desk still sits out the deepest-negative regimes.
   - No stop-loss, sizing cap, delta-neutrality, or liquidity screen is loosened.

## What is explicitly NOT in this amendment
- **NO asset expansion beyond BTC/ETH.** XMR (+12%/yr) and HYPE (+8.6%/yr) are
  real and persistent but carry thin liquidity and an unmeasured basis-cascade
  tail. If pursued at all, they belong in a SEPARATE, tiny, per-name
  volume-capped probe with its own registration — never folded in here.
- **NO change to sizing, delta-neutrality, kill switches, or any risk rail.**

## Preserved rails (unchanged from the sealed carry desk)
Delta-neutral (long spot / short perp, same asset); liquidity + cascade screens
(illiquid perps like BICO remain non-harvestable at size); existing kill/HALT
controls; PAPER ONLY; public data only; no credentials; no real orders.

## Expected result (measured, not promised)
~**+1.6%/yr net additional carry at scale** (maker + 2% gate, BTC/ETH), on top of
the executor's current harvest. This is an ESTIMATE from historical funding; the
182-day live-evidence rule applies before any verdict. Benchmark: 4%/yr cash.

## Adoption path (the disciplined way — keeps the track record clean)
This is a NEW amendment with its OWN clock, not an edit of the sealed carry
registration (editing that would invalidate its record). On sign-off it is
hash-sealed and dated; the executor change is applied under the funder's
`permissions.allow` settings grant, as with the original carry automation.

## Funder sign-off
- [x] Approved by (funder): **Brett Taylor**  Date: **2026-08-10**
      (explicit in-session sign-off: "I, Brett Taylor, approve Carry v2")
- [x] Executor change applied under the existing carry settings grant
      (carry desk already permitted to run; params amended, no new automation).
- Spec hash (sealed 2026-08-10): **52f54a1a586873f5**

---

## Amendment note — stale-snapshot gates on the executor (dated 2026-08-22T13:41Z, append-only)

**Basis:** funder standing directive 2026-08-22 — "all books need live prices, no
stale prices to trade against" (Non-negotiable #7). Audit: reports/STALE_PRICE_AUDIT.md §2.3.
Documented precedent: the +171% BICO row in carry_monitor.json is a 12-day-old
ghost (age_sec ≈ 1.34M) — multi-day-stale rows really persist in this exact feed.

**Defect:** carry_paper_desk.py traded and accrued against `carry_monitor.json`
with no age check — it read neither the snapshot's `checked_at` nor the per-asset
`age_sec` the monitor already computes. If the collectors died mid-HARVESTABLE it
would open at a frozen mark and compound a frozen funding rate forever.

**Fix (2026-08-22, carry_paper_desk.py `tick`):**
- ENTRY refused when the snapshot's `checked_at` is older than 2 hours
  (MAX_SNAPSHOT_AGE_SEC), or when the asset row's `age_sec` exceeds 24h
  (MAX_FUNDING_AGE_SEC — the identical constant and registered gate of the
  sibling carry_newnames_probe). Refusals log ENTRY_REFUSED_STALE to the ledger
  and print loudly.
- ACCRUAL suspended (no funding accrued; ACCRUAL_SUSPENDED ledger event + loud
  line every affected tick) whenever the snapshot is stale or the position's
  funding row is >24h old. The unobserved window is forfeited — the epoch
  advances without accrual, an honest under-count, never a frozen-rate
  over-count.
- EXITS always allowed: the trailing-carry exit rule reads carry_history.jsonl
  and runs regardless of snapshot age.

Entry threshold (2%), exit rule, sizing, eligibility, costs and margin-stress
kills are byte-preserved. These are guard clauses only — no change to the
economics of Carry v2.

### Cost correction — 16 bps was never achievable (2026-08-27, append-only)

The blind-spot audit found this amendment's economics were computed at **16 bps/side**,
a figure that implies Kraken's **$10M/30-day volume tier**. Measured live from
Kraken `AssetPairs` on 2026-08-27: the base tier this desk actually trades at is
**40 bps taker / 25 bps maker** on XBT, ETH, SOL and PAXG alike.

**Consequence, stated plainly:** the headline "+1.15%/yr from maker execution,
confidence: highest" and the "~5.9x cost margin" in this document were computed at
a cost that does not exist. The real maker round trip is **50 bps, not 32** — so
the maker lever is worth materially less than claimed, and the cost margin is
correspondingly thinner. The 2% gate and the exit rule are unaffected in
DIRECTION but their expected value was overstated.

**Fix:** `carry_paper_desk.py COST_BPS_PER_SIDE` corrected 16.0 → **25.0**
(measured maker). Entry gate, exit rule, sizing, eligibility and margin-stress
kills are byte-unchanged. The correction moves AGAINST the book, never for it.

**Also outstanding (NOT fixed here — funder decision):** the audit found that
basis carry, this desk's one "validated" mechanism, was **never run through the
deflation harness at all** — `grep` for deflation/quant_validation/DSR in
`research_basis_carry.md` and `research_carry_deepening.md` returns zero lines.
Every killed candidate cleared that bar; the survivor did not. It should be
re-run at measured costs before the mechanism is relied upon further.
