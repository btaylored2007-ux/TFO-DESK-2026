# Paid marks: what is primary, how to turn it on, how to see 100%

PAPER desk only. No new API keys are required and none are printed. Keys stay where they already live: `~/.tfo_secrets/massive.env` (`MASSIVE_API_KEY`) and `~/.tfo_secrets/eodhd.env` (`EODHD_API_KEY`). The same files `paid_feeds.py` and `veteran_desk/massive.py` already read.

## Primary vs fallback

| Path | Primary when healthy | Fallback |
| --- | --- | --- |
| US main-book marks, stops, rails (`tfo_equity_refresh.mark_fetcher`, command dashboard equity marks, rail refresh for names the cycle did not already quote) | Massive 1-minute aggregates via `paid_feeds.fetch_quote` | Yahoo chart |
| Veteran Desk US `book.live_px` | Massive 5-minute aggregates (unchanged) | Yahoo |
| LSE / EU marks and seat quotes (`.L` `.DE` `.PA` `.AS` `.MC` `.MI` `.SW` and the other Cboe Europe suffixes) | EODHD Cboe Europe websocket (`wss://ws.eodhistoricaldata.com/ws/eu` and `/ws/eu-candles`) when `health()["healthy"]` | FMP 1-minute for `.L` `.AX` `.T` `.HK`, otherwise Yahoo |
| 1m / 5m / 15m bars for seats and the THF feature store | `data/intraday/bars.sqlite` (Massive for US, EODHD intraday for other listings, live 1m candles from the EU websocket) | Daily bars in `pit.sqlite`. Pit is not extended. |
| IBKR borrow gates | `state/ibkr_borrow.json` (existing FTP puller, untouched) | Shorts fail closed when the file is missing or older than 24h |

Yahoo is not removed. A dead socket, a stale print during the cash session, or a missing key sends that symbol back to the fallback.

## Mac enable

From the desk root (`/Users/test/Applications/MultiVenueDesk` on the live machine):

```bash
# EU/LSE realtime consumer. KeepAlive, same interpreter as com.tfo.massive-ws.
# Loads the key itself. Do not put the key in the plist.
cp launchd/com.tfo.eodhd-eu-ws.plist ~/Library/LaunchAgents/
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.tfo.eodhd-eu-ws.plist

# Minute bars every 15 minutes. Exits 0 if a key is missing.
cp launchd/com.tfo.intraday-bars.plist ~/Library/LaunchAgents/
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.tfo.intraday-bars.plist
```

`safe_deploy` these files the usual way before the launchctl bootstrap: `veteran_desk/eodhd_eu_ws.py`, `veteran_desk/intraday_store.py`, `tools/intraday_bars_pull.py`, plus the mark-path edits (`tfo_equity_refresh.py`, `paid_feeds.py`, `command_server.py`, `tfo_ticket.py`, `veteran_desk/book.py`). The plists point at the framework Python 3.14, which already has `websocket-client`.

Optional bid/ask stream (counts again against the websocket symbol cap, default off):

```bash
launchctl setenv EODHD_EU_QUOTES 1   # or add it to the plist EnvironmentVariables
```

Symbol cap defaults to 24 names (open/armed EU positions first, then liquid probes). Override with `EODHD_EU_MAX_SYMBOLS`.

One-shot checks, no secrets in the output:

```bash
python3 -m veteran_desk.eodhd_eu_ws --symbols
python3 -m veteran_desk.eodhd_eu_ws --status
python3 tools/intraday_bars_pull.py
```

## How to tell it is 100%

All of these at once, during the London/EU cash session:

1. `python3 -m veteran_desk.eodhd_eu_ws --status` prints `"healthy": true`, `"why": null`, `"symbols"` > 0, and `venues_live` includes `LSE` (and `XETRA` / `Euronext` when those names are subscribed). Heartbeat `age_sec` stays under 45. A print older than 120s during the session is not used; that name falls back.
2. A US mark on the main book or the command dashboard shows source `Massive 1m aggs (real-time)`, not `Yahoo public chart`. `paid_feeds.STATS["fallback_yahoo"]` stays 0 for those names.
3. `data/intraday/health.json` (written by the puller) has `n_1m`, `n_5m`, and `n_15m` all above 0 and `newest_utc` inside the current session. Seat packs then carry `intraday`. THF feature rows carry `intraday` when a 5m/15m/1m bar exists.
4. The THF dashboard (and the VD skin of the same page) shows a green **IBKR borrow** card (`fresh_24h`, age under 24h) and a green **Paid marks** card. The amber lines "Yahoo EU/UK quotes run 15-20 min late", "No 1m/5m/15m rows", and "IBKR borrow file stale" are gone from What's broken.

Outside the EU cash session the websocket stays primary for the last close (up to 72h) as long as the consumer heartbeat is fresh. Weekend silence is not a Yahoo fallback. A dead launchd job is: `--status` shows `consumer heartbeat stale` and marks go back to Yahoo.

Retention in the companion store, so it does not grow like pit: 1m kept 10 days, 5m kept 40 days, 15m kept 120 days. `pit.sqlite` is still daily bars only.

Tests in this repo are offline. They do not open the websocket and they do not need the keys. Live connectivity is the `--status` check above, on the Mac, after bootstrap.
