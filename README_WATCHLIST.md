# TFO V1 pre-open watchlist (`tfo_watchlist.py`): PAPER ONLY

This is a token-free launchd job. Every 5 minutes it builds a gate-checked watchlist for each market whose regular open is 20 to 30 minutes away: ASX, TOKYO, HK, INDIA, EUROPE (London, Xetra, Euronext, SIX, Nordics), US (with Toronto) and B3. It refreshes the Kraken crypto list on every tick. `tick`, `build` and `show` never ticket and never write a ledger. The only files they write are `reports/ht_watchlist.json` and `reports/ht_watchlist_heartbeat.json`, both atomically. Only `fire MARKET --execute` can ticket, and it does so by calling the desk's own `tfo_buy.py` / `tfo_crypto.py buy`.

**Gates.** The watchlist does not copy the gates. `build` calls `tfo_buy.gates()` and `tfo_crypto.gates()` directly and feeds them the build's own cached quotes. Before the open, some tfo_buy failures can only be settled by the first regular-session print: session/tape, the auction window, size vs today's dollar volume, and the session-change checks. Those are listed under `deferred_to_open`. Any other tfo_buy failure rejects the name. `fire` re-runs the real tools, so at the open they are the only authority. **Trend filter.** This is a watchlist rule, not a tfo_buy gate. Every candidate, equity and crypto, must have last price > the 50-day SMA of completed daily closes AND a 20-day return > 0. Otherwise it is rejected with `trend filter: below 50d SMA` / `trend filter: 20d return <= 0`, or `trend filter: insufficient history` if there are fewer than 50 bars. `fire` re-checks it on fresh history and last price before calling tfo_buy/tfo_crypto. **News gate (equity only).** It uses yfinance `Ticker.news` and `Ticker.calendar`, one read each per would-be candidate, cached for the build. A candidate is rejected with `news gate: earnings within 1 trading day` when the calendar earnings date is the session day or the next trading day. It is rejected with `news gate: negative headline` when a headline from the last 36h matches the keyword list; the reject record stores `news_headline` and `news_keyword`. A fetch error passes with flag `news_unchecked`, and so does an empty news feed or empty calendar, because yfinance returns `[]`/`{}` on auth failures. `fire` re-checks it before calling tfo_buy. Crypto is skipped. **Vol sizing (build + fire).** Sizing stop = max(5%, 2×ATR14/price). vol_size = 0.25% of firm equity ÷ sizing stop, where firm equity is `tfo_buy.firm_state`, the same source the heat check uses. clip = min(vol_size, tier cap, 5% ADV10, heat room, crypto stack room, powder net fees). The rail stop stays at −5%: a wider ATR shrinks the size and never widens the stop. Each candidate carries `vol_size_usd`, `risk_usd` (clip×5%, the heat basis), `vol_risk_usd` and `sizing_stop_pct`. **Legacy no-rebuy.** Names held in `fundamental_book` or `convexity_sleeve` (keys plus `underlying`/`symbol` fields) are rejected with `legacy book holds name`, in build and again in fire. **Setup tags.** Every `fire --execute` fill appends one line to `reports/ht_fills_tagged.jsonl`: ts, market, symbol, side, qty, fill px, notional, risk_usd, setup, ticket_id (taken from the facts line) and the LOG line. The setup defaults to `watchlist_momentum_equity|crypto`; the 2-hourly hunt uses `fire CRYPTO --execute --setup crypto_hunt`. `report [--setup S] [--json]` shows per setup: trades, win rate, gross P&L (realised exits + open marks), net P&L (null until every fill carries fees) and mean per trade. Scores are labelled `beta` (formula in the JSON). The $15k/day stretch never sizes a clip.

## Install (Mac, desk root `/Users/test/Applications/MultiVenueDesk`)
```bash
cd /Users/test/Applications/MultiVenueDesk
shasum -a 256 -c tfo_watchlist_deploy.tgz.sha256          # the .sha256 file lists the tgz by name
tar xzf tfo_watchlist_deploy.tgz                           # adds: tfo_watchlist.py, README_WATCHLIST.md, launchd/, tests_watchlist/, reports/logs/
python3 -m unittest discover -s tests_watchlist           # offline; runs against THIS desk's tfo_buy/tfo_crypto in a temp copy
python3 tfo_watchlist.py build CRYPTO && python3 tfo_watchlist.py build US   # one manual build (read-only)
python3 tfo_watchlist.py show
cp launchd/com.tfo.ht-watchlist.plist ~/Library/LaunchAgents/
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.tfo.ht-watchlist.plist
launchctl print gui/$(id -u)/com.tfo.ht-watchlist | grep -E 'state|last exit'
tail -f reports/logs/ht_watchlist.log                      # one "WL ... tick due=[..] cand .." line per 5 min
cat reports/ht_watchlist_heartbeat.json                    # next opens, errors, holiday-table expiry flag
```
The tarball doesn't overwrite any existing desk file (check with `tar tzf`). It needs `yfinance` in the interpreter that `/usr/bin/env python3` resolves to under the plist PATH (python.org 3.14 first, the same as the com.tfo.v2-* jobs). If `desk_costs.py` is present, fees use it. Otherwise a conservative fallback is used (the JSON shows `fee_model`).

