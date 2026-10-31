"""Institutional-speed paper services for the TFO desk. PAPER ONLY.

Additive. Does not replace veteran_desk book, gate, or the THF engine.
No broker orders, no secrets in code or logs.

Modules
-------
wake_bus            event wakes (price / news / fill) plus an idle heartbeat
stream_tape         Kraken v2 + Massive websocket consumers -> file tape
paper_router        one paper router: limit fills, stop alerts, no live orders
firm_risk_service   firm heat, US 60% cap, 40% single-name, per-trade floors

Deploy on the Mac (paper, no live money), in this order
-------------------------------------------------------
1. Firm risk service. Publish reports/firm_risk_service.json and serve
   127.0.0.1 only. Desks that do not call it yet are unchanged. Unreachable
   means new risk is refused.
2. Stream tape. Public Kraken trades and Massive quotes into
   state/exec_speed/. Read-only market data.
3. Paper router. Its own blotter and reports/paper_router_alerts.json.
   It does not write veteran_desk_book.json or thf_book.json.
4. Wake bus. Enqueues under state/exec_speed/. Leave
   EXEC_SPEED_DISPATCH and EXEC_SPEED_MIRROR_ENGINE unset until the health
   file is fresh, then set them so seats actually run.
5. tools/safe_deploy.sh is the gate for every later deploy. The :04–:07
   rule is documented in that script and in OWNERSHIP.md.

Verify each piece with the commands in the module docstrings. All of them
are offline if you pass fixture frames and a temp directory.
"""
PAPER_ONLY = True

__all__ = ["PAPER_ONLY"]
