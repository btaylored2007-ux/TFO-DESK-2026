"""Exec-speed paper services. Offline. Temp dirs only. No live books, no brokers."""
import datetime as dt
import json
import os
import sqlite3
import subprocess
import threading
from http.server import ThreadingHTTPServer
from pathlib import Path

SEATCAP_LIVE = True

from veteran_desk.exec_speed import firm_risk_service as R
from veteran_desk.exec_speed import paper_router as PR
from veteran_desk.exec_speed import stream_tape as ST
from veteran_desk.exec_speed import wake_bus as W
from veteran_desk.exec_speed.common import iso, redact, utc_now

T0 = dt.datetime(2026, 10, 31, 1, 0, tzinfo=dt.timezone.utc)
KEY = "TESTKEY-NOT-A-SECRET-123456"


def _book(equity, positions=None, aud=0.65):
    seats = {"banks": {"equity_aud": equity, "cash_aud": equity, "positions": {}}}
    for i, pos in enumerate(positions or []):
        seats["banks"]["positions"]["p%d" % i] = pos
    return {"equity_aud": equity, "audusd": aud, "seats": seats}


def _snap(vd=None, thf=None, oanda=None, firm=None, aud=0.65, now=T0):
    vd = _book(1_000_000) if vd is None else vd
    thf = _book(1_000_000) if thf is None else thf
    oanda = {"open": []} if oanda is None else oanda
    firm = {"level": "OK", "new_entries_halted": False} if firm is None else firm
    snap = R.build_snapshot(vd, thf, oanda, firm, aud, now=now)
    snap["reachable"] = True
    return snap


def _idea(**k):
    base = dict(desk="veteran", seat="banks", ticker="AAPL", side="LONG", venue="US", grade="A",
                risk_pct=0.01, notional_aud=50_000, seat_equity_aud=500_000, risk_aud=5_000,
                us_beta_usd=1_000, cap_binds=False)
    base.update(k)
    return base


def _order(**k):
    base = dict(id="O1", desk="veteran", seat="banks", ticker="AAPL", side="LONG", qty=100,
                order_type="limit", limit=100.0, stop=95.0, grade="A", venue="US",
                risk_pct=0.01, notional_aud=50_000, seat_equity_aud=500_000, risk_aud=5_000,
                us_beta_usd=1_000)
    base.update(k)
    return base


class _Sock:
    def __init__(self, frames):
        self.frames = list(frames)
        self.sent = []
    def send(self, payload):
        self.sent.append(payload)
    def recv(self):
        if not self.frames:
            raise ConnectionError("end")
        return self.frames.pop(0)
    def close(self):
        self.closed = True


def test_floors_match_the_desk_book():
    from veteran_desk import book as BK
    assert R.US_BETA_CAP_PCT == BK.US_BETA_CAP_PCT == 0.60
    assert R.SINGLE_NAME_CAP == BK.SEAT_NOTIONAL_CAP == 0.40
    assert R.RISK_A == BK.RISK_MIN == 0.01
    assert R.RISK_A_CAPBIND == BK.RISK_A_CAPBIND_MIN == 0.015
    assert R.RISK_NON_A == BK.RISK_NON_A_MIN == 0.02
    assert R.HEAT_CAP == BK.HEAT_CAP == 0.15
    assert R.OANDA_RISK_CAP == 0.02 and R.OANDA_MAX_OPEN == 4
    assert PR.SLIP_COLLAR_BPS == BK.SLIP_COLLAR_BPS == 15.0
    assert R.LEVERED["TQQQ"] == BK.LEVERED_ETF_MULT["TQQQ"] == 3


