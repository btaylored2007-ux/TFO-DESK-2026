# MASTER FIX LIST — TFO paper office (PAPER ONLY)
Built 2026-10-30 19:45 WITA (read-only audit). Status: TODO / IN-OTHER-BUILD / DONE ✓ (verified, WITA time) / NEEDS-BRETT / BLOCKED.
Rule: items touching gate_v2.py, book.py, desk.py or THF engine files are deferred until the main build lands.

## P0 — trading / money correctness
| id | symptom | evidence | proposed fix | verify | status |
|---|---|---|---|---|---|
| P0-01 | THF US beta cap is 40% of equity (~US$1.55m), not US$750k | codex_final HIGH book.py:699; DEPLOY_LOG tfo_betacap_dyn_20261030 (Brett 19:22 changed it ON PURPOSE) | Conflict: Brett chose 40% dynamic at 19:22; codex says fixed 750k. Make calc errors block entries either way | unit test cap value + error blocks | NEEDS-BRETT (book.py, deferred) |
| P0-02 | THF ledger RMW has no internal lock; wrapper steals locks by age | codex_final HIGH book.py:977, run_thf_tick.sh:7 | OS flock across tick(); never steal by age | two concurrent ticks -> second exits | TODO deferred (THF) |
| P0-03 | Tick-lock race: lock create + PID write non-atomic | codex_recheck MEDIUM run_thf_tick.sh:6-10 | mkdir/flock atomic + pid inside lock | race test | TODO deferred (THF) |
| P0-04 | Queue transitions commit before ledger save | codex_final HIGH book.py:1282/1309 | journal + idempotent replay | crash-injection test | IN-OTHER-BUILD |
| P0-05 | Fill-time bars: rolling highs/lows ignore armed_utc -> fictitious fills | codex_final HIGH book.py:1130 | timestamped post-arm bars only | replay test | IN-OTHER-BUILD |
| P0-06 | Armed orders bypass halt/heat/risk at fill; gap fills beyond stop | codex_final HIGH book.py:1121 | revalidate halt, stop geometry, risk<=5%, heat, borrow at booking | unit tests | TODO deferred (book.py) |
| P0-07 | Gap stops fill at exact stop; _hold doesn't cover half-bank/T2/trail | codex_final HIGH book.py:1189 | gap-aware exit model + session eligibility on every exit | unit tests | IN-OTHER-BUILD (gap stops) / rest deferred |
| P0-08 | AUDUSD silently defaults 0.65; FX ignores quote timestamps | codex_final HIGH book.py:288/329 | timestamp-validated Yahoo FX, block when missing | test missing FX blocks | IN-OTHER-BUILD |
| P0-09 | Missing/NaN volume only warns; no ADV recheck at fill | codex_final HIGH gate_v2.py:328 | fail closed; A$500k liq + <=5% ADV at final size | unit test | TODO deferred (gate_v2) |
| P0-10 | THF consumes/writes VD forward journal + scorecards; VD veto history can charge THF commissions | codex_final HIGH book.py:1265, desk.py:595 (codex_recheck says now isolated — re-verify) | isolate under state/thf | grep + test | TODO verify after build |
| P0-11 | Shared VD logs written by THF | parent/Marcus report; THF runs through VD desk code paths | THF_DESK -> logs/thf_* paths | tail VD logs after THF tick show no THF lines | TODO deferred (THF) |
| P0-12 | THF dash seat P&L uses VD opening balances (essentials -A$4,897.67 vs actual 0) | codex_final HIGH thf_dash.py:39; recheck MEDIUM thf_dash.py:42 LB.open_marks() still reads VD | THF-specific opening marks snapshot | dashboard seat P&L reconciles to book | TODO deferred (THF) |
| P0-13 | Partial exits keep all accrued borrow on residual | codex_final HIGH book.py:947 | prorate borrow to closed slices | unit test | TODO deferred (book.py) |
| P0-14 | Realised P&L uses current FX for entry and exit (FX P&L at exit missing) | codex_final MEDIUM book.py:945 | keep AUD entry basis; reconcile closed P&L to cash | unit test; fx_settlement test | TODO deferred (book.py) |
| P0-15 | Expiry only checked when trigger false; queue expires_utc ignored when arming | codex_final MEDIUM book.py:1154 | check expiry before every admission/fill | unit test | TODO deferred (book.py) |
| P0-16 | funder_gate.py CFG commented out; config NameError swallowed (enable/auto-approve ignored) | codex_final MEDIUM funder_gate.py:14 | restore CFG, surface config errors | import + read cfg | TODO (check vs .bak-thffresh-cfgfix; THF-adjacent, deferred) |
| P0-17 | Trend18 BTC has no position stop rail — only sleeve kill -30% NAV (stop US$59,854) | logs/entry-engine.log BTC line | add BTC trail/stop rail in trend18_trail.py | entry-engine shows rail stop | NEEDS-BRETT (rule change) |
| P0-18 | Test suite writes live forward_trades.json.tmp (guard blocks it) | pytest 19:42: 5 fail/4 error; TEST GUARD open(w) state/veteran_desk/forward_trades.json.tmp from desk.py:620-643 (FWD_DIR resolved at import) | resolve FWD_DIR at call time / honour test BASE | pytest back to 339 pass + known 1 fail | TODO deferred (desk.py) |
| P0-19 | Trend18 MARK_MISSING UPRO/TQQQ/UGL (logs 20:33/20:40/21:34 AEDT); state/trend18_px_last.json stale since 16-17 Oct | trend18.py fetch_marks: Alpaca primary, and Yahoo was used only when Alpaca raised, so a partial Alpaca reply meant missing marks; px_last.json has no reader (orphan) | Yahoo primary + Alpaca gap-fill (trend18.py, backup .bak-20261030-mfl) | fetch_marks returned all 7 fresh; 19:52 tick marked yahoo_last 11:51:55Z and kill_check ran on them (kill False, dd -9.60%) | DONE ✓ verified 19:54 WITA |

