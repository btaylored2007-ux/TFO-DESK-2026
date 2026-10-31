# Catalyst quality runbook

Paper desk only. This sits on the catalyst stream. It clusters the same story, scores how unexpected it is, and records which source sat behind a paper entry. It does not arm, size, exit, or veto. A missing file, a dead vendor, or a failed translation leaves the stream and the book on their existing path.

Stops, size limits, and exposure caps are unchanged. Nothing in this layer is allowed to block or delay an entry or a stop.

## One story, one wake

Each cycle stamps every headline, then groups copies of the same story:

- The same normalised ticker and the same event (earnings, halt, guidance cut, and the rest), inside a 6 hour window (18 hours for filings).
- Or a close title match. Titles are compared with character shingles and a deterministic MinHash, and with word overlap. Numbered headlines such as "item 0" and "item 1" stay separate.
- A calendar row (earnings date, scheduled EIA, a rate decision on the calendar) does not swallow the later print.

The seat queue gets one line. `sources` lists every wire that carried it. `first_source` and `first_source_utc` are the earliest report, not the one we happened to poll first. `clusters.json` keeps that record for 72 hours. A later copy updates the source list and does not write a second queue line. The seat is woken at most once for that story.

`seen.json` still drops a repeat poll of the same source. It is not the cross-wire cluster.

## Surprise

Each item gets `surprise` from 0 to 100 and `surprise_parts`:

| Part | When it is present | What it measures |
| --- | --- | --- |
| Earnings | FMP or EODHD sent an actual and a consensus (EPS, revenue, or guidance) | How far the print is from the number already on the tape. An in-line print stays low. |
| Novelty | Always | Distance from that ticker's headlines over the last 30 days (`novelty.json`). No history is a mild 55, not a 100. |
| Tape | A US ticker, a Massive key, and a headline less than 25 minutes old | Abnormal price or volume on the first minutes of Massive 1-minute bars. At most 3 symbols a cycle. No key or a failed call leaves this part out. |
| Prior | Always | Event type. A halt, FDA action, guidance cut, M&A, index add, or capital raise starts high. A dividend, a Form 4, or a scheduled print starts low. |

Surprise is folded into the existing urgency. A routine calendar item drops below the wake line (70). A halt or a material 8-K stays above it. A large earnings surprise on an otherwise ordinary headline can wake the seat. Missing parts are skipped. They do not zero the score and they do not stop the cycle.

## Where a paper entry came from

When the book arms a paper entry it reads `clusters.json` and, if a story for that ticker was first reported within 36 hours before the entry (or up to 15 minutes after), it stores the cluster ids and the source list on `tags.catalyst`. The lookup is local. If it raises or finds nothing, the entry is unchanged and the arm proceeds.

The tag is not a ledger write. This layer never opens the book for writing. The nightly score reads closed paper rows only when it is pointed at a book file.

## Nightly source scores

```sh
python3 -m veteran_desk.catalyst_stream score
```

That reads closed paper positions when `reports/veteran_desk_book.json` is present, and writes only under `state/catalyst_stream/`:

- `source_scores.json` — per source and per event: count, hit rate (R > 0), average R, median lead time from `first_source_utc` to the entry
- `source_score_days.json` — the dates a score has been run
- `warroom_card.json` — the card the War Room renders

R is net P&L divided by risk, or the price R when those fields are missing. A print beyond 15R is ignored.

After 30 distinct score dates, a source or event with at least 8 attributed closes and a hit rate under 40% or a negative average R is listed under `proposals` as `down_weight`. Every proposal has `auto_apply: false`. The score file does not change urgency, routing, size, or who gets woken. Applying a down-weight is a separate decision.

The War Room payload includes `catalyst_sources`. The card is the "Catalyst sources" section. It is empty until the first score run. It does not block the rest of the page if the file is missing.

`launchd/com.tfo.catalyst-source-score.plist` runs the score once a day at 21:10 local time. Install it the same way as the stream plist. The stream daemon does not wait for it.

## Asia filings

Japan TDnet and HKEXnews run on every cycle, with the other always-on disclosures.

| Feed | Call | Cache | Gap |
| --- | --- | --- | --- |
| TDnet | `webapi.yanoshin.jp` recent `json2`, then the RSS mirror | 60s | 30s |
| HKEXnews | Title search `lang=E` for the last two Hong Kong dates, then the disclosure RSS | 90s | 45s |

A fresh cache is served without a network call. A failed call reuses a cache younger than six hours. Older than that, the vendor backs off on its own (30s, doubling, cap 15 minutes) and the other vendors still run.

Titles: an English field or a Latin title is kept. Japanese and Chinese filing phrases in the glossary are rendered in English (`title_lang: glossary`). If a `TFO_TRANSLATE_URL` is set, up to three headlines a cycle are sent as a LibreTranslate-style POST. The key is read at call time from `~/.tfo_secrets/translate.env` (`TRANSLATE_API_KEY`) or the environment, sent only as a bearer header, and not written to the queue or the log. Any translator error leaves the original title (`title_lang: untranslated`) and the item is still routed.

Routing, by ticker or sector:

- Quinn: `news_filings`, every filing
- Asia hunts: `.T` to `asia_hunt_jp`, `.HK` to `asia_hunt_hk`. The hunt status file copies the latest rows into `catalyst_advisory`. That block does not arm, size, or demote.
- Sector seat: the routing table in `catalyst_quality.ASIA_SECTOR` (a handful of liquid names) or a sector label on the filing
- Theo: `macro`, when the filing is a bank or energy name, an index change, or the title is policy, BOJ, HKMA, Nikkei, or Hang Seng

A routine filing still has to clear the surprise-adjusted wake line. The queue gets it. The wake file does not, unless the story is actually unexpected.

## What this must not do

- No live orders. The stream and the score command do not import the book tick, the engine wake runner, or the funder queue.
- No ledger writes. Scores and clusters live under `state/catalyst_stream/`.
- No veto. A weak source keeps publishing. A proposal is a proposal.
- Keys stay in `~/.tfo_secrets` and out of `heartbeat.json`, `backoff.json`, and the seat queues.

## Checks

```sh
python3 -m pytest veteran_desk/tests/test_catalyst_quality_20261031.py veteran_desk/tests/test_catalyst_stream_20261031.py -q
python3 -m veteran_desk.catalyst_stream once
python3 -m veteran_desk.catalyst_stream score
```

`once` should show `blocks_trade` and `blocks_feed` false. `clusters.json` should show one id for a story that arrived on two wires, with both names in `sources` and the earlier `first_source_utc`. A halt still appears in `wake/`. A repeated dividend does not.

The chaos test kills every news vendor, including TDnet and HKEXnews, and still arms a paper long and stops an open position. The HTTP count does not move during that tick.