def test_risk_floors_caps_and_oanda():
    ok = R.admit(_idea(), _snap())
    assert ok["ok"] and ok["reason"] == "pass"
    low = R.admit(_idea(risk_pct=0.005, risk_aud=2_500), _snap())
    assert low["reason"] == "below_risk_floor" and low["floor"] == 0.01
    at_cap = R.admit(_idea(notional_aud=200_000, risk_pct=0.01, cap_binds=True), _snap())
    assert at_cap["reason"] == "below_risk_floor" and at_cap["floor"] == 0.015
    bound = R.admit(_idea(notional_aud=200_000, risk_pct=0.015, risk_aud=7_500, cap_binds=True), _snap())
    assert bound["ok"]
    non_a = R.admit(_idea(grade="B", risk_pct=0.015, risk_aud=7_500), _snap())
    assert non_a["reason"] == "below_risk_floor" and non_a["floor"] == 0.02
    assert R.admit(_idea(grade="B", risk_pct=0.02, risk_aud=10_000), _snap())["ok"]
    wide = R.admit(_idea(notional_aud=250_000, risk_pct=0.02, grade="B", risk_aud=10_000), _snap())
    assert wide["reason"] == "single_name_40pct"
    hot_vd = _book(100_000, [dict(status="open", risk_aud=20_000, venue="US", ticker="XOM", side="LONG", shares=1, mark=1)])
    hot = _snap(vd=hot_vd, thf=_book(100_000))
    assert R.admit(_idea(risk_aud=15_000), hot)["reason"] == "firm_heat"
    heavy = _book(1_000_000, [dict(status="open", venue="US", ticker="AAPL", side="LONG", shares=4000, mark=100, risk_aud=1000)])
    over = _snap(vd=heavy)
    assert over["books"]["veteran"]["us_beta_usd"] == 400_000
    assert R.admit(_idea(us_beta_usd=20_000), over)["reason"] == "us_beta_60pct"
    assert R.admit(_idea(side="SHORT", us_beta_usd=-20_000), over)["ok"]
    halted = _snap(firm={"level": "HALT", "new_entries_halted": True})
    assert R.admit(_idea(), halted)["reason"] == "firm_halt_or_heat"
    assert R.admit(_idea(reduce_only=True), halted)["ok"]
    four = _snap(oanda={"open": [{"risk_frac": 0.001}] * 4})
    assert R.admit(_idea(venue="OANDA", desk="oanda", us_beta_usd=0, risk_pct=0.005, risk_aud=100), four)["reason"] == "oanda_max_4_open"
    fat = _snap(oanda={"open": []})
    assert R.admit(_idea(venue="OANDA", desk="oanda", risk_pct=0.03, risk_aud=100, us_beta_usd=0), fat)["reason"] == "oanda_risk_2pct"
    piled = _snap(oanda={"open": [{"risk_frac": 0.015}]})
    assert R.admit(_idea(venue="OANDA", desk="oanda", risk_pct=0.01, risk_aud=100, us_beta_usd=0), piled)["reason"] == "oanda_open_risk_2pct"
    assert R.admit(_idea(venue="OANDA", desk="oanda", risk_pct=0.005, risk_aud=100, us_beta_usd=0), _snap(oanda={"open": [{"risk_frac": 0.0}] * 3}))["ok"]


def test_query_fails_closed_when_missing_stale_or_http_down(tmp_path):
    missing = R.query(path=str(tmp_path / "nope.json"), now=T0)
    assert missing["reachable"] is False and missing["reason"] == "firm_risk_service_unreachable"
    assert R.admit(_idea(), missing)["ok"] is False
    snap = _snap()
    snap["utc"] = iso(T0 - dt.timedelta(minutes=5))
    path = tmp_path / "firm_risk_service.json"
    R.publish(str(path), snap)
    stale = R.query(path=str(path), now=T0)
    assert stale["detail"] == "stale" and R.admit(_idea(), stale)["reason"] == "firm_risk_service_unreachable"
    snap["utc"] = iso(T0)
    R.publish(str(path), snap)
    fresh = R.query(path=str(path), now=T0)
    assert fresh["reachable"] is True and fresh["ok"] is True
    R._Handler.state_path = str(path)
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), R._Handler)
    port = httpd.server_address[1]
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        live = R.query(url="http://127.0.0.1:%d/risk" % port, now=T0, timeout=2)
        assert live["reachable"] is True and live["firm_heat"] == 0
    finally:
        httpd.shutdown()
    down = R.query(url="http://127.0.0.1:1/risk", path=str(path), now=T0, timeout=0.4)
    assert down["reachable"] is False
    assert R.serve.__doc__ or True
    try:
        R.serve(str(path), host="10.0.0.8")
        assert False, "non-loopback bind must refuse"
    except RuntimeError as exc:
        assert "loopback" in str(exc)