## P1 — failed jobs / data
| id | symptom | evidence | proposed fix | verify | status |
|---|---|---|---|---|---|
| P1-01 | com.tfo.pit-daily-topup last exit 1 | logs/pit_daily_topup.log: sqlite3 OperationalError 'unable to open database file' + curl(77) CA load fail = fd exhaustion (yf.download threads=True chunk 150); BINANCE:* syms sent to Yahoo | exclude BINANCE:, cap threads; next run 19:37 succeeded (791 updated, 0 errors) | next launchd exit 0 | TODO (veteran_desk data, not THF engine) |
| P1-02 | Yahoo 429s | thf_tick.log & pit_daily_topup.log 'yf_guard: 429 backoff 2/5/12s' | stagger topup away from THF tick; single shared yf rate budget | 429 count/hour | TODO |
| P1-03 | pit_backfill_macro traceback | RuntimeError fail binance klines symbol=牛来USDT (non-ASCII symbol) | skip non-ASCII/invalid Binance symbols | rerun clean | TODO |
| P1-04 | ht-equity-rails MARK-REFRESH skipped (desk tick running) 59x | logs/ht-equity-rails.log | retry after tick / schedule offset | no skips | TODO |
| P1-05 | deep19-fwd 404s (AXM, TZIC quote not found) | logs/deep19-fwd.err.log | drop delisted symbols from universe | err log clean | TODO P2-ish |
| P1-06 | veteran-desk-news source failures | CoinDesk HTTP 308, Federal Reserve 404, The Register 404 | update feed URLs / follow redirects | news log no fails | TODO |
| P1-07 | Announcements + newsbot state writes | backlog | atomic tmp+rename in state dir honouring BASE | tests | TODO |
| P1-08 | Hunt scripts hard-code /Users/test path for claude_retired.json | hunt48_short_paper.py:360, hunt_clearance.py:24 | use BASE | grep + py_compile | DONE ✓ verified 19:51 WITA |
| P1-09 | Trend18 month-end one-shot must fire 03:50 WITA 31 Oct (=15:50 ET 30 Oct) | plist com.tfo.trend18-30oct-once loaded, Day31 03:50; script syntax OK; guards ET 15:45-15:57; no done marker; sleep prevented (caffeinate) | none needed on Mac; box routine failure separate | check logs/trend18-30oct-once.log after 03:50 | READY (verify tomorrow) |
| P1-10 | Failed box routines today: Trend18 month-end, semiconductor track, shark stalk, execution bot, news bot, rules auditor, code health, best-setups, morning brief | not visible from Mac audit; likely common cause = box->Mac relay/approval or Mac cmd declines | parent to pull routine run errors | rerun each | NEEDS parent/box review |
| P1-11 | wake-queue corrupt JSONL line crashes dispatch; running wakes treated done after restart | codex_final MEDIUM vd_engine.py:492 | skip/repair malformed tail; reconcile running jobs | inject bad line test | TODO deferred (VD engine — touches THF dispatch) |
| P1-12 | DoH for IBKR/OANDA/others | desk_doh.py present with IBKR/OANDA/EXTRA/DATA groups; still curl(77) CA failures in yfinance (curl_cffi CA path) | verify each consumer imports desk_doh; fix certifi CA path for curl_cffi | resolve test per host | TODO verify |
| P1-13 | THF checklist with tick times | backlog | write checklist w/ tick schedule | file exists | TODO deferred (THF) |
| P1-14 | Grok webhook | backlog; no webhook code found in quick grep | needs spec | — | NEEDS-BRETT |
| P1-15 | Box vd_shadow rebuild | backlog (box side) | rebuild on box | — | TODO (box) |
| P1-16 | Disk growth | repo 111G: backups 33G, data 26G (pit.sqlite 8.5G + two 8.5G .bak copies 29 Oct), state 3.5G; disk 73% | prune old pit .bak (17G) after Brett OK; rotate backups | df | NEEDS-BRETT (deletion) |
| P1-17 | Stale OANDA plists | com.tfo.oanda-check/daily/lab loaded, exit 0; oanda-daily.log 2 errors (USD_BRL/USD_KRW candles HTTP 400 on fx volume) | drop unsupported OANDA instruments from fx_volume | log clean | TODO |

