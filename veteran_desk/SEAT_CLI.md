# Veteran Desk seat CLI (PAPER only)

For the VD seat bots. Run from `~/Applications/MultiVenueDesk` with `/usr/bin/python3` (3.9) or 3.14. Every command prints ONE JSON object; exit 0 = ok.

| Command | What it does |
|---|---|
| `python3 -m veteran_desk.seat_cli propose SEAT 'JSON' [--challenger LONG\|SHORT\|STAND_ASIDE]` | Runs gate_v2 on the setup. PASS -> queued auto-approved (no funder tick, Brett 19:05 WITA); `book.py tick` arms it on its next run. Returns id, risk_aud, risk_pct, shares, warnings, or reasons if blocked. |
| `python3 -m veteran_desk.seat_cli status SEAT` | Seat equity, cash, today P&L, P&L since start, open/armed ids, closed count. |
| `python3 -m veteran_desk.seat_cli positions [SEAT]` | All armed/open positions (optionally one seat), each with `peak_fav_R` and `rail_state` (initial / be / trailing / t1_done). |
| `python3 -m veteran_desk.seat_cli close SEAT ID REASON [--shares N \| --pct P]` | Paper close at live mark (armed -> cancelled). With `--shares`/`--pct`: partial close of an OPEN position; the residual stays open with its stop/target/rail; refused if it would leave < 1 share. Audited in seat_cli_audit.jsonl. |
| `python3 -m veteran_desk.seat_cli withdraw SEAT ID REASON` | Pull a pending proposal (queued -> withdrawn) or cancel an armed order (armed -> cancelled). Never touches an open trade (use close). Audited. |
| `python3 -m veteran_desk.seat_cli amend SEAT ID [--stop X] [--target X] [--shares N] REASON` | `--stop`: tighten only, never below the exit-rail floor (the current code stop). `--target`: open or armed; may not be set inside the current mark (long: above mark, short: below); after T1 it sets the residual target. `--shares`: armed orders only, risk 2-5%, notional <= 1x seat equity. Audited. |
| `python3 -m veteran_desk.seat_cli lessons [SEAT] [--add "text"]` | Closed-trade summary (wins, stops, net) + last 20 lesson notes; `--add` appends one note. |

Setup JSON (required: ticker, side, entry, stop, target, grade): 
`{"ticker":"XOM","side":"LONG","entry":118.2,"stop":114.6,"target":125.4,"grade":"A","sleeve":"core","horizon_days":10,"thesis":"..."}`

Seats: energy, essentials_utilities, banks, healthcare, macro, news_filings, price_action, crypto, fx_commodities, tech_ai_infra, trend (Dex Romano, A$500k, added Brett 2026-10-28 20:13 WITA). Desk total 11 x A$500k = A$5.5m.

Rules enforced in code (a bot cannot change them): stop on every trade; risk 2-5% of seat equity via `book.risk_plan` (gate cuts move toward 2%, never below; A 2%, A+ 4% with or without agreeing challenger - Brett 2026-10-29 12:59 WITA); B grade blocked; no crypto shorts; correlated heat 15%; max 8 open per seat; firm HALT and kill switch (-8% halve toward 2%, -12% flat); no chase (>1% through entry at arm = expired). No broker, no live orders.
## Exit rail v1 (code, every tick, Brett 2026-10-29 12:44 WITA)
R0 = |fill - initial stop|. `peak_fav_R` = best favourable excursion so far (in R).
1. peak >= +1.0R -> stop to breakeven (+ round-trip cost buffer). 2. peak >= +1.5R -> trailing stop fill + (peak - 1)R0, kept at least max(0.5R0, ATR14) behind the high-water price, never below BE.
3. Half banked at T1 or +2.0R, whichever comes first; for grade A/A+ ALSO at first of open net A$ >= entry risk_aud (1R$) or +1% of deploy_basis (Brett/TFO 2026-10-29 17:08 WITA); B/C keep T1/+2R only. Residual stop -> BE then trail toward T2. 4. Once peak >= 2.0R, if open R falls to <= 60% of peak -> residual flattened (`exit_reason=giveback`). 5. Giveback protect (Brett HARD 17:17 WITA, all grades): before the half-bank, once peak open net >= 0.5R (RAIL_GB_ARM_R), if open net gives back >= 50% (RAIL_GB_FRAC) and is still > 0 -> bank half at last, residual stop -> BE (`rail_giveback_protect`).
5. The hard stop wins if hit first; stops only tighten; kill switch unchanged. Every rail move is in the position's `rail_log`.

Note: `close` and `amend` take the tick lock; mutating commands also wait out :x0/:x5 tick minutes. Earlier note: `close` writes the book file directly - avoid running it at the same minute as `book.py tick`.