def test_books_missing_publish_blocks_new_risk():
    snap = R.build_snapshot(None, None, {"open": []}, {"level": "OK"}, 0.65, now=T0)
    assert snap["ok"] is False and snap["new_entries_allowed"] is False
    assert R.admit(_idea(), snap)["ok"] is False


def test_kraken_and_massive_frames_feed_one_tape(tmp_path):
    tape = ST.Tape(str(tmp_path), rotate_bytes=1)
    raw = json.dumps({"channel": "trade", "data": [{
        "symbol": "BTC/USD", "side": "buy", "price": "64000.5", "qty": "0.01",
        "timestamp": "2026-10-31T01:00:00.000Z", "trade_id": 7}]})
    assert ST.handle_kraken(raw, tape) == 1
    assert tape.latest("BTC/USD")["px"] == 64000.5
    conn = sqlite3.connect(tape.db_path)
    assert conn.execute("select pair, price, trade_id from trades").fetchone() == ("BTC/USD", 64000.5, 7)
    assert ST.handle_kraken(json.dumps({"channel": "heartbeat"}), tape) == 0
    try:
        ST.handle_kraken(json.dumps({"channel": "trade", "data": [{"symbol": "BTC/USD"}]}), tape)
        assert False
    except ST.SchemaDriftError:
        pass
    n = ST.handle_massive(json.dumps([
        {"ev": "T", "sym": "SPY", "p": 500.25, "s": 10, "t": 1},
        {"ev": "Q", "sym": "QQQ", "bp": 400.0, "ap": 400.2, "t": 2},
    ]), tape, now=T0)
    assert n == 2 and tape.latest("SPY")["src"] == "massive"
    assert tape.latest("QQQ")["px"] == 400.1
    assert os.path.exists(tape.jsonl_path + ".1")
    try:
        ST.handle_massive(json.dumps([{"ev": "status", "status": "auth_failed", "message": KEY}]), tape)
        assert False
    except ST.AuthFailed as exc:
        assert KEY not in str(exc)
    health = tmp_path / "health.json"
    ST.write_health(str(health), {"name": "massive", "status": "auth_failed", "detail": "bad " + KEY}, secrets=(KEY,))
    assert KEY not in health.read_text()
    assert redact("auth " + KEY, (KEY,)) == "auth [redacted]"


def test_stream_reconnects_and_schema_drift_does_not(tmp_path):
    sleeps = []
    def connect():
        raise ConnectionError("down " + KEY)
    def sleep(seconds):
        sleeps.append(seconds)
        if len(sleeps) >= 3:
            raise StopIteration
    try:
        ST.run_consumer(connect, lambda raw: 0, str(tmp_path / "h.json"), name="kraken", sleep=sleep,
                        max_cycles=5, secrets=(KEY,))
        assert False
    except StopIteration:
        pass
    assert sleeps == [1.0, 2.0, 4.0]
    assert KEY not in (tmp_path / "h.json").read_text()
    bad = _Sock([json.dumps({"channel": "trade", "data": [{"symbol": "ETH/USD"}]})])
    try:
        ST.run_consumer(lambda: bad, lambda raw: ST.handle_kraken(raw, ST.Tape(str(tmp_path / "t2"))),
                        str(tmp_path / "drift.json"), name="kraken", sleep=lambda s: sleeps.append("no"))
        assert False
    except ST.SchemaDriftError:
        pass
    assert "no" not in sleeps
    tape = ST.Tape(str(tmp_path / "live"))
    frame = json.dumps({"channel": "trade", "data": [{
        "symbol": "ETH/USD", "side": "sell", "price": 2500, "qty": 1,
        "timestamp": "2026-10-31T01:00:00.000Z", "trade_id": 1}]})
    def sleep_once(seconds):
        raise StopIteration
    try:
        ST.run_consumer(lambda: _Sock([frame]), lambda raw: ST.handle_kraken(raw, tape),
                        str(tmp_path / "live_health.json"), name="kraken", sleep=sleep_once, max_cycles=1)
    except StopIteration:
        pass
    live = json.loads((tmp_path / "live_health.json").read_text())
    assert live["rows"] == 1 and live["last_msg_utc"] and tape.latest("ETH/USD")["px"] == 2500
    sub = json.loads(ST.kraken_subscribe())
    assert sub["params"]["channel"] == "trade" and "BTC/USD" in sub["params"]["symbol"]
    auth = ST.massive_auth_frame(KEY)
    assert KEY in auth and KEY not in json.dumps({"paper_only": True})


