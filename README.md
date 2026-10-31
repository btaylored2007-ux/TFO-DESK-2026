# tfo-desk (code-only export of MultiVenueDesk)

Clean code-only snapshot for speed work. No ledgers, state, reports, logs, data, backups, DBs, price/candle data, secrets or files >5 MB.

## Speed-critical paths
- `veteran_desk/local_llm.py` — local LLM calls
- `tools/queue_answer_daemon.py` — queue answer daemon
- `veteran_desk/claude_review.py` — Claude advice client (never on the arm path). See `veteran_desk/CLAUDE_BRIDGE.md`
- `veteran_desk/vd_engine.py` — engine loop
- `veteran_desk/desk.py` — desk orchestration
- `veteran_desk/pit.py` — pit / data layer
- `veteran_desk/data_layer.py` — data build

`staging/` and `staged_fixes/` hold in-flight variants of the same files. Original README: `README_ORIGINAL.md`.

## THF engine and feeds (scope added)
- THF: `veteran_desk/vd_engine.py`, `veteran_desk/thf_*.py`, `veteran_desk/run_thf_tick.sh`, `work/stage/thf_dash.py`, `work/stage/vd_engine.py` (staged copy), `staging/thfpar/`
- Feeds: `veteran_desk/feeds.py`, `veteran_desk/feed_delay.py`, `veteran_desk/data_layer.py`, `feed_alarms.py`, `paid_feeds.py`, `paid_bars.py`, `fundamental_feed.py`, `binance_risk_prices.py`, `kraken_futures_risk_prices.py`, `handover/dot_feed.py`, `idea_feeds/`
- Collectors: `ws_tape_collector.py`, `news_collector.py`, `polymarket_collector.py`
- Health checks: `desk_risk_health.py`, `tools/health_overlord.py`, `veteran_desk/thf_dash_overlord.py`
