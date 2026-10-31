# Catalyst stream runbook

Paper desk only. The stream is advisory input for each seat. It does not arm, size, exit, or take a lock that another feed or the book tick needs.

`com.tfo.veteran-desk-news` still runs every 15 minutes. That job keeps the news-regime flag (the only news input that can tighten size) and the book tick. Headline and catalyst delivery is this daemon, on a 20 second cycle, so a new item is in the seat queue inside 60 seconds when the source timestamp is fresh.

## What it reads

Paid sources are tried first. Keys are read at call time from `~/.tfo_secrets/<name>.env` (or the matching environment variable). They are not printed, not written into the queue, and not put in the heartbeat.

| Vendor | File | Variable | What it supplies |
| --- | --- | --- | --- |
| FMP | `fmp.env` | `FMP_API_KEY` | Stock news, press releases, earnings calendar, economic calendar, 8-K search |
| EODHD | `eodhd.env` | `EODHD_API_KEY` | Global news with sentiment, ASX / LSE / Asia earnings |
| Massive | `massive.env` | `MASSIVE_API_KEY` | Benzinga ticker news, then Massive reference news |
| Unusual Whales | `unusual_whales.env` | `UNUSUAL_WHALES_API_KEY` | Headlines and options flow alerts |
| EIA | `eia.env` | `EIA_API_KEY` | Weekly petroleum status level plus the next 10:30 ET release |

Free sources fill a gap when that paid group is missing, backed off, or erroring. These have no paid twin and always run: EDGAR Form 4, ASX announcement pages, LSE RNS via the Investegate RSS, HKEX disclosure (best-effort XML), JPX timely disclosure via the public Yanoshin TDnet mirror, and the RTT FDA calendar (PDUFA dates for the healthcare seat).

Central-bank speeches and rate decisions come from the FMP economic calendar. If that call fails, the Fed, ECB, BoE, RBA and BoC RSS feeds plus the ForexFactory week file and `state/calendar/calendar_latest.json` are the fallback. EIA without a key still leaves a rule-based next Wednesday 10:30 ET release (Thursday when that Wednesday or its Monday is a US holiday).

## Where it writes

All under `state/catalyst_stream/`:

- `queues/<seat>.jsonl` — every item for that seat
- `wake/<seat>.jsonl` and `wake_queue.jsonl` — urgency at or above 70, same cycle
- `heartbeat.json` — liveness, vendor status, latency
- `latency_alert.json` — raised when p95 source-to-seat or in-process hop exceeds 60 seconds (at least 5 samples; source-to-seat ignores items older than 6 hours and future calendar times)
- `backoff.json` — per vendor, 30s then doubling, capped at 15 minutes
- `seen.json` — 72 hour dedupe

The wake files are not `state/vd_engine/wake_queue.jsonl`. Writing there would hold the trading wake runner. A seat pack reads the advisory queue with a local tail. A missing file or a bad line does not fail the pack.

Routing is by ticker (the hand universe and pit groups) and, when there is no ticker, by kind and headline: filings and disclosures to `news_filings`, PDUFA to `healthcare`, EIA to `energy` and `fx_commodities`, speeches and rate decisions to `macro` and `fx_commodities`. An unmatched wire still lands on `news_filings` so it is not dropped.

## Install

```sh
mkdir -p logs
cp launchd/com.tfo.catalyst-stream.plist ~/Library/LaunchAgents/
launchctl bootout gui/$(id -u) ~/Library/LaunchAgents/com.tfo.catalyst-stream.plist 2>/dev/null || true
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.tfo.catalyst-stream.plist
```

KeepAlive restarts the process if it exits. ThrottleInterval is 15 seconds so a crash loop does not spin. One failed vendor does not exit the process.

Check it:

```sh
python3 -m veteran_desk.catalyst_stream status
python3 -m veteran_desk.catalyst_stream once
```

`heartbeat.json` field `ok` true and `cycle_s` well under 60. `vendors.*.status` is `ok`, `inactive` (no key), `backoff`, or `fail`. `blocks_trade` and `blocks_feed` stay false.

Stop it with `launchctl bootout gui/$(id -u) ~/Library/LaunchAgents/com.tfo.catalyst-stream.plist`. The book tick does not depend on this process.

## Latency alert

`latency_alert.json` `alert: true` means the rolling p95 is above 60 seconds. It does not halt entries or exits. A cold start can alert while the last few hours of headlines are still in the 200-sample window; fresh items replace them. Clear the samples by stopping the daemon and removing `latency.json` if you need a clean window after a feed outage. Do not delete seat queues to silence the alert.

## Same story, surprise, and Asia filings

Cross-wire clustering, the surprise score, source attribution, the nightly source card, and the TDnet / HKEXnews collectors are in `veteran_desk/CATALYST_QUALITY_RUNBOOK.md`. They are advisory. A down-weight of a weak source is a proposal after 30 days and is not applied. TDnet and HKEXnews stay on their own cache and backoff so a slow filing host does not stall the other wires.

## When a vendor is down

That vendor's `last_err` is a status code or `parse` or `key missing`. The URL is not stored, because FMP, EODHD and EIA put the key in the query string. The seat keeps receiving every other vendor, and the free fallback for that group runs on the next cycle. Trades and stops do not call this module.