def test_price_news_and_fill_wakes_respect_debounce_and_caps(tmp_path):
    bus = W.WakeBus(str(tmp_path / "state"), str(tmp_path / "reports"))
    assert bus.on_price("AAPL", 100, holdings={"AAPL": "banks"}, now=T0) is None
    woke = bus.on_price("AAPL", 101, holdings={"AAPL": "banks"}, now=T0)
    assert woke["seats"] == ["banks"] and woke["kind"] == "price" and woke["desk"] == "veteran"
    assert bus.on_price("AAPL", 103, holdings={"AAPL": "banks"}, now=T0 + dt.timedelta(hours=3)) is None
    again = bus.on_price("KRAKEN:XBTUSD", 1, now=T0)
    assert again is None
    crypto = bus.on_price("KRAKEN:XBTUSD", 1.02, now=T0)
    assert crypto["seats"] == ["crypto"]
    assert bus.on_price("KRAKEN:XBTUSD", 1.05, now=T0 + dt.timedelta(hours=3))["kind"] == "price"
    news = bus.on_news({"id": "n1", "tickers": ["JPM"], "title": "filing"}, now=T0)
    assert news["seats"] == ["news_filings"]
    assert bus.on_news({"id": "n1", "tickers": ["JPM"], "title": "filing"}, now=T0) is None
    fill = bus.on_fill({"kind": "stop", "seat": "banks", "ticker": "AAPL", "desk": "thf"}, now=T0)
    assert fill["kind"] == "stop" and fill["desk"] == "thf"
    day = T0.astimezone(W.WITA).date().isoformat()
    (tmp_path / "state" / "wake_counts.json").write_text(json.dumps({"day": day, "total": 40, "seat": {}}))
    capped = W.WakeBus(str(tmp_path / "state"), str(tmp_path / "reports"))
    assert capped.on_fill({"kind": "fill", "seat": "energy", "ticker": "XOM"}, now=T0) is None
    health = json.loads((tmp_path / "reports" / "exec_speed_wake_health.json").read_text())
    assert health["detail"] == "cap_total" and health["paper_only"] is True
    (tmp_path / "state" / "wake_counts.json").write_text("[]")
    assert capped.on_fill({"kind": "fill", "seat": "energy", "ticker": "XOM"}, now=T0) is None
    assert json.loads((tmp_path / "reports" / "exec_speed_wake_health.json").read_text())["detail"] == "counts_corrupt"


def test_idle_heartbeat_is_one_seat_and_does_not_storm(tmp_path):
    bus = W.WakeBus(str(tmp_path / "s"), str(tmp_path / "r"))
    first = bus.idle(now=T0)
    assert first["kind"] == "idle" and first["seats"] == ["crypto"]
    assert bus.idle(now=T0 + dt.timedelta(minutes=5)) is None
    nxt = bus.idle(now=T0 + dt.timedelta(seconds=W.IDLE_S))
    assert nxt["seats"] == ["news_filings"]
    health = json.loads((tmp_path / "r" / "exec_speed_wake_health.json").read_text())
    assert health["status"] == "ok" and health["stale_after_s"] == W.IDLE_S * 2
    (tmp_path / "s" / "wake_state.json").write_text("[]")
    assert bus.idle(now=T0 + dt.timedelta(hours=5)) is None
    assert json.loads((tmp_path / "r" / "exec_speed_wake_health.json").read_text())["status"] == "degraded"


