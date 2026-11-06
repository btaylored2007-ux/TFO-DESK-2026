"""Pre-pump stays a hypothesis. Missing evidence is a near-miss, not a made-up watch."""
import datetime as dt
import hashlib
from pathlib import Path

from veteran_desk import book as BK
from veteran_desk import crypto_prepump as CP
from veteran_desk import desk as D

BOOK = "b4453b503f33e34110519bf4d7a9bf391ecc2802edbd87acca3e77dc96b7e891"
REPO = Path(__file__).resolve().parents[2]


def _bars(spec):
    start = dt.datetime(2026, 10, 1, tzinfo=dt.timezone.utc)
    rows = []
    for i, (high, low, close, volume) in enumerate(spec):
        rows.append(dict(utc=(start + dt.timedelta(hours=i)).isoformat(), high=high, low=low,
                         close=close, volume=volume))
    return rows


def _coil():
    spec = []
    for i in range(30):
        spec.append((104, 100, 102, 1000))
    for i in range(12):
        spec.append((100.4, 100.0, 100.2, 50))
    return _bars(spec)


def _hypothesis():
    return dict(id="fixture", proven_edge=False, range_percentile_max=0.40, volume_percentile_max=0.40,
                range_window=8, lookback=12, source="labeled_synthetic_fixture")


def test_chase_and_wide_stop_do_not_become_watches():
    pumped = []
    for i in range(20):
        pumped.append((100 + i, 99 + i, 100 + i, 100))
    for i in range(12):
        pumped.append((140, 139, 139.5, 20))
    report = CP.scan([dict(ticker="INJUSD", bars=_bars(pumped), fixture=True)], _hypothesis())
    assert report["watches"] == []
    assert any(item.get("reason") == "existing_chase_rule" for item in report["near_misses"])
    wide = []
    for i in range(32):
        wide.append((100.2, 100.0, 100.1, 100))
    for i in range(8):
        low = 80 if i == 0 else 100.0
        wide.append((100.2, low, 100.1, 10))
    wide_report = CP.scan([dict(ticker="WIDEUSD", bars=_bars(wide), fixture=True)], _hypothesis())
    assert wide_report["watches"] == []
    assert any(item.get("reason") == "stop_outside_existing_band" for item in wide_report["near_misses"])
    assert any(item.get("stop") == 80 for item in wide_report["near_misses"])
    bare = dict(action="stand_aside", reason="nothing")
    assert CP.seat_contract(bare)["reason"] == "no_prepump_report"
    honest = CP.apply_reply(bare, [dict(ticker="NOBARS")])
    assert honest["crypto_contract"]["ok"] is True and honest["crypto_prepump"]["watches"] == []
    assert honest["confirm"] is False


def test_labeled_coil_can_watch_and_shorts_stay_off():
    report = CP.scan([dict(ticker="KRAKEN:SOLUSD", bars=_coil(), fixture=True)], _hypothesis())
    assert len(report["watches"]) == 1
    watch = report["watches"][0]
    assert watch["side"] == "LONG" and watch["confirm"] is False and watch["proven_edge"] is False
    assert watch["trigger"] == watch["entry"] > watch["stop"] == watch["invalidation"]
    assert watch["evidence"]["taker"] == 0.004 and watch["evidence"]["maker"] == 0.0025
    assert watch["asof"]
    missing = CP.scan([dict(ticker="KRAKEN:SOLUSD", bars=_coil())], None)
    assert missing["watches"] == []
    assert any(item["reason"] == "hypothesis_cutoff_not_supplied" for item in missing["near_misses"])
    shorts = CP.scan([dict(ticker="KRAKEN:XBTUSD", side="SHORT", bars=_coil(), fixture=True)], _hypothesis())
    assert shorts["watches"] == [] and shorts["rejected"][0]["reason"] == "hunt48_shorts_off"
    assert CP.reviews_supplied(str(REPO / "state" / "thf" / "crypto_prepump_reviews" / "20261107"))["claimed"] is False


