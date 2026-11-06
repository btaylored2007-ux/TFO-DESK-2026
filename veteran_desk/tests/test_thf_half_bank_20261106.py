"""THF half-bank at net +0.7R. No ledger, no live book, no network."""
import datetime as dt
import json
import os
import subprocess
import sys
from decimal import Decimal

from veteran_desk import thf_half_bank as HB

NOW = dt.datetime(2026, 11, 6, 12, 0, tzinfo=dt.timezone.utc)

def quote(price, **extra):
    base = dict(price=price, asof=NOW.isoformat(), stale=False, session_open=True)
    base.update(extra)
    return base

def long_pos(**extra):
    pos = dict(id="VET-TEST-1", status="open", side="LONG", fill="100", initial_stop="90",
               stop="90", target="130", shares=10, shares_filled=10, shares_requested=10)
    pos.update(extra)
    return pos

def decide(pos, price, entry_cost="0", exit_cost="0", **quote_extra):
    return HB.decide(pos, quote(price, **quote_extra), now=NOW, entry_cost=entry_cost, exit_cost=exit_cost)

def test_threshold_constant_is_net_r_not_percent():
    frozen = HB.frozen_rules()
    assert frozen["threshold_net_r"] == "0.7"
    assert frozen["trigger_basis"] == "net_r"
    assert frozen["percent_of_notional"] is False
    assert frozen["learning_may_modify"] is False
    assert HB.THRESHOLD_NET_R == Decimal("0.7")

def test_long_and_short_boundaries():
    below = decide(long_pos(), "106.99")
    exact = decide(long_pos(), "107")
    above = decide(long_pos(), "107.01")
    assert below["bank"] is False and below["reason"] == "net_below_0.7R"
    assert exact["bank"] is True and exact["reason"] == "net_r_at_least_0.7"
    assert exact["shares_to_bank"] == "5" and exact["residual_shares"] == "5"
    assert above["bank"] is True
    short = dict(id="VET-TEST-S", status="open", side="SHORT", fill="100", initial_stop="110",
                 stop="110", target="70", shares=10, shares_filled=10)
    assert decide(short, "93.01")["bank"] is False
    hit = decide(short, "93")
    assert hit["bank"] is True and hit["immutable_basis"]["side"] == "SHORT"
    assert hit["shares_to_bank"] == "5"

def test_gross_or_two_percent_does_not_replace_net_r():
    # Gross price R is 0.7 but a 1-unit exit cost pulls net under the line.
    missed = decide(long_pos(), "107", exit_cost="1")
    assert missed["bank"] is False
    assert missed["reason"] == "gross_reached_0.7_but_net_did_not"
    assert Decimal(missed["gross_r"]) == Decimal("0.7")
    # +2% of price is only +0.04R when the stop is 50 away. Not a THF bank.
    wide = long_pos(initial_stop="50", stop="50")
    percent = decide(wide, "102")
    assert percent["bank"] is False
    assert percent["percent_rule_ignored"] is True
    assert Decimal(percent["pnl_percent_of_notional"]) == Decimal("0.02")

def test_half_uses_filled_units_once_and_survives_restart():
    pos = long_pos(shares_requested=100, shares_filled=40, shares=40)
    decision = decide(pos, "107")
    assert decision["size_basis"] == "filled_not_requested"
    assert decision["shares_to_bank"] == "20"
    assert decision["requested_shares"] == "100"
    updated, applied = HB.apply_decision(pos, decision)
    assert applied["applied"] is True
    assert updated["shares"] == 20
    assert updated["stop"] == "90" and updated["target"] == "130"
    again = HB.decide(updated, quote("120"), now=NOW, entry_cost="0", exit_cost="0")
    assert again["action"] == "already_banked" and again["bank"] is False
    replay, second = HB.apply_decision(updated, decision)
    assert second["applied"] is False and replay["shares"] == 20
    blob = json.loads(json.dumps(updated))
    restored = HB.decide(blob, quote("130"), now=NOW, entry_cost="0", exit_cost="0")
    assert restored["reason"] == "once_only"

def test_odd_lot_floors_and_single_share_does_not_bank():
    odd = decide(long_pos(shares=11, shares_filled=11), "107")
    assert odd["shares_to_bank"] == "5" and odd["residual_shares"] == "6"
    single = decide(long_pos(shares=1, shares_filled=1), "120")
    assert single["bank"] is False and single["reason"] == "cannot_split_half"

def test_stale_or_missing_cost_never_banks_or_invents_a_price():
    stale = decide(long_pos(), "120", stale=True)
    assert stale["bank"] is False and stale["reason"] == "stale_quote" and stale["net_r"] == "unknown"
    old = decide(long_pos(), "120", asof=(NOW - dt.timedelta(minutes=16)).isoformat())
    assert old["reason"] == "stale_quote"
    closed = decide(long_pos(), "120", session_open=False)
    assert closed["reason"] == "session_closed"
    missing = HB.decide(long_pos(), quote("120"), now=NOW, entry_cost=None, exit_cost=None)
    assert missing["reason"] == "cost_unknown" and missing["bank"] is False

def test_trailed_stop_does_not_shrink_the_initial_risk():
    pos = long_pos(stop="99")  # trailed tighter; initial_stop stays 90
    decision = decide(pos, "107")
    assert decision["bank"] is True
    assert decision["immutable_basis"]["initial_stop"] == "90"
    updated, _ = HB.apply_decision(pos, decision)
    assert updated["stop"] == "99"

def test_module_reimport_keeps_the_rule(tmp_path):
    script = tmp_path / "reload_half_bank.py"
    script.write_text(
        "import json\n"
        "from veteran_desk import thf_half_bank as HB\n"
        "print(json.dumps(HB.frozen_rules()))\n")
    env = dict(os.environ, PYTHONPATH=os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
    ran = subprocess.run([sys.executable, str(script)], check=True, capture_output=True, text=True, env=env)
    loaded = json.loads(ran.stdout)
    assert loaded["threshold_net_r"] == "0.7"
    assert loaded["learning_may_modify"] is False