## Use
```bash
python3 tfo_watchlist.py show [MARKET]              # * = fits the cumulative plan (themes ≤2, heat headroom, powder)
python3 tfo_watchlist.py build EUROPE               # forced build (aliases: LONDON, NY, TSX, NSE, BRAZIL, KRAKEN...)
python3 tfo_watchlist.py fire US                    # preview: runs tfo_buy.py WITHOUT --execute for the top 5
python3 tfo_watchlist.py fire US --execute --top 3  # at/after the first eligible print (US 09:38 NY; DELAYED venues wait out the Yahoo delay: London 08:20, ASX 10:22, Tokyo/HK/India/B3 open+20)
python3 tfo_watchlist.py fire CRYPTO --execute      # any time; rebuilds the crypto list if it is older than 15 min
python3 tfo_watchlist.py fire CRYPTO --execute --setup crypto_hunt   # from the 2-hourly crypto hunt
python3 tfo_watchlist.py report                     # per-setup trades / win rate / gross & net P&L
```
For each candidate, `fire` does four things:
1. It caps the size to the live heat headroom and unreserved powder, both re-read from the ledgers.
2. It previews through the tool.
3. On a size-only failure (5% of today's $vol, or the >$300k tier), it resizes once and previews again.
4. It checks the 2.5× price sanity.

Only after all of that does it call the tool with `--execute`. Every gate is re-run inside the tool, and the tool writes the LOG lines, which `fire` echoes. `--execute` is refused before the first eligible print, more than 120 minutes after the open (unless the list was rebuilt after the open and is <= 15 min old), when the list was built for a different open, or while another fire holds the lock.

**Agent market-open wake.** Run the bank turn first. Then read `reports/ht_watchlist.json` → `markets[MARKET]`. Check that `open.utc` is today's open and `status` is `BUILT`, and note `freezes` and `heat.headroom_notional_usd`. Next run `python3 tfo_watchlist.py fire MARKET --execute` (or `fire CRYPTO --execute`). Report only the printed `LOG ...` lines (or `LOG none`), plus firm equity and powder from `firm_risk.py` if anything filled. The watchlist has no thesis. If you have a catalyst for a name, use `tfo_buy.py` directly with your own `--why`. Candidates are momentum/gap beta by default.

## Rollback
```bash
launchctl bootout gui/$(id -u)/com.tfo.ht-watchlist
rm ~/Library/LaunchAgents/com.tfo.ht-watchlist.plist
rm -rf tfo_watchlist.py README_WATCHLIST.md launchd/com.tfo.ht-watchlist.plist tests_watchlist \
       reports/ht_watchlist.json reports/ht_watchlist_heartbeat.json reports/.ht_watchlist*.lock reports/.ht_fills_tagged.lock
# keep reports/ht_fills_tagged.jsonl (setup history) unless you want it gone
```
No ledger, rail runner or desk module is modified, so nothing else needs to be restored.

## Notes / limits
- Clock: exchange-local opens via zoneinfo (DST-safe). Holiday table covers weekday closures through 2027-01-01. The heartbeat sets `holiday_table_expired`; extend `HOLIDAYS` each quarter.
- Spread comes from Yahoo `info` bid/ask, the same source `tfo_buy` uses. When it is missing, the spread is UNKNOWN and the name is rejected (in both the watchlist and tfo_buy). Non-US names often lack a pre-open bid/ask, so those lists can be thin. Run `build MARKET` again after the open before firing if needed.
- Pre-open sizing uses 5% of the 10-day average $vol. At the first print, tfo_buy checks 5% of TODAY's $vol, and fire steps the size down to fit.

## GIVEBACK DNA (hard, 2026-10-29 17:20 WITA)
GIVEBACK DNA (Brett HARD 2026-10-29 17:17 WITA; spec final 17:20; PAPER): every bot and engine. Never sit through a reverse that gives back open profit. Mechanical early-bank trigger on an open winner BEFORE its normal half-bank: (a) peak >= +0.5R then >= 50% of that peak given back; OR (b) a bar closes through the prior bar's low (shorts: high) on >= 2x average volume; OR (c) a logged adverse news item. Action: bank half or more at the live mark, residual stop to BE then normal trail. Noise (no trigger) is not a turn. Never forces an entry; never sells what is not held. Every early bank logs reason + rail counterfactual (what the normal rail would have done). Normal half-bank = net A$ >= 1R of ORIGINAL entry risk after costs (NO +1% leg); Dex trend seat 1.5R.