def test_dispatch_stays_off_until_asked(tmp_path, monkeypatch):
    argv, env = W.desk_cmd("thf", ["banks", "energy"])
    assert argv[-1] == "--seats=banks,energy" and env["THF_DESK"] == "1"
    argv, env = W.desk_cmd("veteran", ["banks"])
    assert "THF_DESK" not in env
    bus = W.WakeBus(str(tmp_path / "s"), str(tmp_path / "r"))
    row = {"desk": "veteran", "seats": ["banks"], "wake_id": "x"}
    monkeypatch.delenv("EXEC_SPEED_DISPATCH", raising=False)
    assert bus.dispatch(row, lambda *a, **k: (_ for _ in ()).throw(AssertionError("ran")), str(tmp_path)) is None
    monkeypatch.setenv("EXEC_SPEED_DISPATCH", "1")
    seen = {}
    def popen(argv, cwd, env):
        seen.update(argv=argv, cwd=cwd, desk=env.get("THF_DESK"))
        return "proc"
    assert bus.dispatch(row, popen, str(tmp_path)) == "proc"
    assert seen["desk"] is None and seen["argv"][-1] == "--seats=banks"


def test_router_fills_at_limit_stops_on_gap_and_fails_closed(tmp_path):
    snap = _snap()
    router = PR.PaperRouter(str(tmp_path / "state"), str(tmp_path / "reports"), risk_query=lambda: snap)
    assert router.submit(_order(), now=T0)["status"] == "working"
    assert router.on_print("AAPL", 100.4, now=T0) == []
    filled = router.on_print("AAPL", 99.5, now=T0)
    assert filled[0]["kind"] == "fill" and filled[0]["px"] == 100.0
    blotter = json.loads((tmp_path / "state" / "paper_blotter.json").read_text())
    assert blotter["orders"]["O1"]["status"] == "open" and blotter["paper_only"] is True
    alerts = json.loads((tmp_path / "reports" / "paper_router_alerts.json").read_text())
    assert alerts["alerts"][-1]["kind"] == "fill" and alerts["paper_only"] is True
    stopped = router.on_print("AAPL", 90, now=T0)
    assert stopped[0]["kind"] == "stop" and stopped[0]["px"] == 90
    assert json.loads((tmp_path / "state" / "paper_blotter.json").read_text())["orders"]["O1"]["status"] == "closed"
    chase = PR.PaperRouter(str(tmp_path / "c"), str(tmp_path / "cr"), risk_query=lambda: snap)
    assert chase.submit(_order(id="S1", order_type="stop_limit", limit=100, stop=95), now=T0)["ok"]
    rejected = chase.on_print("AAPL", 101, now=T0)
    assert rejected[0]["kind"] == "slip_reject"
    assert json.loads((tmp_path / "c" / "paper_blotter.json").read_text())["orders"]["S1"]["status"] == "working"
    def down():
        raise ConnectionError("service down")
    blind = PR.PaperRouter(str(tmp_path / "b"), str(tmp_path / "br"), risk_query=lambda: snap)
    blind.submit(_order(id="B1"), now=T0)
    blind.on_print("AAPL", 100, now=T0)
    blind.risk_query = down
    assert blind.on_print("AAPL", 94, now=T0)[0]["kind"] == "stop"
    refused = blind.submit(_order(id="B2"), now=T0)
    assert refused["ok"] is False and refused["reason"] == "firm_risk_service_unreachable"
    events = (tmp_path / "b" / "events.jsonl").read_text()
    assert '"kind": "fill"' in events and '"kind": "stop"' in events
    bus = W.WakeBus(str(tmp_path / "w"), str(tmp_path / "wr"))
    woke = bus.poll({}, events_path=str(tmp_path / "b" / "events.jsonl"), now=T0)
    assert {row["kind"] for row in woke} >= {"fill", "stop"}


