# Live source still required — 2026-11-06

These paths were not in the dependency pack `f20945713371e003b601ffede45cb44de2fac77216fcba686ab47223f7b8d505` and were not invented here. Tests that import them stay red. No stub was added.

| Path | Why it is named |
|---|---|
| `veteran_desk/filing_freshness.py` | `veteran_desk/b_tight.py` imports `sec_claim_fresh` from it. Four tests in `tests/test_fillfix_20261103.py` (`F5cCatalyst`) raise `ModuleNotFoundError`. |
| `veteran_desk/small_coin.py` | `veteran_desk/book.py` imports it lazily, and `desk.risk_gate` fail-closes with `small_coin check error ImportError` when it is absent. `test_review_20261030.py::test_half_risk_a_allowed_at_1pct` still fails for that reason. The 1% floor was not changed. |
| `tools/thf_dash_refresh.sh` | `veteran_desk/tests/conftest.py` reads this path. `test_autonomy_integration_20261106.py::test_dashboard_mirror_copies_status_but_not_active_learning_database` raises `FileNotFoundError`. |
| `veteran_desk/tests/test_intent_routing_20261104.py` | Two autonomy integration tests load this file and raise `FileNotFoundError`. |

Present in this tree from the pack, and not listed as missing: `veteran_desk/session_expiry.py`, `veteran_desk/thf_http.py`, `veteran_desk/bridge_maintenance.py`, `veteran_desk/b_tight.py`, `veteran_desk/seat_risk.py`, `veteran_desk/pit_fx_sgp.py`, `tests/test_fillfix_20261103.py`, `tests/test_bank07_20261106.py`.

`staged_fixes/*/book.py` with `RAIL_BANK_R = 1.0` is not the live tick. Do not copy it.
