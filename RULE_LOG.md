# TFO desk rule log (dated; newest first). PAPER.

## 2026-10-29 20:35 WITA: brett_override_20261029_2017 (PAPER)
- Brett, verbatim, 20:17 WITA (TFO chat 81c0a6b3; his own reply to TFO's question "Hunt book sizing and slots?"): "Raise the hunt cap to A$5k a trade and add a 4th slot"
- RULE: the US MAIN hunt book (state/hunt48_short) risk cap per trade goes A$2.5k -> A$5k, and max open goes 3 -> 4. Scope is the main book only: the GPT and Claude comparison books and the ASX/Asia/LSE hunt engines stay at 3 / A$2.5k. MIN_ROOM_R 1.0, heat 5%, ADV, sector caps, stops, R rails and direction balance (last slot opposite once 3 are the same direction) are unchanged. Existing tickets keep their own risk_cap_aud (FLG t4, VDE t24, ARE t25 not resized).
- WCLD (grade A) armed under the new cap: t26.

## 2026-10-29 19:50 WITA: brett_override_20261029_1917 (PAPER)
- Brett, verbatim, 19:17 WITA (TFO chat 81c0a6b3, [t1957u]): "Arm the grade A’s"
- Scope: lifts the soft trim for grade A names on this US + LSE pre-open scan ONLY. All hard rules stay: stops, room >= 1.0R, sector and heat caps, ADV, A$500k floor, price not through stop, R rails. GPT verdict is advisory (Brett 18:00).
- Grading (RULE_LOG 11:32): A+ = trend + clean chart + room to target; A = trend + clean chart + room >= 1.5R; B or worse = pass. Hunt T2 is 1.5R, so every cleared hunt name grades A at best.
- Result: ARE short ARMED (main id 25, 19:49 WITA, A$2.5k-cap normal hunt sizing). WCLD (A) staged: no slot left (main book 3/3). IYE rejected: sector cap (VDE in Equity Energy) and not top decile. LSE PSON.L and MKS.L cleared but grade B (room 1.26R / 1.23R < 1.5R), and PSON.L live 1306.5 is through stop 1353.9. Not armed.

## 2026-10-29 18:07 WITA: brett_override_20261029_1758 (PAPER)
- Brett, verbatim, 17:58 WITA (TFO chat 81c0a6b3, [t1953u]): "ARM THEM" (replying to VDE/SLDE/RNG passing desk rules).
- Scope: VDE, SLDE and RNG only, exempt from the soft trim and the GPT gate. GPT verdict is recorded as an advisory warning on the ticket. Normal sizing and stops, R rails, MIN_ROOM_R 1.0.
- Result: VDE ARMED (main, id 24, 18:07 WITA). RNG NOT armed: px 86.215 is through its stop 93.5775, so the setup is invalid. SLDE NOT armed: px 27.19 is through its stop 27.9102, and the sector cap is already used by FLG (Financial Services). Hard safety gates were not exempted.

## 2026-10-29 18:05 WITA: GPT FAIL advisory again (gptadvisory, PAPER)
- Brett, verbatim, 18:00 WITA (TFO chat 81c0a6b3, [t1954u]): "Chat gpt can be warning not a block"
- hunt_verdict_veto.veto(): "2026-10-29 item 8" is reverted. GPT FAIL/PENDING no longer blocks or demotes; new warn() gives the advisory text, recorded as ai_gpt on the ticket. Still blocks: finalize != CLEARED, missing or stale verdict files, and no row. hunt_clearance.ai_ok was already advisory (desk PASS -> CLEARED), unchanged. Covers the ASX, Asia, LSE and US paths.

## 2026-10-29 17:49 WITA: HUNT EXIT RULE FINAL (bankfinal) — equity/session hunts (Asia/LSE, ASX, US main+GPT). PAPER
Brett, verbatim:
- 17:30 WITA: "The reward risk ratio - for example Theo's risk was $4500 aud and then we made $4500"
- 17:34 WITA (relayed): "If it covers fees and risk amount we are up, I'm not sure what the problem is? Banking it is profit forward."
- 17:38 WITA: "Yes" (full exit at 3R with a trailing stop before that, over keeping the +5% full exit)
- 17:44 WITA: "Yes go ahead" (time stop)
RULE:
1. R locked at entry = fill-to-ORIGINAL-stop risk; never recomputed after stop moves.
2. Half-bank at NET A$ P&L >= 1R (trend-tagged setups 1.5R; untagged 1R). Net = full round trip: entry+exit commission, slippage, borrow (shorts).
3. +2% does nothing (no half, no stop move).
4. Only AFTER the half: remainder stop -> fee floor (entry +/- RT cost/share), then existing ATR/structure trail (US). Asia/ASX cal path has no trail: fee floor holds until 3R/giveback/time exit.
5. FULL FLAT at net 3R of original risk (price move on original size). Replaces +5% full flat / T2 full exit on these hunts.
6. TIME STOP: exit if < +0.5R net by the close of session 2 (US: close of fill session +1 NY session; Asia/ASX: fill + 48h). Deadline fixed at entry (time_stop_utc), never extended; missing mark -> hold; exit_reason rail_time_stop; counterfactual rail logged to state/hunt_giveback/early_banks.jsonl. No stop move at 0.5R (rejected). Applies only to trades filled after 17:45 WITA (deadline must be fixed at entry); FLG and earlier fills grandfathered.
7. -5% stop, short-side stops, giveback (a)(b)(c), MIN_ROOM_R 1.0 unchanged. Claude book (FLG stop 10.73) untouched. Calendar bench unchanged.
Unmodelled: Asia/ASX borrow fixed at 3 days (not accrued); US exit slippage flat 5bp; FX conversion cost on A$ marks; SEC/FINRA fees only if IC.us_side includes them; stamp duty refunds n/a.

## 2026-10-29 17:20 WITA: GIVEBACK DNA — hard standing rule for EVERY bot and engine (Brett HARD 17:17 WITA, spec final 17:20; PAPER)
- RULE: GIVEBACK DNA (Brett HARD 2026-10-29 17:17 WITA; spec final 17:20; PAPER): every bot and engine. Never sit through a reverse that gives back open profit. Mechanical early-bank trigger on an open winner BEFORE its normal half-bank: (a) peak >= +0.5R then >= 50% of that peak given back; OR (b) a bar closes through the prior bar's low (shorts: high) on >= 2x average volume; OR (c) a logged adverse news item. Action: bank half or more at the live mark, residual stop to BE then normal trail. Noise (no trigger) is not a turn. Never forces an entry; never sells what is not held. Every early bank logs reason + rail counterfactual (what the normal rail would have done). Normal half-bank = net A$ >= 1R of ORIGINAL entry risk after costs (NO +1% leg); Dex trend seat 1.5R.
- Scope: Veteran Desk seats (rail code owned by Marcus Hale in veteran_desk/book.py — not touched here), hunt48 books (US/ASX/LSE/Asia via hunt_orchestrator), OANDA sleeve, Trend18, TFO V1 rails (tfo_trail_rail/tfo_bank_rail_scanner), crypto.
- SUPERSEDES the 17:13 bank1pct "+1% of deploy_basis" leg: half-bank is net A$ >= 1R original risk after costs only (Dex trend 1.5R). Code change for VD is Marcus's.
- Soft trim A+-only for new crypto arms stays until Fri 30 Oct 15:11 WITA (unchanged).
- Status: documented here + HUNT_BRIGADE.md + README_WATCHLIST.md; TODO hooks (comment only, no logic change) in hunt_orchestrator.py and oanda_sleeve.py. Engines without a coded trigger: agents apply the rule by hand and log reason + counterfactual.

## 2026-10-29 10:12 WITA: Veteran book/desk no-chase — pullback buy-limits allowed (Marcus ISRG)
- BUG: arm/live_check treated any entry >1% from live in either direction as chasing. VET-ISRG-20261029-010231 buy-limit 436 vs live 444.19 (~1.9% above) blocked as not chasing.
- RULE: buy-limit below market = pullback, always allowed (mirror sell-limit above = rally wait). Chase block only for `order_type=stop_limit` already >1% through the trigger. Through-stop still hard-rejects. Fill path already refused stop_limit beyond limit.
- Files: veteran_desk/book.py, veteran_desk/desk.py. Tests: veteran_desk/tests/test_gate_stale_crypto_hkd_20261029.py (ISRG + sell-limit mirror + stop_limit through). Backups: book.py.bak-20261029-1008-nochase, desk.py.bak-*-nochase*. Deployed between runs; seats stay within 1% until live.

## 27 Oct 2026 19:44 WITA: London hunt48 cost_R_max 0.15R → 0.30R (London only) + matching clearance T1 hurdle
Source: Brett 19:44 WITA chat approving the LSE stamp-duty caveat from the 19:14 build ("raise the London book cost cap to 0.30R (and the matching clearance threshold). London only. Keep 0.15R for every other book").
1. **Engine.** `asia_markets.MARKETS["lse"]["cost_R_max"] = 0.30`. `asia_hunt48.cost_R_cap(mkt|trade)` reads it; default `COST_R_MAX = 0.15` for jp/hk/sg/kr/tw (and ASX via its own module, untouched). `cost_gate` benches only when `cost_R > cost_R_cap`. Status JSON exposes `cost_R_max`.
2. **Clearance.** For `--book lse`, the "costs eat >X% of T1 move" hurdle rises from 25% to `cost_R_max / T1_R` (= 0.30 / 0.45 ≈ **66.7%**), so a real London setup at ~0.25–0.28R can PASS. Every other book (ASX, US, Asia) stays at **25%**.
3. **Why.** UK stamp duty 0.5% on buys made every LSE long cost_R ≥ ~0.18–0.28 against the old 0.15R cap, so the book was permanently flat. Cap lift is London-only; shorts FTP still untouched.
4. **Unchanged.** A$2.5k risk/trade, max 3 open, 1 per sector, T2 1.5R, room_R ≥ 1.0R, stop on every trade, same-day clearance before open, demoted never re-armed, PAPER only, no broker endpoints.
5. **Backups / tests.** `backups/lse-cost-cap-030-20261027-194426/`. `tests/test_lse_hunt48.py` (15), plus asia/asx/room/clearance/warroom suites OK. War Room card shows `cost ≤0.3R`.

## 13 Oct 2026 06:20 AEDT: OANDA practice sleeve, remaining audit fixes (Brett: TFO full authority on the paper sleeve)
Source: research/oanda_audit_20261013.md (#3, #9, #10) and GPT's medium findings in research/oanda_audit_20261013_gpt.md (N07, N08, N12, N13). Code: oanda_sleeve.py, oanda_risk.py, oanda_designs.py. GPT review: BLOCK rounds 7-10, PASS round 11 (research/reviews/oanda_remaining_20261013/).
1. **Signal exits are not sent into the rollover spread (#3).** A signal exit closes right away only if the live spread is within the entry limit (min(3x the median daily spread, 10bp)). Otherwise it is written to `state/oanda/pending_exits.json` (`exit_deferred`). `check()` retries it every 15 min and forces the close at the next 08:05 Europe/London. Broker stops stay active the whole time. If the trade is gone (stopped/closed), the intent is dropped. A re-request keeps the earliest deadline. A reversal on that pair, and any entry blocked by slots, caps or margin while an exit is pending, is deferred (not dropped) until the exit fills or the entry's normal 20h expiry. Pending-exit trades don't count toward queue slots/caps; actual-position caps are still enforced at order time.
2. **OXF1 trail (#9).** It uses the closes of bars that END after entry (a bar's time is its open; it ends 24h later), seeded with the entry fill.
3. **Volatility (#10).** One constant, VOL_DAYS = 252. Sizing vol is the blueprint's zero-mean EWMA arithmetic vol (lambda = exp(-ln2/60)). TSMOM normalisation uses log EWMA vol. The shadow EWMA covariance needs at least 252 synchronised rows to seed (otherwise none).
4. **NAV recon (N07).** The account summary is read first, and TRANSFER_FUNDS flows are counted only up to its lastTransactionID, so NAV and flows share one watermark. The checkpoint can be recovered from the previous recon row (watermark_txn, transfer_ids). The transfer checkpoint is written only after the recon row, so a failed write cannot turn a deposit into a return.
5. **Missing financing (N08).** No carry entry and no cost-gate pass without a valid side-specific OANDA financing rate (`skip:no_financing_data`). Unknown funding is not zero.
6. **Shadow coverage (N12/N13).** An exposed currency with no factor history makes the shadow vol bound unknown (a_star 0, `vol_unavailable`). The same applies when all covariances are missing, and an unavailable raw vol counts as a breach. The constant-0.5% series is labelled as normalised closed-trade attribution.
Backups: backups/oanda_remaining_20261013_0608/. Tests: new tests/test_oanda_remaining_20261013.py. Instrument fixtures in older tests now carry explicit zero financing.

## 13 Oct 2026 06:05 AEDT: OANDA practice sleeve audit fixes (commits 19891eeb, d48712dd; GPT review PASS)
Source: research/oanda_audit_20261013.md + research/oanda_audit_20261013_gpt.md. Brett gave TFO full authority over the paper sleeve.
1. **OXF1 entries need a fresh crossover (#1).** An entry needs a fresh EMA20/60 cross on the last completed bar and at least 300 bars. Exits still use the EMA state.
2. **Conversion and sizing (#5, #4).** No conversion factor means no entry. Sizing and margin use OANDA's homeConversions accountLoss for the quote currency.
3. **Stops (#7).** A missing stop is restored from ATR, or from the last confirmed ledger stop when ATR is unavailable. A restore stop or trail already through the market closes the trade.
4. **State files (#8).** pending.json, last_daily.json and pending_exits.json are written atomically. last_daily is saved after run_queue, and only when every exit and trail succeeded (otherwise `management_incomplete`, and the next daily run retries). Same-bar queue items made under the current rules are preserved.
5. **Risk caps.** Caps charge the R frozen at entry (actual-fill R when logged, including recovered orders), scaled only by remaining units. A trailed stop no longer releases budget. Caps are rechecked after each fill (`post_fill_cap_breach`, log only).
6. **Orders.**
   - One deterministic client id per decision, checked at the broker before any POST.
   - Entries use positionFill OPEN_ONLY, and are skipped if another owner holds the pair.
   - A stop retry never resends a stop looser than the broker's fresh read-back.
   - A cancelled close is not an exit.
7. **Bar freshness.** Entries require the bar of the expected NY session. Queued entries whose bar is no longer current are dropped.
8. **Realised P&L (#12).** Realised P&L = broker realizedPL + financing over all closed TFO trades (paged) plus open-trade realised/financing, counting each trade once. If the broker can't be read, new entries are blocked.
9. **Dashboard (#13).** AEDT time, data age and a STALE flag, book x/8 vs OXF1 x/4, and live vs shadow labels.
10. **Scale gate.** The 6 trades closed by the 'score None' defect are flagged `excluded_bug`. They are kept, not counted, and the frozen rules are unchanged. oanda_scale_gate.py, SCALE_GATE_FROZEN.json and the live config.json are now committed.
Backups: backups/oanda_audit_fixes_20261013_0543/. Tests: tests/test_oanda_audit_fixes_20261013.py (33). All OANDA suites pass.

## 12 Oct 2026 21:41 AEDT: OANDA practice sleeve widened, risk caps added, None score no longer closes, frozen scale-up gate (Brett 2026-10-12 21:41)
Source: Brett, 12 Oct 2026 21:41 AEDT. Keep risk at about 0.5% of equity per trade, widen the desk to build 30-50 trades quickly, and scale size only after the edge holds up after costs. Scope: `oanda_sleeve.py`, `oanda_designs.py`, new `oanda_scale_gate.py`, `state/oanda/config.json`. Practice account 101-011-39617916-001 only. Brett's trades 61365 and 61368 and all untagged trades are untouched. KDESK is untouched.
1. **Universe.** New entries use 18 pairs (`P18`): the 7 majors (EUR_USD, GBP_USD, USD_JPY, USD_CHF, AUD_USD, NZD_USD, USD_CAD) and 11 liquid crosses (EUR_JPY, GBP_JPY, AUD_JPY, CAD_JPY, CHF_JPY, EUR_GBP, EUR_CHF, EUR_AUD, GBP_CHF, AUD_CHF, AUD_NZD), plus OXL1 XAU_USD. NOK/SEK and thin crosses are dropped for new entries. Any trade already open on a dropped pair is still exit-scored on the old 32-pair list. The only built live setups are OXL1 and OXL2, and both FAILED the desk gate. OX-L3 (carry x trend) is a subset of OXL2 and would add no new trades, so it is not wired in.
2. **Risk.** 0.5% of NAV per trade for every design (OXL1 cut from 1%). Rail: `cfg()` caps risk at the level the frozen scale gate allows (0.5% unless it passes). max_trades rail goes from 4 to 8. OXL2 slots go from 3 to 8.
3. **Per-currency net cap 1.5% NAV.** Open risk for each trade = entry risk_aud scaled by (current broker stop distance / entry stop distance), and 0 once the stop is at or past entry. Long pair = +base / -quote. A new entry is refused (`entry_cap_block`) if it pushes any leg's |net| above 1.5%. This is enforced both in the daily queue build and in `place_entry`.
4. **Portfolio open-risk cap 4% NAV** (sum of the open risk above), enforced in the same two places.
5. **None score fix.** Root cause of the 12 Oct 08:05 AEDT closes of AUD_JPY 61636, USD_JPY 61639 and GBP_JPY 61642 ("signal flip/flat (score None)", all small winners, +A$111.52 in total): `score_for` reused the ENTRY function `_carry`. That function returns None for any of four reasons: no candles, no rate, |diff| < 1.5%, or OANDA's live quoted financing < 0.5%/yr. The rate diffs were unchanged (+3.05/+2.36/+2.25%, and the same OECD rates are in the cache now). The run fired at Sun 17:05 NY, right at the weekly rollover, when OANDA intermittently quotes 0/0 financing (a known issue in the 4 Oct report), so the guard returned None and daily() treated None as flat. Fix: the exit score (`_carry(for_exit=True)`) ignores the financing guard (an entry filter only) and returns None only when inputs are missing. Valid inputs give the signed diff, or 0.0 for a real flat (|diff| < 1.5%). In `daily()`, None now logs `signal_unknown` and HOLDS the trade (the stop still trails). Only 0 or an opposite sign closes. Exceptions in scoring are treated as None.
6. **401s (12 Oct 09:01-09:06 AEDT).** OANDA-side "Insufficient authorization" on every endpoint for about 5 min, the same pattern as the 5 Oct blip. The token and account were unchanged, and the 09:17 check placed AUD_CHF, USD_CHF and GBP_CHF normally. The bounded read retries worked as designed. The 09:01 check and 09:05 daily runs exited with an error and no orders were sent. No code change.
7. **Frozen scale gate** (`state/oanda/SCALE_GATE_FROZEN.json`, read-only, sha256 b6a6c71b..., never rewritten; `oanda_scale_gate.py`). To step 0.5% -> 0.75%: >= 30 closed TFO trades, >= 4 currencies, net > 0 after costs (OANDA realizedPL with the spread embedded in fills, plus trade financing), and net > 0 without the single best trade. To step 0.75% -> 1.0%: the same tests on a FRESH >= 30 trades opened at >= 0.75%. Never above 1%. A missing or tampered file means 0.5%. `daily()` refreshes `scale_gate_status.json` (read-only GETs). At freeze: 6 closed trades, net +A$185.96, so not passed and risk is 0.5%.
Backups: `backups/oanda_widen_caps_20261012_2143/`. Tests: new `tests/test_oanda_widen_caps_20261012.py` (18). The existing tests pass: api_retry 36, entry_quality 14, risk_audit 31, research/behaviour. `test_oanda_sleeve_gate` baseline is updated 500 -> 250 (OXL1 now 0.5%). Dry run 21:47 AEDT: queue AUD_JPY, EUR_AUD, GBP_JPY. USD_JPY was skipped by the USD cap (+1.80%). EUR_CHF, USD_CAD and AUD_NZD were skipped by the 4% cap (open risk is 2.3% incl. XAU 61591 at its old 1% sizing).

## 10 Oct 2026 06:12 AEDT: entry-engine B grade = half clip ($75k); A = $150k (Brett 2026-10-10 06:12)
Source: Brett 2026-10-10 06:12: B grade = half clip ($75k), A = $150k.
Scope: NEW entry-engine auto-fire clips only (`research/entry_engine/auto_fire.py`, themes `entry_engine_equity` / `entry_engine_crypto`). Open positions and every other caller of `tfo_buy` / `tfo_crypto` unchanged. `risk_caps` entry-engine theme cap stays the A rule ($150k / $7,500); B is enforced by requesting the half-sized clip (tools may size down, never up).
1. **Engine tickets (`entry_engine.make_ticket`).** Grade A: `min($150k, $7,500/stop, clip cap, %vol/ADV)` as before. Grade B: same formula with target $75,000 and planned-loss $3,750 (half). Equity still sizes at the -5% V7 rail; crypto at the coil stop (desk tool re-sizes at C3).
2. **Auto-fire (`auto_fire.grade_request_usd`).** Requests the ticket `clip_usd` (grade-sized). If a B ticket still carries an A-sized clip above the $75k B target, halves it and caps at $75k. Decision rows in `auto_fire.jsonl` log `usd`, `size_note`, `grade_target_usd`, `grade_planned_loss_usd`.
3. **Unchanged.** Heat, powder+fees, vol/ADV, spread, shark max, C3, theme cap (2), 3/day, 30 min spacing, A/B-only, chase, veto, kill switch, firm halt.
Backups: `backups/entry_engine_b_half_20261010_0612/`. Tests: `research/entry_engine/test_auto_fire.py`, `research/entry_engine/test_entry_engine.py`, `tests/test_entry_engine_150k_20261009.py`.

## 9 Oct 2026 21:10 AEDT: entry-engine auto-fire clips target $150,000 (Brett 2026-10-09 21:10)
Source: Brett (funder), 9 Oct 2026 21:10 AEDT: raise the main book's entry-engine auto-fire clips (top 3 A/B picks per day, paper) to $150,000 per trade.
Scope: NEW entries tagged theme `entry_engine_equity` / `entry_engine_crypto` only (i.e. `research/entry_engine/auto_fire.py`). Every other caller of `tfo_buy.py`, `tfo_crypto.py buy`, `tfo_watchlist.py` and overnight2k keeps the TFO Risk audit cap ($5,000 / (stop + 0.5% slip), ~$90.9k at -5%). Open positions untouched.
1. **Rule (`risk_caps.py`, `ENTRY_ENGINE_*`, `theme_params()`).** clip = min(requested, $150,000 target, $7,500 / actual stop). The $7,500 planned loss is measured AT the stop: equity -5% V7 rail -> $150,000 (planned loss incl. the 0.5% slip allowance ~$8,250, reported but not added to the cap). Crypto: risk = whatever makes $150k at its C3 2xATR stop, capped at $7,500, so a wide stop sizes down (ETH 7.5% stop -> ~$99.7k; XBT 5.6% -> ~$134.8k at 21:30). Rule 2 (ATR > stop size-down) and the $5k minimum/skip are unchanged. Rule id `BRETT_ENTRY_ENGINE_150K_20261009`; ticket `risk_cap` records `planned_loss_at_stop_usd`, `target_usd`, `loss_basis`.
2. **Wiring.** `tfo_buy.risk_size(usd, theme)` and the `execute()` refusal guard use the theme cap; `main()` passes `--theme`. `tfo_crypto.risk_size(..., theme)` / `cmd_buy`. `auto_fire.LiveDeps.equity_presize` sizes with theme `entry_engine_equity`.
3. **Unchanged gates.** 5% firm heat cap, unreserved powder + fees, 5% of day dollar volume (equity) / 5% of 24h $vol (crypto), engine 1% ADV for equity tickets, spread < 20bp and quote consistency, >$300k mega-liquidity tier, shark max clip (0.5% firm risk to -5% = 10% of firm equity, ~$180k today), C3 1% firm risk, crypto stack/clip caps, theme cap (2 clips), DD and Sydney-day freezes, auto_fire 3/day and >= 30 min spacing, A/B grade, chase and veto checks.
4. **Engine ticket display (`entry_engine.make_ticket`, `config.json`).** Tickets show min($150k target, $7,500 / stop, cap, %vol/ADV). Equity tickets size at the desk's -5% rail (`equity.ticket.sizing_stop_pct` 5.0), so they show $150,000 / $7,500 planned loss; crypto tickets use the coil stop and the desk tool re-sizes down at its C3 stop.
Backups: `backups/entry_engine_150k_20261009_2110/`. Tests: new `tests/test_entry_engine_150k_20261009.py` (21); `research/entry_engine/test_auto_fire.py` presize fixtures 90,900 -> 150,000. Revert: restore the backup files (or make `risk_caps.theme_params()` return `{}`).

## 5 Oct 2026 12:20 AEDT: OANDA practice sleeve stops count only when the broker confirms them; broker/ledger stop reconcile
Source: the 11:41 desk driver scorecard reported "intermittent OANDA 401s" and said the XAU_USD trail to 4319.80 existed only on our side while the broker held 4323.57.
1. **The 401s were one OANDA-side blip.** Since 08:00 AEDT there were exactly 3 responses, all within one minute: 09:05:05 `GET /trades` (daily reconcile), 09:05:11 `GET /summary` (daily run, exit 1) and 09:05:55 `GET /trades` (check reconcile). Since the retry change at about 09:10 there have been none: `state/oanda/api_retries.jsonl` has never been created, and every check run has exited 0. `GET /v3/accounts` on the practice host with the same token lists exactly `101-011-39617916-001`. The token is valid and has the right account access, so Brett doesn't need to do anything.
2. **The XAU "trail" was my 09:10 dry run.** The line was `stop_update 4319.8035 live=false`, and dry runs used the live event name. It was never sent, and per the design it shouldn't be: trails run only on a NEW daily close in a live daily run. The 08:05 run had already processed the 2026-10-01T21:00Z close before trade 61591 opened at 09:05:59, so its first trail is the Tue 6 Oct 08:05 AEDT run, using Monday's close. Broker vs ledger at 12:16, read-only: all 4 TFO trades match, with no broker trailing stops: 61597 GBP_CHF 1.08558, 61594 USD_CHF 0.81878, 61591 XAU_USD 4323.568, 61587 NZD_CHF 0.45923. Ledger fixed to match the broker: a `stop_reconcile` line was appended for 61591. The old line was kept for audit. Nothing was sent to the broker.
3. **Code (`oanda_sleeve.py`):**
   - `set_stop` is now tighten-only against the trade's current broker stop. A looser stop is refused and logged `stop_update_refused`.
   - `stop_update` with `confirmed=true` is written only after the broker shows the new stop: a trade read-back, or the PUT response's `stopLossOrderTransaction` if the read-back can't be done. Otherwise it is logged `stop_update_failed` or `stop_update_unconfirmed`. Dry runs log `stop_dry`.
   - New `reconcile_stops()` runs in every check and daily run. The broker is authoritative, so the ledger is corrected to it (`stop_reconcile`). The one exception: if the broker shows a stop looser than one it previously confirmed, the confirmed stop is pushed again (tighten-only, confirmed). A missing stop goes to the existing restore path. Results are written to `state/oanda/stop_reconcile.json`. Trades 61365 and 61368 and untagged trades are never examined.
   - `oanda_panel.py` hides unconfirmed and dry `stop_update` lines.
Backups: `backups/oanda_stop_confirm_20261005_1215/`. Tests: `tests/test_oanda_api_retry_20261005.py` now has 36 (adds stop confirmation and reconcile, including a replay of the XAU dry line). In two existing tests, the dry-stop expectation changed from `stop_update` to `stop_dry`: `tests/test_oanda_entry_quality_20261005.py` 14/14 and `research/behaviour/test_oanda_sleeve_gate` 15/15. `tests/test_risk_audit_20261004.py` 31/31 and all of research/behaviour 107/107 pass. Dry run 12:16 AEDT exited 0: no retries, no stop mismatches, no orders.

## 5 Oct 2026 09:10 AEDT: OANDA practice sleeve API retry (09:05 daily run failed on a transient 401)
Source: TFO Behavior flagged that launchd `com.tfo.oanda-daily` exited 1 at 09:05 AEDT (Sun 22:05 UTC). `GET /summary` returned HTTP 401 "Insufficient authorization", before the behaviour gate. Diagnosis: no local defect. Both jobs run the same python, working directory and PATH, with no `OANDA_PRACTICE_TOKEN` in the launchd or shell environment. Both read the same token file, `~/.tfo/oanda_practice_token` (65 bytes, stripped, unchanged since 4 Oct 19:10). The host (practice) and account `101-011-39617916-001` are hard-coded. The 401 hit two jobs and two endpoints (`/trades` at 09:05:05 and `/summary` at 09:05:11 in daily; `/trades` at 09:05:55 in check). The same token authenticated about 5 s later (09:05:59 check entries) and in re-tests at 09:09. So this was a transient rejection on OANDA's side lasting under 1 min, not an expired or invalid token. The real weakness was that `api()` treated 401 as fatal (it only retried 5xx and URLError), and it also re-sent writes, including order POSTs, on 5xx.
Nothing was missed. 09:05 is the DST backup slot: the 08:05 run had already processed the latest close (2026-10-01T21:00Z), so 09:05 would have exited with "already processed". The 09:06 check placed the 3 queued entries (XAU_USD 61591, USD_CHF 61594, GBP_CHF 61597), and `pending.json` is empty. All 4 TFO trades have broker stops. No TFO trade closed, so no stop fill went unreconciled. Brett's 61365 and 61368 were untouched.
Change (code only; no orders, closes or stop changes were sent):
1. **Reads (GET) are retried and bounded.** 401, 429, 5xx, timeouts and connection errors get 3 tries with 5 s and then 15 s waits (~20 s). Other 4xx errors are not retried. The token is re-read on each try. Each retry is logged to stderr and `state/oanda/api_retries.jsonl`. After 3 failures the run still fails loudly (exit 1).
2. **Writes are never auto-retried.** Entry POST: after any error, the order is looked up by client id (`GET /orders/@<cid>`, then open trades with that client id). If it filled, it is recorded as filled. If it was cancelled, that is reported. If it is confirmed absent after a transient error, the result is `defer:order_not_placed` and the entry stays queued, so the next scheduled run re-prices it and re-runs every gate. If the state can't be read, the result is `error:unverified` and the order is NOT resent. Close and stop update: the trade is read back first. If it is already closed, nothing is resent and the event is `close_already_closed` when the trade was closed by a broker stop (so it isn't double counted). If the stop is already at target, nothing is resent. A single resend happens only if the error was transient and the trade is still OPEN. Protected trades and non-TFO trades are still refused before any call.
Backups: `backups/oanda_api_retry_20261005_0908/` (oanda_sleeve.py, RULE_LOG.md, config.json, pending.json, last_daily.json, snapshot.json, trades.jsonl). Tests: new `tests/test_oanda_api_retry_20261005.py` (22, offline), which includes a 09:05 replay. Existing suites pass: `tests/test_oanda_entry_quality_20261005.py` 14, `research/behaviour/test_oanda_sleeve_gate` 15, `tests/test_risk_audit_20261004.py` 31, all of `research/behaviour` 107. Dry run 09:10 AEDT with the launchd environment exited 0: no queue (4/4 TFO slots full), no retries needed, no orders.

## 5 Oct 2026 08:40 AEDT: OANDA practice sleeve entry-quality rails (execution bot fill-quality review)
Source: execution bot fill-quality review, 5 Oct 2026. NZD_CHF (trade 61587, OXL2 long) filled at Sun 4 Oct 17:05 New York (Mon 08:05 AEDT), 5 min after the weekly FX reopen, at a spread of about 26bp. The old gate (3x the median daily-close spread, about 37bp for NZD_CHF) let it through. Authority: TFO, which has full authority on the OANDA practice sleeve. Scope: NEW entries in `oanda_sleeve.py` only (`daily` and `check` both place entries through `run_queue` -> `place_entry`; launchd `com.tfo.oanda-daily` / `com.tfo.oanda-check`). Open trades, broker stops, chandelier trails and exits are untouched. Trade 61587 is left as is. Brett's trades 61365 (SPX500) and 61368 (NAS100) are untouched.
1. **Weekly-reopen blackout.** No new entry from the Friday 17:00 NY close until 60 min after the Sunday 17:00 NY reopen (no entry before Sun 18:00 NY = Mon 09:00 AEDT while NY is on EDT, Mon 10:00 AEDT once NY is on EST). Times are computed in NY wall-clock (zoneinfo, with a US-DST-rule fallback). Queued entries are deferred and kept, not dropped, and logged `entry_deferred` with reason `defer:reopen_blackout` / `defer:fx_weekend_closed`. The behaviour gate is not called during the blackout. Code: `reopen_blackout()`, used in `run_queue()`. Config `reopen_blackout_min` (default 60; the rail floors it at 60).
2. **Hard spread cap.** Entry spread must be <= min(3x the median daily-close spread, 10bp), whichever is tighter. Code: `spread_limit()`, used in `place_entry()`. Config `spread_cap_bp` (default 10; the rail caps it at 10, so it can only be tightened). NZD_CHF's limit is now 10bp (was 37bp). USD_CHF goes from 11.3 to 10bp, GBP_CHF from 18.7 to 10bp, and XAU_USD stays at 6.9bp (the 3x rule binds).
Backups: `backups/oanda_entry_quality_20261005_0836/` (oanda_sleeve.py, config.json, pending.json, RULE_LOG.md). Tests: `tests/test_oanda_entry_quality_20261005.py` (14, offline). The existing `research/behaviour` tests (78) and `tests/test_risk_audit_20261004.py` (31) still pass. Dry run 08:37 AEDT: all 3 queued entries (XAU_USD, USD_CHF, GBP_CHF) were deferred with `reopen_blackout 37min`. No orders, closes or stop changes.

## 4 Oct 2026 21:30 AEDT: TFO Risk audit, Head Trader adopted
Source: research/deep19/RISK_AUDIT.md (TFO Risk, 4 Oct 2026, post-trade proposals for week 28 Sep to 4 Oct). Scope: MAIN TFO desk entry paths only:
shark/equity entries (`tfo_buy.py`), HT crypto clips (`tfo_crypto.py buy`), the pre-open hunt / watchlist fires (`tfo_watchlist.py`, which routes through those two tools) and overnight2k clips (`research/overnight2k/overnight2k.py`).
NOT the Parallel Desk (Brett set its rules explicitly; clips <= $40k, planned loss <= ~$2k) and NOT trend18 (separate approved sleeve with its own kill rule). Existing open positions are untouched; rules apply to NEW entries.

1. **Planned-loss cap per name (adopted; equities and crypto).** clip = min(existing size, $5,000 / (actual stop distance + slippage allowance)). Stop = the stop the position will actually carry (shark -5% rail; crypto C3 2xATR14 entry stop or V0 -5%; a tighter caller stop such as overnight2k's -5% night stop), never ATR as a proxy. Slippage allowance = max(0.5%, measured book slip). At a -5% stop the cap is ~$90.9k (equity clips $90.9k via tfo_buy, $90k via the watchlist's $1k rounding). Only ever shrinks a clip; a bound clip is floored to $100. Logged when it binds: `state/risk_caps/cap_log.jsonl`, the ticket's `risk_cap` field, a `RISKCAP` line in the tool output and an overnight2k `RISKCAP` audit line. Code: `risk_caps.py`; `tfo_buy.main/risk_size` (plus a hard refusal in `tfo_buy.execute` above the cap); `tfo_crypto.cmd_buy/risk_size` (re-gates at the sized clip); `tfo_watchlist.planned_loss_cap` (plan sizing); `overnight2k.risk_size_clip`.
2. **High-ATR coins (adopted).** If ATR14% > the stop distance: clip x stop/ATR14. If the result is below the desk minimum crypto clip ($5k) the entry is skipped (never rounded up). Overnight2k fails closed when ATR14 is unavailable. For main-desk C3 clips (stop = 2xATR14) this never binds by construction; it binds for overnight2k's -5% night stop and for any V0 / --stop-pct entry. Overnight2k passes `--stop-pct 0.05 --slip-pct <book slip> --presized` to `tfo_crypto.py buy` so the size-down is not applied twice.
3. **Crypto vetoes (crypto_vetoes.py CONDITIONAL PASS condition adopted).** Every vetoed would-be entry is logged to `state/vetoes/vetoed_entries.jsonl` (symbol, timestamp, veto reason, ref price, context); 24h/72h hypothetical outcomes (raw, max up/down, -5% stop-adjusted net of 0.52% round trip) are filled from live Kraken hourly bars (Coinbase fallback) by launchd `com.tfo.veto-outcomes` (hourly, `veto_log.py fill`). Counter `state/vetoes/veto_counter.json`: review due at 30 counted vetoes (one per symbol per UTC day; fail-closed / not-run rows are logged but not counted). Today the only main-desk caller of the veto is overnight2k (buys end Mon 5 Oct 22:00 AEDT).
4. **Label.** `shadow_value_usd` is shown on the dashboard as "hypothetical notional" (deep18/deep19 shadow panels and the 2x Nasdaq shadow). API field kept; alias `hypothetical_notional_usd` added in deep18_fwd/deep19_fwd plus `field_labels` on deep19 shadows (appears in /api/deep19_fwd after the command server next restarts; it imports the module once).
5. **Post-trade review entry price.** Entry price = the ticket fill in the exit's share basis (exit ticket principal_usd / qty), not the latest BUY's px_usd. 8766.T's 51.25 was the pre-split price (15-for-1, ex 29 Sep); corrected to 3.4165 in `state/post_trade/week_closes_2026-10-04.jsonl` with AAPL and STO.AX (same matching bug); erratum appended to `weekly_2026-10-04.md`. Helper: `post_trade_entry_px.py`.
Not adopted (TFO Risk FAIL): 48-72h post-stop cool-down (no evidence it would have prevented a loss); tighter crypto stale_flat (already in code).
Backups: `backups/risk_audit_20261004_2130/`. Tests: `tests/test_risk_audit_20261004.py`.


## 2026-10-06 08:29 AEDT - PARALLEL DESK: post-only maker execution on Kraken (Brett: "Yes to all")
Kraken clip entries = post-only limit buys at the live bid (maker 0.12%, 15-min TTL, cancelled if unfilled); +2% half / +5% flat = resting post-only limit sells at the rail price (maker 0.12%); -5% stop unchanged = market sell at the live bid (taker 0.22%). US stocks unchanged. Files: research/parallel_desk/{parallel_desk.py, pd_rails.py, parallel_desk_web.py, test_parallel_desk.py, test_pd_rails.py, README.md}; log state/parallel_desk/rule_changes.jsonl; order events state/parallel_desk/orders.jsonl. Revert: CRYPTO_EXEC = "taker". Backups: backups/parallel_desk_maker_20261006_083532/. Tests 62/62 OK; live dry-runs of both runners clean.

## HT 2026-10-10 10:21: stale-tape fix
Cause: auto_fire skipped crypto candidates when tfo_crypto.quote() said tape != "LIVE", i.e. last Kraken trade (Trades count=1, correct pair, epoch maths fine; no tz/cache/pair bug) >= 60 s old. The engine itself allows 15 min, so thin coins at quiet hours (AAVE 03:11 AEDT, trade 149 s old; NIGHT 04:49/05:00 AEDT, 71 s/77 s) were skipped as STALE. Threshold only, not a data bug.
New entry rule (auto_fire.entry_tape + tfo_crypto.entry_tape, BUY gate only; rails/exits still need < 60 s): BAD never; LIVE (< 60 s) as before; day $vol >= $1M: last trade <= 3 min OK (trigger tested on last), or last trade 3-15 min old + book fetched in this quote (<= 15 s) uncrossed with spread <= 20 bp -> trigger tested on the book mid; > 15 min or $vol < $1M -> refuse. Fill/chase check unchanged: max(last, ask) <= trigger +0.5%.
Replay (no orders): all three STALE skips would now pass the tape check but still skip "trigger not reached" (last below trigger). Files: tfo_crypto.py, research/entry_engine/{auto_fire.py,test_auto_fire.py}. Backups: backups/stale_tape_fix_20261010_1021/. Tests: entry/auto_fire/equity 103/103 OK.

## 12 Oct 2026 22:40 AEDT: OANDA practice sleeve, GPT blueprint (Brett has full authority on this paper sleeve; wants more trades from wider coverage, not bigger size)
Source: GPT blueprint, summarised in research/oanda_gpt_blueprint_20261012.md. Formulas: research/oanda_formulas.md (new). New maths module: `oanda_risk.py`.
**LIVE:** (A) every entry logs a frozen `r0_aud` (|units| x D x c_quote). Open-risk caps now charge R0 when it is present. `state/oanda/net_r.json` records net R = (realizedPL + financing)/R0 for every closed TFO trade. Legacy trades use risk_aud and are flagged. (B) Wilder ATR20 replaces the SMA ATR for stops and trails. Candles are explicitly dailyAlignment=17 America/New_York. Units are floored to tradeUnitsPrecision and rejected below minimumTradeSize. (C) units = min(0.5% risk ceiling, 3% per-position vol cap). The binding constraint is logged (`sizing_binding`, `vol_cap_note`). (E) cost gate: spread <= 0.05 x ATR20, and (spread + 0.5 x spread slippage + financing debit over 30 days)/R0 <= 0.10. Credits are never counted. Blocks are logged as `entry_cost_block`. Rails: vol cap <= 3%, cost <= 0.10, spread/ATR <= 0.05. (H) `nav_daily.jsonl` (one row per UTC day), `financing.jsonl` (DAILY_FINANCING via transactions/sinceid), `instrument_meta/YYYY-MM-DD.json`, plus instrument_meta on every entry line. (F) new frozen paper design **OXF1**: EMA20/60 daily trend on 20 pairs plus XAU_USD at 0.25%. Entries are queued with not_before = the next 08:05 London. Stop at the executable quote ± 2 ATR20. Trail at the highest/lowest close since entry ∓ 3 ATR20, tighten-only. Exit on the opposite crossover. No re-entry in the same regime after a stop-out. No TP, no time exit. It shares max 8 trades, the 1.5% net and 4% total caps, margin, spread, blackout, behaviour and cost gates. One position per pair: OXL1/OXL2 take queue priority. Exits now follow each trade's own design (clientExtensions comment). Enabled in state/oanda/config.json `designs`.
**SHADOW (never blocks):** (D) GPT caps (net 1%, gross 1.5%, total initial 2%, |e_c|<=E, gross notional <=3E, 8% EWMA-vol with lambda 0.988514 and gamma 0.30, batch a). Logged per daily run as `shadow_gpt_caps`, with a running count in `state/oanda/shadow_gpt_caps.json`. (G) TSMOM 20/60/120 + 0.2 carry/vol score goes to `shadow_scores.jsonl`. (I) the constant-0.5% track is `shadow_05_aud` in net_r.json.
**NOT CHANGED:** live caps 1.5% net / 4% total / 8 trades; the frozen scale gate (sha verified by test). GPT's 24/36-month scaling review is written up as a proposal: research/oanda_scaling_proposal_20261012.md.
Backups: backups/oanda_gpt_blueprint_20261012_2240/. Tests: new tests/test_oanda_gpt_blueprint_20261012.py (26). Existing OANDA suites 36 + 14 + 18 + behaviour gate 15 all pass. One existing test was updated for the RISK dict and the score_for(design, …) signature. Dry run 22:5x AEDT (launchd python) exit 0: 3 OXL2 entries dry-placed, all risk-bound, cost_r 0.015–0.029. All 15 OXF1 candidates were skipped by the live 4% open-risk / 1.5% USD caps. GPT's caps would have blocked 3/3, because the current book is already about 1.5% net short CHF.

## 13 Oct 2026 04:31 AEDT: OANDA OXF1 trend sleeve gets its own risk budget (Brett)
Why: in the 12 Oct dry run, the live 4% open-risk cap and the 1.5% USD cap blocked all 15 OXF1 candidates.
**Book (OXL1/OXL2), unchanged:** 4% total open risk, 8 trades, 1.5% per-currency net.
**OXF1, new and separate:** 2% total open risk (`trend_open_risk_cap_pct`), up to 4 trades (`trend_max_trades`; designs.MAXSLOTS OXF1 8 -> 4), 0.5% per trade (gold 0.25%), and its own 1.5% per-currency net cap (`trend_ccy_net_cap_pct`) computed over OXF1 positions only. Total desk open risk can now reach 6%. All four are rails in cfg() and can only be set tighter. Margin (50%), spread, blackout, behaviour and cost gates and the frozen scale gate are still shared and unchanged.
**Combined check (log only, never blocks):** the book + OXF1 per-currency net is written to `state/oanda/combined_ccy.jsonl` after the daily queue and before every entry. If any |net| > 2.5%, `combined_ccy_warning` is also written to trades.jsonl.
**Same pair:** kept at ONE position per pair across the whole desk. This is the safer option: the account nets positions, so a second same-direction trade would just double the pair's risk, and an opposite trade would net off the first. OXL1/OXL2 keep queue priority.
**Unchanged:** GPT shadow caps logging (`shadow_gpt_caps`), the TSMOM score shadow, the constant-0.5% shadow track, and the launchd schedules.
Files: oanda_sleeve.py, oanda_designs.py, state/oanda/config.json (4 new keys). Backups: backups/oanda_trend_budget_20261013_0431/. Tests: new tests/test_oanda_trend_budget_20261013.py (17). Existing suites 36 + 14 + 26 + 18 + behaviour gate 15 all pass (126/126).
Dry run ~04:33 AEDT, launchd python, exit 0, no live orders: 3 OXL2 entries were dry-placed. 4 OXF1 entries were queued (EUR_USD, GBP_USD, AUD_USD and EUR_GBP, all short), held until 08:05 London (18:05 AEDT). NZD_USD was skipped by the OXF1 USD 1.5% cap. The combined USD net after the queue is +2.8%, so a warning was logged.

## 13 Oct 2026 04:50 AEDT: OANDA practice sleeve, GPT blueprint Part C (source: research/oanda_gpt_blueprint_full_20261013.txt, lines 759-1045)
**LIVE (affects entries):** (C1) one decision per completed 17:00 NY bar per instrument + strategy version (`state/oanda/last_processed_bar.json`, key `VERSION|ins`; designs.VERSION). A bar older than the latest close never trades (`bar_stale_skip`), and a retry never re-decides (`duplicate_decision_skip`). Quote health: a stale quote (> 120 s, `quote_max_age_s`), a missing quote time or a failed pricing snapshot defers the NEW entry only (`quote_health_block`). Broker stops are untouched. Entry records now carry requested_units vs filled_units, decision/quote/submit/fill times, broker_transaction_id, strategy_version and decision_id. (C4) the protective stop is rounded OUTWARD to the instrument's price quantum using Decimal, and the order is sized from the actual rounded distance. (C5) DAILY_FINANCING is booked once per broker transaction id.
**LOG ONLY:** `state/oanda/nav_recon.jsonl` writes one row per day: broker NAV, TRANSFER_FUNDS external flows (booked once each, never counted as returns), flow-adjusted r_NAV, a balance+uPL-NAV cash check, and the constant-0.005 shadow series (from net_r.json).
**SHADOW (never blocks):** (C2) a closed-form a_star replaces the old bisection. It takes the minimum of the vol roots (raw AND shrunk EWMA, 8%, 252 days), the signed net-currency (1%) and currency-notional (|e_c| <= E) bounds, gross currency (1.5%), total stop (2%) and gross pair notional (3E). It is logged in `shadow_gpt_caps` with every bound, the limiting constraint and the same batch's a_star on an empty book. (C4) the integer-unit rounding fallback (recheck the rounded batch, drop failures, halve a) is also logged there.
**Versioning:** the Part B baseline (OXF1 `partB-ema20_60-v1-20261012`) and Part A TSMOM+carry (`partA-tsmom_carry-v1-20261012-shadow`, shadow score only) are tagged separately and never combined. Live caps, the trend 2% budget and the scale gate are unchanged.
Backups: backups/oanda_partC_20261013_0434/. Tests: new tests/test_oanda_partC_20261013.py (18), which includes the exact C3 worked numbers. All OANDA suites pass, 144/144.

## 16 Oct 2026 ~21:00 AEDT - OANDA PRACTICE sleeve v3 (Brett 20:49 AEDT: widen universe, add setups, raise utilisation)
- Audit (broker realizedPL + financing, A$): 9 closed TFO trades, all OXL2 carry: 5 wins / 4 losses, net -A$375.26, avg -0.16R. OXL1 gold short open; OXF1 no trades yet.
- Backtest (work/oanda_v3/sim.py, OANDA daily BA candles 2010-26, 85 instruments, spread+slippage costs, financing NOT modelled): trend pullback, vol-filtered 55d breakout
  and carry-aligned trend all LOST on FX (-0.04 to -0.26R/trade) and on equity indices; only metals+energy were positive in both halves (pullback +0.10/+0.09R, breakout +0.55/+0.24R).
- Deployed: OXP1 (commodity pullback) and OXB1 (commodity breakout) on XAU/XAG/XPT/XCU/BCO/WTICO (oanda_setups.py). New 'cmdty' bucket: 0.75% risk/trade (own rail,
  not the frozen scale gate), 2.5% bucket open risk, 4 trades, group caps METALS 1.5% / OIL 0.75% (counting every bucket's rows), USD net 2.5%.
  New whole-sleeve ceiling: total open risk <= 8% across book (4%) + OXF1 (2%) + cmdty (2.5%). Stops: 2xATR20 on fill, 3xATR20 best-close trail, setup exit rules.
- NOT changed: book/OXF1 risk (0.5%, frozen scale gate), per-ccy 1.5% caps, cost gate, spread caps, behaviour gate, KILL/halt. FX setups not deployed (negative backtest).
- Backup: backups/oanda_20261016_pre_v3/. Tests: tests/test_oanda_cmdty_20261016.py + all prior OANDA tests pass.

## 16 Oct 2026 ~21:10 AEDT - Commodity 'big up day' ATR-scaling test (head trader decision): REJECTED, not deployed
- Proposal: for OXP1/OXB1 only, block entry only if the day's move > 1.5 x ATR20%. Test: work/oanda_v3/sim_gate.py (CM6, 2010-26, after spread+slippage).
- OXP1 per trade: current gate +0.108R (n=567) -> ATR rule +0.063R (n=636). OXB1: +0.450R (n=158) -> +0.366R (n=185). Total R also lower for both.
- Fails the keep-only-if-holds-up test. Behaviour code unchanged; WTICO OXP1 long of 16 Oct stays blocked (+2.5% day >= 2%).

## 16 Oct 2026 ~21:30 AEDT - GPT ideas (Codex CLI gpt-5.6-sol, /tmp/gptq/answer.md) validated; v4 deployed (Brett 21:03 AEDT)
- Test: work/oanda_v3/sim_gpt.py, OANDA daily BA 2010-26, spread+slippage, financing at CURRENT OANDA rates (proxy). avgR <2019 / 2019+:
  GPT1 cmdty breakout stop-entry + 55-bar exit (ex-NATGAS) +0.706/+0.233 PASS (with NATGAS +0.660/+0.215); GPT2 cmdty pullback stop-entry + 60-bar time exit +0.111/+0.049 PASS;
  GPT3 passive 63/126/252 trend broad -0.009/-0.015 FAIL (FX +0.054/-0.034, bonds -0.144/0.000, indices -0.057/-0.051 FAIL; commodities +0.083/+0.059 PASS, state-entry +0.054/+0.042 PASS);
  GPT4 index RSI2 mean reversion -0.152/-0.170 FAIL; GPT5 bond 100-bar breakout -0.844/+0.025 FAIL.
- Deployed: OXB1 v2 (GPT1, 1% risk), OXP1 v2 (GPT2, 0.75%), OXT1 passive commodity trend (XAU/XAG/XCU/WTI/Brent, 0.5%, 4xATR stop, 6xATR trail).
  cmdty bucket 4% / 6 trades, METALS 1.5%, OIL 1.0%, total sleeve <= 8% unchanged. Backup backups/oanda_20261016_pre_v4/.
- Placed: XCU_USD OXT1 long 330u @6.55347, stop 6.02833, r0 A\$251.24 (trade 61697). WTICO OXP1 blocked by behaviour gate; BCO OXT1 blocked by OIL cap.

## 16 Oct 2026 ~21:50 AEDT - AGGRO sleeve (Brett 21:35/21:36/21:37 AEDT): +5%/week HARD target on the OANDA PRACTICE account
- oanda_aggro.py, tag AGGRO, state/oanda_aggro/. Notional = WHOLE account NAV (start A$50,315.52); steady TFO trades count in its risk.
  Rails: 3% NAV risk/trade, account open risk (TFO+AGGRO) <= 15% (also enforced in the TFO caps_check), METALS 6% / OIL 3% / USD 9% net,
  margin <= 60%, broker stop on every order, weekly halt at -15% vs the FX week's opening NAV (until Sun 17:00 NY), hard kill at NAV <= 60% of start
  (flatten every TFO + AGGRO trade, KILL files for both sleeves). Brett's 61365/61368 never touched. Runs inside the existing launchd jobs (oanda_sleeve.py hook).
- Setups: daily commodity OXB1 v2 / OXP1 v2 / OXT1 only. H4 (2014-26, split 2020-06) and H1 (2019-26, split 2023) versions of breakout, pullback and
  passive trend on commodities, FX and indices ALL FAILED after costs (work/oanda_v3/sim_intra.py) -> not traded. No FX/index setup passed.
- Monte Carlo (work/oanda_v3/mc_aggro.py, 2019+ tested trades, 1.86/week, avg +0.068R, 3% risk): P(week >= +5%) 13.3%, median week -0.5%,
  mean +0.4%; 13 weeks p10 -20% / median +0.4% / p90 +37%; P(5%/wk compounded over 13 wks) 0.8%; P(-40% kill) 0.1% (iid; ignores correlation and gaps).
- Placed: BCO_USD OXT1 long 62u @108.132, stop 91.38, r0 A\$1,506 (trade 61700). Backup backups/oanda_20261016_pre_aggro/. Tests pass.

## 17 Oct 2026 ~09:50 AEDT - Entry engine crypto: post-only MAKER entries + planned exits (Brett promise via KDESK owner; TON entry paid ~$403 taker)
- Scope: tfo_crypto.py clips with theme `entry_engine_crypto` (com.tfo.entry-engine -> auto_fire -> `tfo_crypto.py buy`). Other themes/books unchanged.
- ENTRY: post-only limit resting at the best bid (never crosses), TTL 15 min. PAPER fill only when a later public Kraken print trades STRICTLY BELOW the limit (prints at the limit do not fill: queue unknown); fills at the limit with the Kraken MAKER fee (0.25% vs 0.40% taker). If a print at/below the entry's own stop (C3 stop, else -5%) comes first, the order is cancelled. Unfilled at expiry: cancelled, powder reservation returned. NO taker fallback. Powder for clip + maker fee is reserved at placement (all tfo_crypto gates/risk caps run at placement as before).
- PLANNED EXIT (+half, HALF_2PCT/HALF_15PCT): when the rail says HALF, a post-only sell rests at the ask (TTL 10 min); fills only on a print strictly ABOVE it, at the limit, maker fee; expired -> re-posted next rails run if HALF still applies.
- STOPS stay stops: STOP / trail / BE floor / max-hold (act STOP or FLAT) are taker at the bid exactly as before, and cancel any resting exit first. auto_fire coil-low guard (`sell --rule ht_flat`) unchanged and stays armed while the entry rests.
- auto_fire: a `RESTING ... post-only BUY` tool line counts as fired (3/day, 30-min spacing unchanged); resting entries count as held.
- Fee impact at the TON size (~$100k clip): entry fee ~$400 -> ~$250; planned half exit 0.40% -> 0.25%. Trade-off: some entries will not fill (missed moves) - tracked in book `resting_log`.
- Backup: backups/entry_engine_maker_20261017/ (tfo_crypto.py, auto_fire.py, reports/tfo_ht_crypto.json). Tests: new tests/test_crypto_maker_20261017.py (8) + auto_fire PostOnlyParse; test_entry_engine_150k_20261009 updated to read the resting entry. Full tests/ discover: no new failures vs the pre-change baseline (17 failures / 91 errors pre-existing, unrelated: fundamental_open_new etc.).
- Revert: copy the backup files back (or set MAKER_THEMES = set() in tfo_crypto.py).

## 17 Oct 2026 ~10:30-11:30 AEDT - Weekly cut review actions (Brett approved 10:29 AEDT; reports/weekly_cut_review_20261017.md)
- **Entry engine crypto (theme `entry_engine_crypto`), commit 99654f6d:** exits back to +2% HALF (HALF_2PCT) and the rest FLAT by +5% (FLAT_5PCT); this replaces the C3 +15% half for these clips only (other themes keep C3/V0). Minimum stop 1.5%: the rail stop is max(C3 2xATR at entry, 1.5%); sizing, heat and the C3 1% risk check use the same floored stop; auto_fire's guarded coil stop is never tighter than 1.5% below the entry reference (CRYPTO_MIN_STOP_PCT). Post-only maker entries are unchanged. Both the +2% half and the +5% flat are planned post-only maker exits. STOP, trail, BE floor and max-hold stay taker stops and cancel any resting exit. Trail, BE floor and 40-day max hold apply between +2% and +5%. Revert: `EE_THEMES = set()` in tfo_crypto.py (and CRYPTO_MIN_STOP_PCT in auto_fire.py). Tests: new tests/test_crypto_ee_exits_20261017.py (16), plus auto_fire tests (61; the coil-stop expectation was updated to the 1.5% floor, and a wide-stop case was added).
- **OANDA OXL2 carry CUT, commit 46912650:** state/oanda/config.json `entry_disabled_designs: ["OXL2"]`. daily() queues no OXL2 entries (logs `design_disabled_skip`), and place_entry refuses queued OXL2 items. OXL2 stays in `designs`, so the one open OXL2 trade (61663 GBP_CHF) keeps its broker stop, trail and signal exit and runs off. Brett's 61365/61368 and AGGRO trades are untouched, and no scheduler or launchd change was made. Tests: tests/test_oanda_oxl2_cut_20261017.py (6).
- **OANDA snapshot / firm risk, commit 27dfe472:** the snapshot owner for an AGGRO-tagged trade is now `AGGRO`, not `other (untouched)`. Only 61365/61368 read `Brett (pre-existing, untouched)`. This takes effect at the next sleeve run. firm_risk.py claims state/oanda_aggro/stats.json, so it is no longer an unclaimed ledger. It publishes `oanda_aggro` in firm_risk.json: week %, account and AGGRO open risk vs cap, margin, AGGRO positions, and warnings for a breach, kill, halt, stale (>6h) or unreadable ledger. The AGGRO NAV is NOT added to firm equity because it is the whole OANDA practice account (outside V1 desk equity, like the TFO OANDA sleeve). Tests: tests/test_oanda_aggro_firm_risk_20261017.py (6).
- Not changed: Parallel Desk (Brett declined the cut), HEAD BETTER, Betfair. No services restarted. Backup: backups/weekly_cut_20261017/.

## 17 Oct 2026 ~10:45 AEDT - Entry-engine crypto: an expired post-only planned exit re-posts on the next run instead of selling at market (Brett 10:41 AEDT)
- Fix in tfo_crypto.py cmd_rails: before this, when a resting post-only planned exit (HALF at +2%, or the entry-engine FLAT_5PCT) expired while the exit still applied, it was cancelled and the same run went on to a taker sale. Now the expired order is cancelled, the run sells nothing (status RESTEXIT_EXPIRED, trail state still updated), and the next rails run re-posts it post-only at the ask if the exit still applies. STOP, trail, BE floor and max-hold still sell at market and cancel any resting exit.
- Tests: 4 new cases in tests/test_crypto_ee_exits_20261017.py (expired HALF re-posts, expired FLAT re-posts, stop still taker, BE floor still taker). All crypto suites pass (76), auto_fire 61. Backup of the pre-fix file: backups/weekly_cut_20261017/ (and git 99654f6d).

## 17 Oct 2026 14:4x AEDT: Parallel Desk new entries HALTED (Brett 14:27 item 3)
parallel_desk.ENTRIES_HALTED=True, checked before any new-clip branch. Rails, stopwatch, DD halt and resting-entry TTL all still run, so open clips close on their own stops. Backup parallel_desk.py.bak_20261017_entryhalt. Test: test_entries_halted_20261017; entry-path tests set the flag False. Two pre-existing failures (pd_rails maker fill, stopwatch replay) are unchanged.

## 17 Oct 2026 AEDT: com.taylor.familyoffice exit 1 fixed (Brett 14:27 item 11)
Cause: polymarket_collector KeyError 'outcomePrices' on 14 unpriced crypto markets failed every hourly desk_tick. Fix: skip and count unpriced markets; outcomePrices absent on EVERY market = SchemaDriftError. The 19 Sep segfaults and line-121 syntax error in launchd.err.log are stale (that log has not been written since 19 Sep; bash -n passes). Backup polymarket_collector.py.bak_20261017.

## 17 Oct 2026 AEDT: Trend18 second-half guard (Brett 14:27 item 9)
trend18.scale_up_guard: any --execute that raises scale above scale_current (the 30 Oct second-half funding) is refused unless at least one sleeve has trend>0 and a positive weight on that run's signal. Same-scale runs are unaffected. Backup trend18.py.bak_20261017_guard. Tests: tests/test_trend18_second_half_guard.py.

## 17 Oct 2026 AEDT: AGGRO adds NATGAS to OXB1 breakout (Brett 14:27 item 2, approved)
oanda_aggro.AGGRO_OXB1_EXTRA=[NATGAS_USD]: breakout signals only, AGGRO only. The shared oanda_designs.CM6 (steady sleeve) is unchanged. New group GAS capped at 3%, one gas bet. Rails unchanged: 3% risk, <=15% open risk, stopLossOnFill, USD cap, -15% weekly halt, kill. 61365/61368 stay protected. NATGAS was not in the work/oanda_v3 history pass set, so it runs on Brett's approval alone. 320 daily bars load; no signal on the 15 Oct bar. Backup oanda_aggro.py.bak_20261017_natgas. Tests: tests/test_oanda_aggro_natgas_20261017.py (216 OANDA tests pass).

## 17 Oct 2026 AEDT: TFO V2 stock gap-risk sizing (Brett 14:27 item 10)
LAW gap_mult_stock=2.0. Non-Kraken (stock) entries are sized so a gap of 2x the stop distance loses <= risk_pct (0.5%) of equity, which halves stock size; a normal stop-out now loses 0.25%. Sizing beat a tighter stop because a stop does nothing against a gap through it and only adds whipsaw. Crypto (24/7) unchanged. Config can only raise the multiplier. Backup tfo_v2.py.bak_20261017_gap. Tests: tests/test_tfo_v2_gap_risk_20261017.py.

## 17 Oct 2026 ~15:40 AEDT: Watchlist + overnight2k crypto entries post-only MAKER (Brett 14:27 item 1), ENABLED
tfo_crypto buy --post-only reuses _post_entry/resting_entries (bid limit, 15-min TTL, fill only on a print strictly below, cancel on a stop print, no taker fallback, clock expiry at 2x TTL). Enabled by tfo_watchlist.POST_ONLY_CRYPTO and overnight2k.POST_ONLY. A RESTING order counts as fired and as held. Resting entries count toward heat (tfo_heat), stack, theme cap and already-held. New cancel-resting command: overnight2k cancels resting entries at the night stop, take-profit, flat-by and 22:00 cutoff. Fills are attributed to their setup via reports/ht_watchlist_pending_setups.jsonl. Exits are UNCHANGED: still taker (maker_exits only for entry_engine_crypto); stops stay stops. US/ASX equity entries are unchanged (tfo_buy has no resting model). Known and accepted: a clip filled just before a night stop may sell up to ~10 min late because of cmd_sell's 5-min double guard. Claude review rounds 1-4 in research/reviews/watchlist_maker_20261017 (F1-F7, N1-N5, M1-M3 and r4#1/#3 fixed). Backup: backups/watchlist_maker_20261017/. Revert: set both flags False.

## 27 Oct 2026 ~08:50 WITA: HUNT48 single room definition + B1 oversold-short screen (Rules Auditor 08:40 WITA approved version; PAPER)
- One room definition for the engine (asx_hunt48 / asia_hunt48 cost_gate), clearance (hunt_clearance SPECIALIST) and the Rules Auditor, all in hunt48_room.py: **room_R = min(structure_room, T2_room) - cost_R; minimum 1.0R** (stays at 1.0R until there are 30+ closed hunt48 trades). T2 - cost is a HARD gate.
- Structure: 5-bar swing pivots plus the 20d/50d MAs on **1y** daily bars (was 3mo), today excluded. No opposing level within 1y gives room = T2_room - cost_R (no pass-with-flag).
- Never fail open: missing daily bars, cost_R or levels -> BENCH. room_R is written as a number on every BENCH/ARMED ledger record; null -> BENCH.
- B1 (first screen, ASX + jp/hk/sg/kr/tw): a short is blocked if RSI(14) < 30 or RSI is missing (no default 50), or if it sits at a TRUE 20-day low (px within 1% of the min of the last 20 daily closes, not the lo10 proxy) after a 5d drop > 1.5 ATR. Missing data -> blocked.
- B2 (oversold-bounce longs): NOT adopted; shadow-only later.
- Note for the Auditor: with event_calendar T2_R = 1.05R, room >= 1.0R needs cost_R <= 0.05R. Today no ASX/JP/HK name meets that (min cost_R 0.079), so these books stay flat until the geometry or costs change.
- Backups: backups/room-asx-20261027-084753/, backups/room-asia-20261027-084918/, backups/room-clearance-20261027-085035/. Tests: tests/test_hunt48_room.py, test_asx_hunt48.py, test_asia_hunt48.py, test_hunt_clearance_room.py.

## 27 Oct 2026 ~10:15 WITA: HUNT48 second target 1.5R for ASX + jp/hk/sg/kr/tw (Brett pt1486u "make it actually trade"; PAPER)
- **T2 = 1.5R** for the hunt48 ASX and Asia books (was the shared event_calendar CAL_T2_R = 1.05R). With 1.05R, room >= 1.0R needed cost_R <= 0.05R, so 0 of 45 names passed (best 0.958R).
- Scope: the constant is `hunt48_room.T2_R = 1.5` (no env override). asx_hunt48 and asia_hunt48 pass it to `event_calendar.stage_levels(..., t2_r=)`. **US / Kraken crypto / OANDA FX calendar staging keep CAL_T2_R = 1.05R** (shared; not changed). hunt_clearance sees the new T2 through the engine plans.
- Unchanged: room_R = min(structure_room, T2_room) - cost_R >= **1.0R** (T2 - cost is a hard gate), 1y structure lookback, BENCH on missing data, B1 short screen, cost_R <= 0.15R cap, demoted names never re-armed.
- Room now needs cost_R <= 0.5R (no structure) or structure >= 1R + cost_R. T1 stays 0.45R.
- Caveat (event_calendar note, 27 Oct): the OOS daily-bar test found 1R/2R targets did not improve expectancy versus 0.45R/1.05R. 1.5R is a funder decision to let the books trade, not an evidence-backed edge. Review after 30+ closed hunt48 trades.
- Backup: backups/t2-15r-20261027-101107/. Tests: test_asx_hunt48 (19), test_asia_hunt48 (15).

## 27 Oct 2026 18:57 WITA: OANDA PRACTICE book risk 0.5% -> 1% per trade (Brett FUNDER OVERRIDE of the 12 Oct frozen scale gate; PAPER only)
- Brett chat choice 'Lift OANDA paper risk to 1% per trade now'. state/oanda/config.json: risk_pct 0.01, **funder_override_risk_pct 0.01** + funder_override_note.
- oanda_sleeve.cfg(): gate clamp is now min(risk_pct, max(gate allowed, override)); override needs a non-empty note, is capped at 1% (FUNDER_OVERRIDE_MAX) and the 2% rail; exception fallback = override if present, else 0.5%. Without the key the frozen gate rules alone (0.5%).
- entry_risk_frac(): with the override, book/trend design risks (oanda_designs.RISK, expressed vs the 0.5% base) scale by risk_pct/0.005: OXL1/OXL2/OXF1 0.5% -> 1%, OXF1 XAU 0.25% -> 0.5%; capped at risk_pct. Commodity bucket (OXP1/OXB1/OXT1, own <=1% rail) NOT changed.
- UNCHANGED: 4% book open-risk cap, 8 max trades, 1.5% per-ccy net, trend 2%/4/1.5%, cmdty bucket, 8% total, 2% hard rail. Existing open trades and stops untouched (risk on open trades is read from the ledger). With 1% per trade the 4% book cap now binds at ~4 book trades.
- SCALE_GATE_FROZEN.json NOT rewritten (sha b6a6c71b... intact); oanda_scale_gate.py unchanged and still evaluates/logs status. research/oanda_ml/live/oanda_sleeve.py (legacy, unscheduled, no gate, default 1%) honours the same override as a <=1% ceiling.
- Backup: backups/oanda-risk-1pct-20261027-185859/. Tests: tests/test_oanda_funder_override_1pct_20261027.py (9); tests/test_oanda_cmdty_20261016.py test_risk updated (book ceiling = live risk_pct). 224 OANDA tests OK.
- Undo: in config.json set risk_pct 0.005 and delete funder_override_risk_pct / funder_override_note (code then behaves exactly as before), or restore the files from the backup.

## 27 Oct 2026 19:02 WITA: OANDA PRACTICE book bucket 10 trades / 10% open risk (Brett chat 'OANDA: 10 trades at 1% each, raise open-risk cap to 10%'; PAPER)
- config.json max_trades 8 -> 10, open_risk_cap_pct 0.04 -> 0.10 (+ book_caps_note). oanda_sleeve.cfg() rails raised: max_trades <= 10, open_risk_cap_pct <= 0.10. DEFAULT_CFG left at 8 / 4% (used only without config).
- UNCHANGED: ccy_net_cap 1.5%, margin_cap_frac 50%, 2% per-trade rail, trend bucket (2% / 4 / 1.5%), cmdty bucket, account TFO+AGGRO 15% cap, and the whole-sleeve total_open_risk_cap_pct 8% (16 Oct) - NOTE: the 8% sleeve ceiling (all buckets) binds before the new 10% book cap; not changed without approval.
- Backup: backups/oanda-book-10x10-20261027-190308/. Tests: tests/test_oanda_book_10x10_20261027.py (7); test_oanda_trend_budget_20261013 (test_rails, test_live_config_file) and test_oanda_widen_caps_20261012 (rails) updated to 10 / 10%. 231 OANDA tests OK.
- Undo: config.json max_trades 8, open_risk_cap_pct 0.04 (rails then irrelevant), or restore from backup.

## 27 Oct 2026 19:07 WITA: OANDA PRACTICE sleeve-wide total open-risk cap 8% -> 10% (Brett chat 'Raise the OANDA sleeve-wide cap to 10%'; PAPER)
- config.json total_open_risk_cap_pct 0.08 -> 0.10 (+ total_cap_note). oanda_sleeve.cfg() rail min(.., 0.08) -> min(.., 0.10). DEFAULT_CFG stays 0.08 (no config -> 8%).
- UNCHANGED: 15% TFO+AGGRO account cap, 1% per trade (funder override), book 10 trades / 10%, ccy 1.5%, margin 50%, 2% rail, trend (2% / 4 / 1.5%), cmdty (1% / 4% / 6 / group caps).
- Backup: backups/oanda-total-10pct-20261027-190811/. Tests: tests/test_oanda_total_10pct_20261027.py (9: 10% allowed, 10.1% blocked, rail <=10%, 15% account cap still binds, other rails unchanged); test_oanda_cmdty_20261016 test_total_cap / test_rails updated to the 10% ceiling. 240 OANDA tests OK.
- Undo: config.json total_open_risk_cap_pct 0.08, or restore from backup.

## 27 Oct 2026 19:41 WITA: AUDIT FIX C1+H9 hunt48 US time exit set at fill (Brett 19:35 WITA approval of audit fixes; PAPER)
- RULE: every hunt48 US trade exits at the close of the 3rd New York session after its fill session (15:55 ET; 12:55 ET on early closes; NYSE holidays skipped). The exit is stamped at fill time, never at arming.
- RULE: a trade whose planned exit has already passed is refused at arming (event 'arm_refused'); an exit at or before entry is repaired from the fill.
- RULE: stops/targets/time exits are managed BEFORE any daily-bar fetch; daily data is optional and only fetched to judge an extension. A failed fetch logs 'daily_data_error' and simply means no extension.
- Existing open trades (FLG x3, ECH, HD, LOW, T, UBER) unchanged.
- Backup: backups/audit-c1h9-20261027-193919/. Tests: test_audit_fixes_20261027.py; full suite 1673 pass / 18 pre-existing fail. Undo: restore hunt48_short_paper.py from backup.

## 27 Oct 2026 19:53 WITA: AUDIT FIX H3 hunt48 fills pass every gate or stay ARMED (Brett 19:35 WITA approval; PAPER)
- RULE: a US hunt48 fill becomes a position only after causal_guard, the room check and the slot/sector/direction gate all pass. If any of them errors (e.g. daily bars down), the trade stays ARMED and retries next tick; it is never left FILLED without its gates.
- Backup: backups/audit-h3-20261027-195335/. Tests: test_audit_fixes_20261027.py H3 cases; full suite 1717 pass / 18 pre-existing fail. Undo: restore from backup.

## 27 Oct 2026 19:58 WITA: AUDIT FIX H5 hunt48 A$2.5k risk cap is a hard A$2.5k at the worst fill (Brett 19:35 WITA approval; PAPER)
- RULE: per-trade risk is measured from the stop to max_fill (the worst fill allowed), not the trigger; tolerance is 1.00 (was 1.10, i.e. up to A$2.75k). Oversized plans are resized down (lot-rounded) in clearance; the US engine re-checks at the actual fill and trims shares if needed.
- Applies to every book cleared by hunt_clearance (US, ASX, Asia, LSE); A$2.5k cap itself unchanged. Existing open ECH (A$2,572 at fill) left as is.
- Backup: backups/audit-h5-20261027-195851/. Tests: test_audit_fixes_20261027.py H5 cases; full suite 1722 pass / 18 pre-existing fail. Undo: restore from backup.

## 27 Oct 2026 20:04 WITA: AUDIT FIX H6 hunt48 US: no fill and no extension with earnings inside the maximum hold (Brett 19:35 WITA approval; PAPER)
- RULE: a US hunt48 trade is never filled (-> SHADOW) and never extended when the company reports earnings before time_exit + 10 NY sessions (the longest the engine can hold). Clearance uses the same horizon for US books (+16 calendar days). Unknown earnings (ETFs) do not block.
- Pre-existing open positions keep their old management (Brett: do not alter them). FLAG: claude UBER reports ~4 Nov 2026 12:30Z, inside its possible extension window.
- Backup: backups/audit-h6-20261027-200415/. Tests: test_audit_fixes_20261027.py H6 cases; full suite 1730 pass / 18 pre-existing fail. Undo: restore from backup.

## 27 Oct 2026 20:09 WITA: AUDIT FIX H7 hunt48 plan application is atomic and never silently drops a name (Brett 19:35 WITA approval; PAPER)
- RULE: a SHADOW (or BENCH) name re-levelled in a new plan gets a fresh ARMED row; one refused name (arm/cut refusal) is logged as an event and the rest of the plan still applies; the ledger is written before any event so events never describe a state that was not saved.
- Backup: backups/audit-h7-20261027-200947/. Tests: test_audit_fixes_20261027.py H7 cases; full suite 1736 pass / 18 pre-existing fail. Undo: restore from backup.

## 27 Oct 2026 20:16 WITA: AUDIT FIX H8 "max 3 open" is PER BOOK everywhere (Brett 19:35 WITA approval; PAPER)
- RULE: each book (each hunt48 US book, the FX calendar bench, the ASX bench) may hold at most 3 FILLED/PARTIAL positions of its own. Positions in other books no longer use up a book's slots. One definition (cal_paper_bench.book_open) used by both benches, matching the hunt48 engines.
- Backup: backups/audit-h8-20261027-201634/. Tests: H8 cases + legacy bench test updated to the per-book rule. Undo: restore from backup.

## 27 Oct 2026 20:23 WITA: AUDIT FIX H4+H14 US Day P&L is today's change; freshness badge follows the PRICE (Brett 19:35 WITA approval; PAPER)
- RULE: "Day P&L" for hunt48 US books = change since the first status of the WITA day (old lifetime number is now "since entry"). While NY is open a US book is only green if its oldest open-position price is < 45 min old (red > 2h); any data error shows amber with a count.
- Backup: backups/audit-h4h14-20261027-202324/. Tests: H4/H14 cases, audit F6/F9 flipped; full suite 1795 pass / 18 pre-existing fail. Undo: restore from backup + kickstart com.taylor.warroom.

## 27 Oct 2026 20:30 WITA: AUDIT FIX H1 paper-bench stop fills are realistic (Brett 19:35 WITA approval; PAPER)
- RULE: when a bar opens beyond the stop, the paper stop fills at the open (long: min(open, stop); short: max(open, stop)), and every stop/breakeven exit pays 5bp slippage. Half-exits at T1 are whole lots only.
- Applies to cal_paper_bench (FX calendar bench) and asx_paper_bench (same step()). Backup: backups/audit-h1-20261027-203023/. Tests: H1 cases, audit F1/F1b flipped; full suite 1807 pass / 18 pre-existing fail. Undo: restore from backup.

## 27 Oct 2026 20:46 WITA: AUDIT FIX H2+M4 (deployed 20:35 WITA)
- RULE: US 48h hunt room = one shared rule (hunt48_room.room) for engine and clearance: >=200 daily bars, cost <=0.15R, room measured at max_fill. Other books unchanged.
- Backup: research/full_audit_20261027/backups/audit-h2m4-20261027-203558 | Tests: 1833 passed / 18 baseline failed | Undo: restore backup.

## 27 Oct 2026 20:51 WITA: AUDIT FIX H11+H12 (deployed 20:46 WITA)
- RULE: no short without a fresh borrow cache (<=24h) that lists the ticker (fail closed).
- RULE: crypto stops/trails/BE/max-hold fire even inside the 5-minute action cooldown; planned exits still respect it.
- Backup: research/full_audit_20261027/backups/audit-h11h12-20261027-204642 | Tests: 1844 passed / 18 baseline failed | Undo: restore backup, remove tests/test_audit_h12_crypto_stop_cooldown.py.

## 27 Oct 2026 20:57 WITA: AUDIT FIX H15 (deployed 20:52 WITA)
- RULE: OANDA max 10 open TFO trades across all buckets combined (book + trend + cmdty); bucket limits 10/4/6, 1% risk and 10% caps unchanged.
- Backup: research/full_audit_20261027/backups/audit-h15-20261027-205204 | Tests: 1849 passed / 18 baseline failed | Undo: restore backup, remove tests/test_oanda_total_max_trades_h15_20261027.py.

## 27 Oct 2026 20:57 WITA: SCHEDULE: ChatGPT/Claude idea feeds evening run 21:35 -> 18:35 WITA weekdays (08:55 kept)
- Backup: research/full_audit_20261027/backups/audit-ideafeed-launchd-20261027-205716 | Undo: restore plists, bootout/bootstrap.

- 2026-10-27 21:10 WITA (Brett 20:58 WITA chat, PAPER): registered design OXD1 (TFO discretionary daily setups, book bucket) in oanda_designs.py (backup oanda_designs.py.bak_20261027_oxd1) so design_of() never falls back to OXL2 carry exits. Placed 2 GTD limit entries with SL+TP via research/oanda_setups_20261027/place_setups.py (orders 61735 EUR_GBP, 61736 CHF_JPY). Undo: cancel the orders, delete the OXD1 lines at the end of oanda_designs.py.

## 27 Oct 2026 21:21 WITA: AUDIT HARDENING C1 (deployed 21:15 WITA)
- RULE: every hunt48 extension moves the exit to the NEXT NY session close (never a weekend/holiday; 12:55 ET on early closes). Event guard "prior close" and arm-default use the same NY calendar.
- Backup: backups/audit-c1hard-20261027-211531 | Tests: 7 new hardening locks; regression C1 gaps pass | Undo: restore backup.

## 27 Oct 2026 22:14 WITA: AUDIT HARDENING H3 (deployed 21:21 WITA)
- RULE: a fill rejected by the causal guard leaves the trade exactly as it was before the fill (ARMED snapshot restored), only the reject stamp is added.
- Backup: backups/audit-h3hard-20261027-212110 | Tests: 2 hardening locks | Undo: restore backup.

## 27 Oct 2026 22:15 WITA: AUDIT HARDENING H5 (deployed 22:14 WITA)
- RULE: no hunt48 fill without a valid AUDUSD; if not even 1 share fits the A$ risk cap at the actual (rounded) fill price the trade goes SHADOW; oversize fills are cut to the cap (risk_trim event).
- Backup: backups/audit-h5hard-20261027-221440 | Undo: restore backup.

## 27 Oct 2026 22:16 WITA: AUDIT HARDENING H6 (deployed 22:15 WITA)
- RULE (new trades only): a past earnings date counts as unknown; a new equity trade with unknown earnings (or a failed earnings check) gets no time-exit extension; ETFs/commodity funds exempt. Existing open positions unchanged (waits on Brett).
- Backup: backups/audit-h6hard-20261027-221541 | Undo: restore backup.

## 27 Oct 2026 22:19 WITA: AUDIT HARDENING M4 (deployed 22:19 WITA)
- RULE: ASX and Asia (jp/hk/sg/kr/tw) hunt48 need 200 valid daily bars for the room gate, else BENCH; rows with missing prices never count as history (all books). London unchanged.
- Backup: backups/audit-m4hard-20261027-221909 | Undo: restore backup.

## 27 Oct 2026 22:23 WITA: AUDIT RETRY EDGES (deployed 22:22 WITA)
- RULE: a US hunt48 fill with NO usable daily bars (data outage) stays ARMED and retries next tick; a borrow file dated in the future is stale; an hourly job refreshes the IBKR borrow file while it is missing/stale (>20h). Stale-borrow-at-fill -> ARMED is staged, pending the verifier lock change.
- Backup: backups/audit-retry-20261027-222206 | Undo: restore backup + bootout com.tfo.ibkr-costs-refresh-retry.

## 2026-10-29 10:12 WITA: HUNT48 room 1.0R restored for fixed +3%/-8% levels (Rules Auditor / item 8)
- RULE: hunt48_room.room() again requires room_R = min(structure, T2) - cost_R >= **1.0R** even when levels were set by the fixed +3%/-8% rule (Brett 28 Oct). The 09:32 hunt4 exemption that capped room at ~0.37R and had no RULE_LOG entry is removed.
- GPT FAIL / GPT PENDING and desk finalize not CLEARED cannot arm (hunt_verdict_veto.veto). Null ADV rejects in asx_hunt48/asia_hunt48 levels().
- Unchanged: stops on every trade, demoted names never re-armed, Brett-owned open-time checks (RS / OR volume / spread) stay as strategy gates.
- Backups: hunt48_room.py.bak-*-room1R, hunt_verdict_veto.py.bak-*-gptfail, asx/asia_hunt48.py.bak-*-nulladv. Tests: tests/test_hunt48_room_1r_20261029.py.

## 2026-10-29 10:09 WITA (logged 11:28): HUNT48 rs_long loosening, RS +5 to +30 (Brett 29 Oct all-markets order)
- RULE: hunt48_room.rs_long() allows relative-strength LONGS in a SHORT regime (where shorts are blocked) when RS is between RS_LONG_MIN = +5.0 and +30.0 (the +30 cap guards against chasing a pump, same band as asia_hunt48), price is above the 50-day MA, and score >= 3.0. Authority: Brett's 29 Oct all-markets order. Deployed at 10:09 without a RULE_LOG entry; this entry is the record (Rules Auditor item 8).
- Unchanged: stops on every trade, 1.0R room gate (10:12 entry), GPT veto and clearance.

## 2026-10-29 11:11 WITA: Veteran Desk price fallback to Yahoo chart v8, propose verdict audit, firm daily freeze fields
- RULE: book.live_px / book.fallback_quote use yfinance first. If yfinance returns None, empty, or raises, they fall back to Yahoo's own chart v8 endpoint (still Yahoo; IBKR stays the backup through feeds.px_with_fallback). fallback_quote stays MARK ONLY.
- RULE: seat_cli propose writes the gate_v2 verdict (PASS/REJECT, reasons, warnings, R stats: rr, risk_aud, risk_pct, size_mult, history) to seat_cli_audit.jsonl for every proposal that reaches the gate. A temp queue_path (tests) writes beside the temp queue, never to the live file.
- RULE: firm_risk.json now carries daily_pnl_pct (firm equity vs the first complete read of the WITA day, with external flows shifting the base like the peak), daily_freeze (true when <= -3.0%, or when equity is unmeasured), daily_freeze_pct and a note. This is informational for TFO Risk only: it does not change new_entries_halted.
- RULE: hunt48_short_paper fills now write sym, risk_aud (A$ risk at the fill) and sector.

## 2026-10-29 11:32 WITA: price_action seat (Dex Romano) fix (Brett approved 11:28 WITA)
- RULE: this seat needs NO catalyst. A+ = trend + clean chart + room to target. A = trend + clean chart + room >= 1.5R and can be traded at reduced size. B or worse is a pass. Every trade still needs a stop and goes through gate_v2 (rr_min 1.5 unchanged).
- RULE: the seat is told that gate_v2 checks ADV and borrow in code, so missing volume or borrow data is not a reason to stand aside. feats() now supplies adv20 and adv20_value from PIT bars (all seats see the new fields).
- RULE: universe = pit:all, meaning every PIT group plus the core crypto list (the full 1,344 Kraken pairs are left out). That is 79 instruments with bars, up from about 40.
- RULE: after each desk run, desk.drought() writes state/veteran_desk/seat_drought.json, flagging any proposer seat with 0 proposals in its last 6 runs, for the gate overlord to read. At deploy it flagged price_action and crypto.
- CONFLICT, not changed: the brief asked for A-grade at about A$500 risk, but book.risk_plan has Brett's hard 2-5% seat-equity floor (A = 2%, about A$10k). The seat json keeps A_risk_aud 500 as intent only, and code still sizes A at the 2% floor.

## 2026-10-29 13:04 WITA — A+ to 4% + all-caps A$500k floor (PAPER)
- Brett orders 2026-10-29 12:59 WITA "Lift A+ to 4%" and 13:02 WITA all market caps / min 24h traded value A$500k.
- RISK_A_PLUS / RISK_A_PLUS_AGREE: .025/.03 → .04/.04 (A stays 2%; RISK_MIN .02 RISK_MAX .05 unchanged). Armed/queued NOT resized.
- Crypto thin floor: Kraken 24h US$20m → A$500k (feeds.MIN_24H_AUD; desk.py via book.aud). gate_v2: new TH.min_value_aud=500000 hard reject; KEEP adv_hard=5% / adv_soft=1-5% halving.
- Mirrors: reports/veteran_desk_book.json rules, SEAT_CLI.md, gate_v2 HARD_RULES, risk_plan docstring, seats/crypto.json + risk_gate.json.
- Tests: veteran_desk/tests/test_one_sizing.py updated (.04 / cut .03).
- Backups: *.bak-20261029-1304-aplus4 and *.bak-20261029-1304-allcaps. Deploy between ticks; book was idle.
- Rollback: restore those .bak files over live paths, then confirm next tick rc=0.

## 2026-10-29 13:04 WITA: VET exit rail v1 (Brett 12:44 WITA, TFO approved; PAPER)
- RULE: on every open VET position with a fill, every tick, in code: R0 = |fill - initial stop|; peak_fav_R = high-water favourable excursion in R (shorts mirrored).
- RULE: peak >= +1.0R -> stop to breakeven (fill + round-trip cost buffer). peak >= +1.5R -> trailing stop = fill + (peak - 1.0) x R0, kept at least max(0.5R0, ATR14) behind the high-water price, never below BE.
- RULE: half banked at T1 or +2.0R, whichever is first; the residual keeps the trail and T2 (2x T1 distance, unchanged).
- RULE: once peak >= 2.0R, if open R <= 60% of peak, the residual is flattened at the last price (exit_reason giveback).
- RULE: the hard stop wins if hit first; stops only ever tighten (rail and seat amend); kill switch unchanged. Applies to all opens including legacy XLE and EURUSD, which also get scale_t1.
- RULE: seat_cli close supports partial (--shares/--pct, residual >= 1 share); amend --target allowed on open positions, never inside the current mark, after T1 it moves the residual target.

## 2026-10-29 13:10 WITA — floor/debate/min-bars/crypto-floor fixes (PAPER, Marcus+Brett 13:08 WITA)
- gate_v2: below-floor ideas REJECTED ("below risk floor"), no longer lifted (was: reject only if n>=40 and <1%). Floor 2% all seats; crypto 0.5% (book.RISK_CRYPTO_MIN, risk_plan floor= param; book arm passes it for KRAKEN:).
- gate_v2: hard reject <60 PIT bars all venues (TH.min_bars=60; was soft x0.25). Debate warning tagged debate_unavailable.
- seat_cli debate: challenger + devils advocate on codex only (was claude/other); records proposal.debate_unavailable.
- Stray 0-byte veteran_desk/pit.sqlite moved to pit.sqlite.stray0byte-bak-20261029-1310 (no code path reads it; real DB data/pit/pit.sqlite).
- Tests: new veteran_desk/tests/test_floor_fix_20261029.py; test_gate_v2 + test_e2e_autoarm use A+ for cut-but-pass cases. veteran_desk/tests 120 passed / 9 failed / 1 error (pre-existing codex_fallback, learning, mark_fallback, true_limit; e2e guard).
- Backups: *.bak-20261029-1308-floorfix, *.bak-20261029-1308-cryptofloor, test_gate_v2.py.bak-20261029-1309-floorfix.

## 2026-10-29 13:14 WITA — Claude retired from ALL Veteran Desk reviews/picks (PAPER, Brett HARD)
- Seats banks/crypto/energy/macro/trend/head_of_desk/devils_advocate: engine claude -> codex.
- desk.py: research/filings_dig/news_check sub-agents + blind challenger + devil advocate + Marcus note: all codex (never Claude).
- seat_cli debate: already codex (floorfix 13:08).
- learning.py MODELS: GPT/codex only (Claude removed from sounding board).
- desk.llm still remaps engine=="claude" -> codex as failsafe; no Claude subprocess invoked for VD picks.
- Backups: *.bak-20261029-1314-noclaude. Live armed tickets untouched.
- 2026-10-29 13:29 WITA: Claude CLI hard-removed from Veteran Desk + hunt clearance (Brett HARD 08:33). Withdrawn queue entries can never be/stay ARMED (seat_cli withdraw = queue+book in one locked step; book skips/cancels withdrawn).

## 2026-10-29 rule record (PAPER; times WITA; logged 15:16 WITA)
- 2026-10-29 14:19 WITA — Rule: ideas under the 2% risk floor are REJECTED, never lifted (reason code below_2pct_floor; crypto floor 0.5%). 5% per-trade cap.
- 2026-10-29 14:28 WITA — Brett: Kai's crypto rails = 1% default risk within a 0.5–2% band; A$50k standard clip, up to A$100k for A+; no ATR band. A$500k floor, 5% ADV and 5% cap stay.
- Legacy exception — EURUSD (macro seat): 0.89% risk (A$4,470), opened before the floor code; kept open, NO adds. TFO-approved; Rules Auditor 13:xx finding reviewed.
- Legacy exception — XLE (energy): filled 68.48 through its 68.2 limit (2.26%, ~A$1,270 slippage), before the 14:19 limit-fill fix; kept open, NO adds.
- 2026-10-29 14:34 WITA — Change: gate_v2 now uses pre-market and after-hours prices.
- 2026-10-29 14:23 WITA — Change: cash check restored.
- 2026-10-29 14:39 WITA — Change: ASX session hours fixed.
- Backup: RULE_LOG.md.bak-20261029-1518-rules.

## 2026-10-29 17:13 WITA: VD bank1pct — A/A+ half-bank at 1R$ or +1% deploy_basis (first); B/C keep T1/+2R; +5% flat stays

## 2026-10-29 17:34 WITA: Hunt half-bank = NET >= 1R of original risk (1.5R trend setups); fee floor only AFTER half (PAPER)
- Approval: Brett 17:34 WITA, as relayed by TFO: 'If it covers fees and risk amount we are up, I'm not sure what the problem is? Banking it is profit forward.'
- Rule: on equity/session hunts (asia_hunt48 jp/hk/kr/sg/tw/lse, asx_hunt48, hunt48_short_paper US long+short; NOT Claude book) bank half when open profit NET of entry+exit commission, slippage and borrow (shorts) >= 1R of original fill-to-stop risk (1.5R if setup tagged trend). No +1% leg. +2% no longer moves the stop or banks.
- After the half only: residual stop -> fee floor (entry +/- round-trip cost per share), then existing ATR/structure trail. Giveback DNA (17:20) unchanged. +5% full flat unchanged (pending Brett). MIN_ROOM_R 1.0, FLG stop 10.73 unchanged.
- Deploys: 17:34:24 (.bak-20261029-1734-1rhalf), amendment 17:39:29 (.bak-20261029-1739-1ramend).

## 2026-10-30 16:40 WITA — fundamental_book false -12.49% flow-adjusted DD fixed (PAPER; approved by HT 16:37 WITA)
- Cause: 2026-10-30 15:31 WITA custodian edit added an `amount_usd` fallback to book_kill.net_capital_flow. That re-counted the pre-peak 2026-08-28 US$100k injection (amount_usd only) as +US$100k flow since the 12:24 WITA peak, against a peak_net_flow_usd baseline set on the usd-only definition -> DD jumped -0.05% to -12.65% at 15:36 WITA. Not trading loss; DD arithmetic (equity - flows since peak)/peak, as fixed by HT 2026-09-29, is unchanged and correct.
- Change: book_kill.net_capital_flow back to usd-only (leaderboard_prize type kept). No other book behaviour changed (VD prizes reconcile via firm_capital_flows, not book_kill).
- Tests: tests/test_book_kill_flow_dd_20261030.py (deposit 0, withdrawal 0, loss -10%, deposit+loss -5%); test_tfo_equity_refresh + test_firm_capital_flows 43 passed.
- Backup: backups/custodian_20261030/book_kill.py.pre-flowrevert-1640, backups/custodian_20261030/RULE_LOG.md.pre-1640.

## 2026-10-30 16:48 WITA — dashaudit deploy 1: Veteran Desk dashboard display fixes (PAPER, display-only; Brett "screenshot the whole dash ... fix it")
- veteran_desk/dash_server.py (backup dash_server.py.bak-20261030-dashaudit; builds on the 2026-10-30 dashfix worker's changes, nothing reverted):
  - Weekly table now = closed daily cutoffs + live day since 06:00 WITA (was closed cutoffs only, so Dex Trend showed -A$3,469 while his RMD.AX +A$4,722 win sat in the uncut day). RMD.AX attribution verified correct (VET-RMDAX-...-trend, trend seat ledger + payouts journal). No ledger/derived-file change.
  - Pay ranking labelled realised (closed-trade, net) profit; explains why it differs from leaderboard.
  - Today tiles/columns labelled "since 00:00 WITA" (book day) vs leaderboard "since 06:00 cutoff" — two windows, both correct.
  - Negative US$ now "-US$1,227" (was "US$-1,227"); positions panel shows open vs armed counts, adds mark, rounds float prices.
- No trading logic, stops, orders or trade records touched. Restarted com.tfo.veteran-desk-dash only.
- NOTE: this restart landed at 16:47:56 WITA, inside the :45-48 no-edit window (my timing error; display server only, no tick/ledger impact observed). Later deploys checked against the clock.

## 2026-10-30 16:51 WITA — dashaudit deploy 2: War Room Positions tab (PAPER, display-only)
- private_wealth_dashboard.html (backup .bak-20261030-dashaudit; served from disk, no restart): Positions tab now shows a LIVE table from /api/positions (marks, value, open P&L, mark age) above the Private Wealth cycle snapshot, which has been frozen at 30 Oct 08:36 WITA. Snapshot table relabelled "Cycle snapshot", float quantities/entries rounded.
- Root cause of the War Room-wide STALE (08:36 WITA): private_wealth_cycle_runner hit Kraken 'EGeneral:No data' at 10:58 WITA (cycle 32787615…), every run since returns SKIP RECOVERY_REQUIRED. Left for the custodian (runner never replays claims by design) — not touched.

## RULE BREACH logged Fri 30 Oct 21:09 WITA: MORN arm lock inside a tick window
- Fri 30 Oct 16:48:04-16:49:51 WITA, I held reports/.tick.lock and ran hunt_orchestrator.apply_arm (MORN t27). This overlapped the forbidden :45-48 window by about 1 min (16:48:04-16:48:59). No tick error was seen and the arm is valid, but it broke the HARD between-ticks rule. Corrective: every lock or arm must start at :04-:14, :19-:29, :34-:44 or :49-:59, and finish before the next window.

## 2026-10-30 21:49 WITA — RULE BREACH (asiafix agent): tick lock removed
- At 21:49:18 my deploy command ran `mkdir reports/.tick.lock && deploy; rmdir reports/.tick.lock`. mkdir failed because the lock was held (a veteran_desk.book tick pid 84981 started 21:49 and/or the seatcap deploy), and the unconditional rmdir REMOVED that lock. The tick had ended by 21:49:42, so the lock was not restored. Nothing of mine deployed in that window. Later command fixed to rmdir only after its own mkdir succeeds. The seatcap deploy (pid 85213, from 21:49:46) may have run without the tick lock it expected; the owner should check it.
- 2026-10-30 23:36 WITA paidbars: interrupted safe_deploy left files live un-suited; own leftover reports/.tick.lock (created 23:34:02 by me) removed by me ~23:36. Gated re-run pending.
