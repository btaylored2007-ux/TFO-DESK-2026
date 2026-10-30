# Autonomous Deployment Policy (PAPER) — Option 2

**Granted by Brett Taylor, 2026-08-14, deliberate policy approval.** This is the durable
authority artifact — autonomous deployment is governed by THIS file + the deterministic
gate in `autonomous_deploy.py`, never by a chat message. PAPER ONLY, always.

## What it authorises
The weekly hunt may deploy a discovered mechanism as a $10k paper probe WITHOUT per-probe
sign-off — but ONLY through `autonomous_deploy.py`, which refuses anything that fails the
gate below. The agent does not decide; the deterministic gate does. The gate is STRICTER
than funder-discretion: note `cef_discount` (deployed by Brett manually) would NOT qualify
here — it failed deflation. Autonomy only ever deploys things Brett would not have had to
argue about.

## The gate — ALL must hold (checked in code against the lab receipt)
1. `survives_deflation == True` (search-deflated Sharpe beats the null after the full house trial count)
2. `t_stat >= 2.0` (genuine significance, not a flicker)
3. `n_effective >= 20` (repeated bets, not one persistent position)
4. net per-trade `> 0` (clears its modelled cost wall)
5. `|beta_to_SPY| <= 0.30` (low-beta — the firm already carries +0.24; no adding to it)
6. A `<name>_probe.py` exists and `current_targets()` returns a dollar-neutral book
   (|Σweights| < 0.05, gross <= 2.0, every leg priced) — validated at deploy time.

## Hard rails (never loosened)
- **PAPER ONLY.** No broker, no orders, no credentials, ever.
- **$10,000 per probe.** **−30% drawdown auto-kill** (standard `mark()` freeze). **182-day clock.**
- **Portfolio caps:** at most **5 concurrent** auto-probes and **$50,000 total** auto-deployed.
  Beyond either cap, autonomy STOPS and reverts to proposing to Brett.
- **Sealed pre-registration** auto-written (`<NAME>_PROBE_PREREGISTRATION.md`, hash) BEFORE capital deploys.
- **Frozen registrations untouched.** `kraken_paper_bot.py` never edited. Engine never restarted. Secrets never read.

## Arm / disarm (Brett controls, instantly)
- **Armed** iff `AUTONOMY_ARMED` exists AND `AUTONOMY_HALT` does NOT. Both checked every deploy.
- **Kill switch (instant, global):**
  ```
  touch /Users/test/Applications/MultiVenueDesk/AUTONOMY_HALT
  ```
  Disarms all future autonomous deployment immediately. Existing probes keep running under
  their own −30% kill; remove the file to re-arm.
- **Disarm permanently:** delete `AUTONOMY_ARMED`.

## Amendment 2026-08-15 — exposure-dial tightening (funder-directed)
The gate additionally REFUSES any new deployment when the registered exposure dial
(`EXPOSURE_DIAL_REGISTRATION.md`, spec 21e05244c53d4e77) reads scale < 0.80, is stale
(>48h), or is unreadable — fail-closed. This is a strictly tighter gate: the amendment
can only block deployments, never enable one. No existing threshold was loosened.

## Transparency
Every autonomous deployment: writes the sealed pre-reg, appends to
`reports/autonomous_deployments.log` with the receipt numbers, and push-notifies Brett with
the mechanism, the gate figures, and the pre-reg hash. Nothing deploys silently.

## Review
Auto-probes appear on the tournament board + dashboard exactly like every sleeve, tagged
`auto`. Each earns a 182-day forward verdict; graduates or is killed honestly. This policy is
reversible at any time by Brett with no loss to the running books.