def test_walk_forward_selects_on_train_and_charges_fees():
    spec = []
    for i in range(25):
        spec.append((104, 100, 102, 800))
    for i in range(10):
        spec.append((100.3, 100.0, 100.1, 40))
    spec.append((101.2, 100.0, 100.8, 40))
    for i in range(15):
        spec.append((108, 100, 106, 900))
    bars = _bars(spec)
    future = [dict(bar) for bar in bars]
    future[-1]["high"] = 999
    before = CP.measure(CP._ordered(bars)[0], 30, 8, 12)
    after = CP.measure(CP._ordered(future)[0], 30, 8, 12)
    assert before[0]["range_high"] == after[0]["range_high"]
    report = CP.walk_forward(bars, grid=[(0.2, 0.2), (0.5, 0.5), (1.0, 1.0)], range_window=8,
                             lookback=12, holdout_frac=0.25, slippage_bps=5)
    assert report["proven_edge"] is False and report["holdout_used_for_selection"] is False
    assert report["survivorship_not_assessed"] is True
    assert report["fees"]["taker"] == 0.004
    if report["holdout"] and report["holdout"]["n"]:
        assert report["holdout"]["lookahead_ok"] is True
    assert hashlib.sha256((REPO / "veteran_desk" / "book.py").read_bytes()).hexdigest() == BOOK
    assert BK.RAIL_BANK_R == 0.7


def test_existing_chase_gate_still_rejects_and_fallback_does_not_invent(monkeypatch):
    f = dict(atr14=1, close=120, sma20=100, ret5=0.2, ret20=0.3, choppy=False, efficiency_ratio20=0.4,
             knowable_utc="2026-11-06T00:00:00+00:00", venue="CRYPTO")
    gate = D.risk_gate_v1(dict(ticker="INJUSD", side="LONG", grade="A", entry=120, stop=118, target=126),
                          f, dict(call="LONG"), dict(factual_kill=False),
                          dt.datetime(2026, 11, 6, 1, tzinfo=dt.timezone.utc), 0)
    assert any("pump/dump chase" in reason for reason in gate["reasons"])
    monkeypatch.setenv("THF_DESK", "1")
    monkeypatch.setattr(D, "ENGINE_WAKE", "wake-1")
    monkeypatch.setattr(D, "_engine_wake", lambda seat: dict(candidates=[]))
    reply = D._code_fallback("crypto")
    assert reply["action"] == "stand_aside" and reply["crypto_prepump"]["watches"] == []
    assert reply["crypto_contract"]["ok"] is True
    assert "PRE-PUMP" in D._crypto_prepump_line(dict(seat="crypto"))
    assert "CP.apply_reply" in Path(REPO / "veteran_desk" / "thf_round.py").read_text()


def test_package_refuses_production_and_blocks_the_live_desk_hash(tmp_path):
    import subprocess
    import sys
    pkg = REPO / "deploy" / "crypto_prepump_20261107"
    stage = tmp_path / "stage"
    for name in ("desk.py", "thf_round.py", "thf_msgdesk.py"):
        dest = stage / "veteran_desk" / name
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil_src = pkg / "base" / "veteran_desk" / name
        dest.write_bytes(shutil_src.read_bytes())
    (stage / "veteran_desk" / "book.py").write_bytes((REPO / "veteran_desk" / "book.py").read_bytes())
    dry = subprocess.run([sys.executable, str(pkg / "install.py"), "--root", str(stage)], capture_output=True, text=True)
    assert dry.returncode == 0, dry.stdout
    assert "REPLACE veteran_desk/desk.py" in dry.stdout
    assert "INSTALL_NEW veteran_desk/crypto_prepump.py" in dry.stdout
    assert "NOT_A_PROVEN_EDGE" in dry.stdout
    refused = subprocess.run([sys.executable, str(pkg / "install.py"), "--root", "/opt/tfo/live/thf", "--staging", "--apply"],
                             capture_output=True, text=True)
    assert refused.returncode == 2 and "REFUSED_ROOT" in refused.stdout
    live = tmp_path / "live"
    target = live / "veteran_desk" / "desk.py"
    target.parent.mkdir(parents=True)
    target.write_bytes(b"live-desk")
    (live / "veteran_desk" / "book.py").write_bytes((REPO / "veteran_desk" / "book.py").read_bytes())
    for name in ("thf_round.py", "thf_msgdesk.py"):
        (live / "veteran_desk" / name).write_bytes((pkg / "base" / "veteran_desk" / name).read_bytes())
    blocked = subprocess.run([sys.executable, str(pkg / "install.py"), "--root", str(live), "--staging", "--apply"],
                             capture_output=True, text=True)
    assert blocked.returncode == 2 and "BLOCKED veteran_desk/desk.py" in blocked.stdout
    assert target.read_bytes() == b"live-desk"