## P2 — cosmetic
| id | symptom | evidence | proposed fix | verify | status |
|---|---|---|---|---|---|
| P2-01 | warroom / veteran_desk_dash BrokenPipeError tracebacks | logs/warroom.err.log (207), veteran_desk_dash.log (38) | catch BrokenPipe/ConnectionReset in handlers | err log quiet | TODO |
| P2-02 | THF dash innerHTML unescaped | codex_final MEDIUM thf_dash.html:39 | textContent / escape | XSS string test | TODO deferred (THF) |
| P2-03 | Secret redaction leaves values; bearer tokens not covered | codex_final MEDIUM desk.py:235 | full-value redaction | unit test | TODO deferred (desk.py) |
| P2-04 | Dash overlord memory cache not invalidated | codex_final LOW thf_dash_overlord.py:43 | invalidate mem cache | repair check | TODO deferred (THF) |
| P2-05 | launchd -15 last-exit on dashboards (18860/18870, vd-engine, pw-watch) | launchctl list | expected (SIGTERM restarts) — confirm KeepAlive | ports respond | INFO |

## IDLE DESKS (Brett ruling 19:48 WITA: cut only desks/sleeves that have NEVER traded; keep every desk that has traded, even if losing)
Audit 19:55 WITA, read-only. "Fills" = ledger trades with a `fill` price. NOTHING unloaded or paused — the parent will do the pausing.
| desk | started (first record) | staged rows | fills | launchd jobs | verdict |
|---|---|---|---|---|---|
| HUNT48_HK | 2026-10-27 | 138 | 0 | com.tfo.hunt48.hk, com.tfo.hunt48.hk.preopen | IDLE → cut candidate |
| HUNT48_KR | 2026-10-28 | 20 | 0 | com.tfo.hunt48.kr, com.tfo.hunt48.kr.preopen | IDLE → cut candidate |
| HUNT48_TW | 2026-10-28 | 82 | 0 | com.tfo.hunt48.tw, com.tfo.hunt48.tw.preopen | IDLE → cut candidate |
| HUNT48_SG | 2026-10-28 | 1 | 0 | com.tfo.hunt48.sg, com.tfo.hunt48.sg.preopen | IDLE → cut candidate |
| HUNT48_LSE | 2026-10-28 | 51 | 0 | com.tfo.hunt48.lse (currently running, PID 24058), com.tfo.hunt48.lse.preopen | IDLE → cut candidate |
| HUNT48_JP | 2026-10-26 | 171 | **1** (7453.T filled 30 Oct 09:00 WITA, stopped out, net −A$2,713.50) | com.tfo.hunt48.jp, com.tfo.hunt48.jp.preopen | HAS TRADED → KEEP under the ruling (Brett to confirm) |
| HUNT48_ASX | 2026-10-26 | 587 | 0 (2 ARMED for Mon 02 Nov 07:00 WITA) | com.tfo.hunt48.asx, com.tfo.hunt48.asx.preopen | IDLE so far, but has live armed orders. NEEDS-BRETT |
| Edge50 CRYPTO_TR | 2026-10-24 (inception) | 6 daily marks | 0 (100% CASH every day, equity A$100,000 flat) | com.tfo.edge50-fwd (shared with US_ML + ASX_MOM, which are invested: keep). State is in ~/.tfo_edge50 | IDLE → cut the sleeve only; the job can't be unloaded without stopping US_ML/ASX_MOM |
| HUNT48_SHORT / _GPT / _CLAUDE | before 26 Oct | 27/18/22 | 2/3/3 CLOSED with entries | com.tfo.hunt48.short, com.tfo.hunt48.short.gpt | HAVE TRADED → KEEP |
Shared jobs that serve the idle hunts too (keep them): com.tfo.hunt-orch, com.tfo.hunt-orch-verify, com.tfo.verdictveto.verify, com.tfo.intraday-rescan.
Box routines tied to these desks: not visible from the Mac. The parent should map them.
Not yet checked for idle status: crypto20 ft1 shadow, deep18/deep19 fwd, shadow-nasdaq2x, csp-sleeve, oanda-lab, hunt-shape-trial.

Note: test_hunt48_short_paper.py shows 8 failures that predate this work: a tfo_buy.py hash pin, and risk_aud 5000 vs 2500 after the cap changes. They are not caused by P1-08 and are TODO (P1-18).