def test_package_has_no_live_broker_orders():
    root = Path(__file__).resolve().parents[1] / "exec_speed"
    text = "\n".join(p.read_text() for p in root.glob("*.py"))
    for banned in ("api.ibkr.com", "stream-fxtrade.oanda.com", "placeOrder", "orders/create"):
        assert banned not in text
    assert "PAPER ONLY" in text and "wss://ws.kraken.com/v2" in text


def _deploy_tree(tmp, suite="def test_ok():\n    assert True\n"):
    tools = tmp / "tools"
    tools.mkdir(parents=True)
    script = Path("tools/safe_deploy.sh").read_text()
    dest = tools / "safe_deploy.sh"
    dest.write_text(script)
    dest.chmod(0o755)
    (tools / "test_baseline.json").write_text('{"known_failures": []}\n')
    tests = tmp / "veteran_desk" / "tests"
    tests.mkdir(parents=True)
    (tests / "test_exec_speed_20261031.py").write_text(suite)
    (tmp / "hello.py").write_text("x = 1\n")
    (tmp / "staged.py").write_text("x = 2\n")
    return dest


def _run_deploy(tmp, minute, second="0", runtime="450", suite=None):
    script = _deploy_tree(tmp, suite or "def test_ok():\n    assert True\n")
    env = os.environ.copy()
    env.update({
        "SAFE_DEPLOY_MINUTE": str(minute), "SAFE_DEPLOY_SECOND": str(second),
        "SAFE_DEPLOY_RUNTIME_S": str(runtime), "SAFE_DEPLOY_SKIP_VD_TESTS": "1",
        "SAFE_DEPLOY_BASELINE": str(tmp / "tools" / "test_baseline.json"),
    })
    return subprocess.run(["bash", str(script), "--dry-run", "--tag", "t", "staged.py:hello.py"],
                          cwd=tmp, env=env, capture_output=True, text=True, timeout=90)


def test_deploy_window_is_04_through_07(tmp_path):
    early = _run_deploy(tmp_path / "a", "0")
    assert early.returncode == 3 and "tick window" in early.stdout
    late = _run_deploy(tmp_path / "b", "7", second="30")
    assert late.returncode == 3 and "cross the next tick" in late.stdout
    huge = _run_deploy(tmp_path / "c", "5", runtime="700")
    assert huge.returncode == 3 and "does not fit" in huge.stdout
    ok = _run_deploy(tmp_path / "d", "5")
    assert ok.returncode == 0, ok.stdout + ok.stderr
    assert "hard gate passed" in ok.stdout and "DRY RUN passed" in ok.stdout
    assert (tmp_path / "d" / "hello.py").read_text() == "x = 1\n"
    edge = _run_deploy(tmp_path / "e", "7", second="29")
    assert edge.returncode == 0, edge.stdout + edge.stderr


def test_deploy_refuses_and_rolls_back_when_the_hard_suite_fails(tmp_path):
    bad = _run_deploy(tmp_path / "bad", "4", suite="def test_bad():\n    assert False\n")
    assert bad.returncode == 1, bad.stdout + bad.stderr
    assert "hard test gate failed" in bad.stdout and "ROLLED BACK" in bad.stdout
    assert (tmp_path / "bad" / "hello.py").read_text() == "x = 1\n"
    tree = tmp_path / "miss"
    script = _deploy_tree(tree)
    (tree / "veteran_desk" / "tests" / "test_exec_speed_20261031.py").unlink()
    env = os.environ.copy()
    env.update({"SAFE_DEPLOY_MINUTE": "5", "SAFE_DEPLOY_SECOND": "0", "SAFE_DEPLOY_RUNTIME_S": "450",
                "SAFE_DEPLOY_SKIP_VD_TESTS": "1"})
    missing = subprocess.run(["bash", str(script), "--dry-run", "hello.py"], cwd=tree, env=env,
                             capture_output=True, text=True, timeout=30)
    assert missing.returncode == 1 and "hard test suite missing" in missing.stdout
