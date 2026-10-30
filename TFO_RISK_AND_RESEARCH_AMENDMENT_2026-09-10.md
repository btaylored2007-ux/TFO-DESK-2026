# TFO risk and research tightening — 10 September 2026

Prospective PAPER-only amendment. Authority: the funder's explicit instruction in the current task, “IMPLEMENT ALL LISTED STRATEGIES IMMEDIATELY AS PER YOUR SUGGESTIONS AND ANY NEW ONES THAT YOU CAN FIND. THERE NEEDS TO BE AN EDGE YOU HAVENT FOUND YET. ARM IT - FULL AUTHORISATION”.

This implements the defensible recommendations in the 15:23 AEST assessment. It does not revive the explicitly withdrawn discretionary dip ladder, promise every book will recover, authorise live trades, reset losing histories, or turn a research result into a validated edge. Original registrations remain unchanged; the prospective differences are recorded here.

## Capital and risk

- No incremental capital to unvalidated return strategies. Existing bounded experiments and passive/measurement controls retain their limits; confirmed mean-reversion halts remain. No capital transfers or discretionary trade orders were made in this implementation.
- New autonomous probes still require the existing $10,000 per-probe, five concurrent/$50,000 total cap, dollar-neutral targets, cost model, validation and beta limits. New evidence requirements tighten admission: positive net t-statistic, effective sample, deflated Sharpe, benchmark superiority, preregistration, untouched holdout, purged/embargo validation, liquidity, delay, decay, independent observations, measured costs, point-in-time data and a distinct mechanism. Required evidence must exist under the desk and match SHA256; executable probe source must match its receipt. Artifact binding verifies integrity, not the truth of research claims; replication is still required.
- A missing, malformed, stale, future-dated, contradictory or explicitly invalid firm-risk decision refuses NEW entries. Existing exits remain available. The later-hunt Fundamental opener now checks book/firm admission under its ledger lock, both before pricing and immediately before commitment. Existing sizing, provenance, date, liquidity and net-return hurdles are not loosened; no arbitrary daily entry limit is added.
- Firm warning/halt thresholds remain −10%/−20%; no force-liquidation rule is added. Accounting repair is separately documented in FIRM_CAPITAL_ACCOUNTING_REPAIR_2026-09-10.md. The internal transfer is not external funding. Missing books cannot become withdrawals; original history and losses are retained.
- Canonical risk inventory now drives the exposure dial. Positions are signed USD exposures divided by total firm equity; gross-exposure diagnostics are explicitly separate. Missing histories are unmeasured dollars, not zero risk, and close the new-probe admission gate. The 10% volatility target and 0.80 minimum admission scale remain. The dial does NOT resize holdings.
- Inverse-weight concentration is named effective holdings, not independent bets. Correlation, covariance dimensions, book risk contributions and joint factor attribution are descriptive measured-sample diagnostics, not proof of diversification during a crisis.

## Carry amendment and accounting

- Retain the corrected 32% gross annualised basket gate. Prospectively restate the old 2% understated per-asset gate as 16%, consistent with the earlier eightfold annualisation correction. No existing entry is rejudged under the new threshold.
- Enforce A$33,000 TOTAL notional across assets, using remaining capacity for staggered admissions. Existing positions do not create another A$33,000 allocation on the next tick.
- Current maker cost metadata is 25bps per side/50bps round trip. Existing booked entry fees remain as recorded, not rewritten at today's fee assumption.
- Elapsed-hour accrual retains the current fixed-notional PAPER model. Missing, stale, nonfinite or invalid inputs and excessively long unobserved intervals suspend accrual; they cannot abort an otherwise valid registered exit check. Unobserved intervals are not recovered by extrapolating a later rate. The ledger remains locked and atomically published; corrupt files cannot reset capital.
- Pre-correction funding remains unreconciled. Recorded accrued funding, current-rate scenarios and settled-rate reconstruction are distinct; none is labelled proven economic edge. No retroactive eightfold multiplication or fabricated cash adjustment is authorised by this repair.

## Research armed, deployment conditional

The finite economic-hurdle carry lab is registered in reports/carry_economic_hurdle_preregistration_2026-09-10.json. It runs two hypotheses and two controls across two cost and three delay cases (24 configurations). It uses a separate append-only public settled-funding cache, two bounded GETs per 20 hours, a research lock and unchanged-input skipping. The grid will not be expanded to seek a green result. Its control explicitly describes the pre-amendment 32%/2% implementation, not the new 16%/remaining-cap rule.

At implementation, no candidate passes. The two hypotheses are carry-family variants, not independent new strategies. Historical data is discovery-contaminated, complete independent cycles/prospective observations are insufficient, and full basis/FX/margin/paired-fill economics remain missing. ZERO new probes are deployed. Existing AUTONOMY_ARMED remains present, but risk and evidence can and currently do refuse allocation.

The existing hourly desk_tick.sh now also runs the read-only carry hurdle report, fully-loaded NAV/concentration diagnostics and incremental research collector. No new scheduler or duplicate autonomous loop is created. Passive same-asset and cash comparisons remain opportunity-cost benchmarks, not a new funded allocation. Cash yield in this firm is simulated.

## Remaining limitations

- Five currently unpriced instrument histories prevent a complete firm-volatility estimate. No portfolio-wide forced scaling is inferred from the partial estimate.
- Fundamental catalyst audit finds 18 dated/44 undated positions; no unambiguous future dates were recoverable. No dates are invented, and the existing design still lacks a general long price stop.
- Betfair is outside the 35-book inventory. Its previously failed snapshot recovered and reports PAPER/no routed orders; no code restart or risk-gate bypass was performed.
- OANDA remains decommissioned and is not reactivated. Its last removal record did not establish flat practice positions.

Verification: 180 selected regression tests pass across accounting, risk, canonical coverage, evidence admission, carry, profitability and Fundamental entry/provenance. Tests use synthetic isolated ledgers; no live orders are used for testing. Syntax/whitespace checks pass. Existing source/runtime work unrelated to this change is preserved.
