# CEF Discount-to-NAV Reversion — Paper Probe Pre-Registration

**Spec hash (sha256[:16]): `6d13387831117064`** — sealed 2026-08-14. Editing any
frozen field below invalidates this registration. NEVER edit to fit a result.

## Authority
Funder authorised deployment 2026-08-14 after reviewing `cef_discount_lab.py`
(receipt `reports/cef_discount_lab.json`, note `reports/research_cef_discount.md`).
This is a FORWARD out-of-sample paper probe. PAPER ONLY — no broker, no orders, no
credentials, ever. It exists to earn (or destroy) significance on its LIVE record,
where search-deflation does not apply because it is ONE pre-committed test.

## Why a forward probe rather than a backtest verdict
The in-sample harness KILLED this on deflation (t=1.20, DSR confidence 0.585 vs
~0.95 needed, after 1,179 house trials) — but not for lack of edge. It showed the
strongest properties of anything tested: net Sharpe +0.54 (3% borrow), +60 bps/trade
after costs, beta to SPY −0.05 (market-neutral), +64.9% in the 2008 crash, 33
effective bets. The honest resolution of "real but in-sample-underpowered" is a
pre-registered forward test, not more backtest torture. This probe IS that test.

## Frozen specification (do not edit)
- **Mechanism:** closed-end-fund discount-to-NAV reversion (Pontiff 1995/1997).
- **Signal:** `disc = price/NAV − 1`; `disc_z` = trailing-24-month z-score of each
  fund's own discount. **LONG** the widest-discount-vs-own-history tercile (most
  negative z), **SHORT** the narrowest/richest tercile. Dollar-neutral, equal-weight
  within each tercile.
- **NAV source:** Yahoo `X<TICKER>X` pseudo-ticker (official fund NAV, free).
- **Universe (28 CEFs):** BDJ BGY BME BOE BST DSL EOS ETV ETW ETY EXG GAB GOF JPC
  JQC NAD NCV NEA NZF PCN PDI PFN PTY RCS RVT UTF VKQ VMO.
- **Capital:** $10,000 paper. **Rebalance:** monthly (28-day minimum).
- **Costs modelled:** 25 bps per side on turnover; **short borrow** 3%/yr.
- **Kill rule:** freeze at −30% drawdown (enforced by `mark()` — same as every sleeve).
- **Benchmark:** cash (dollar-neutral book). **Clock:** 182 days.
- **Started:** 2026-08-14. **First & only verdict:** 2027-02-12. No verdict before.

## Monitoring
Registered as tournament sleeve `cef_discount` (`reports/tournament/cef_discount.json`),
updated every desk tick, shown on the tournament board and war-room dashboard. Ranking
metric at review is return/maxDD, per the tournament rules — raw return never ranks.

## What would make it a real desk
If it holds its forward record to 2027-02-12 (positive risk-adjusted, DD kill not hit,
market-neutrality intact), it graduates from probe to a registered sleeve. If it decays,
it is frozen and killed honestly. Either outcome is logged with receipts.

---

## Disclosure — stale-fill fix (appended 2026-08-22T13:41Z, append-only; sealed spec bytes above unchanged)

**Basis:** funder standing directive 2026-08-22 — "all books need live prices, no
stale prices to trade against" (Non-negotiable #7). Audit: reports/STALE_PRICE_AUDIT.md §2.2.

**Defect (period 2026-08-14 probe start → 2026-08-22 fix):** the monthly rebalance
priced every fill from `lab.yf_hist`'s cache, which has no read-TTL and is purged
only when its newest file exceeds 20 days — so fills could book at closes up to
20 days old whenever a research run had refreshed the cache mid-cycle.

**Fix (2026-08-22, cef_probe.py `current_targets`):** signal (z-score panel) still
reads the cache — the registered signal is unchanged. FILL prices now come only
from a fresh 5-day fetch at decision time (`_fresh_fill_prices`), with a per-name
recency refusal: any name whose latest fresh bar is older than 2 trading days is
REFUSED loudly, skipped this cycle, and retried next cycle (the existing
"drop names without prices" machinery handles the skip). Terciles, gross,
dollar-neutrality, costs, cadence and the 182-day clock are untouched. This
restores the registered intent (discounts current at rebalance) rather than
changing any rule.
