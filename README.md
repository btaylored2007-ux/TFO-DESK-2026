# tfo-desk (code-only export of MultiVenueDesk)

Clean code-only snapshot for speed work. No ledgers, state, reports, logs, data, backups, DBs, price/candle data, secrets or files >5 MB.

## Speed-critical paths
- `veteran_desk/local_llm.py` — local LLM calls
- `tools/queue_answer_daemon.py` — queue answer daemon
- `veteran_desk/vd_engine.py` — engine loop
- `veteran_desk/desk.py` — desk orchestration
- `veteran_desk/pit.py` — pit / data layer
- `veteran_desk/data_layer.py` — data build

`staging/` and `staged_fixes/` hold in-flight variants of the same files. Original README: `README_ORIGINAL.md`.
