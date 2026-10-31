"""Shared firm-risk query service. PAPER ONLY. Places no orders.

Other desks call `pretrade` once, before opening new risk. `query` / `admit`
are the pieces it uses. A missing, stale, or unreachable service refuses new
risk (fail closed). Exits and stops always pass: this service never liquidates
and never blocks a reduce-only order. Decisions append to
state/firm_risk_decisions.jsonl. A seat rail below the firm floor is not lifted.

Rails published here match the desk that already trades paper:
  firm heat cap          book.HEAT_CAP (15% open risk / equity)
  US beta cap            book.US_BETA_CAP_PCT (60% of that book's equity)
  single-name notional   book.SEAT_NOTIONAL_CAP (40% of seat equity)
  grade A floor          1% (book.RISK_MIN)
  grade A when 40% binds 1.5% (book.RISK_A_CAPBIND_MIN)
  non-A floor            2% (book.RISK_NON_A_MIN)
  OANDA                  risk <= 2% NAV and <= 4 open (OXF1 trend sleeve)

Usage
-----
  python3 -m veteran_desk.exec_speed risk-once --root "$TMP"
  python3 -m veteran_desk.exec_speed risk-serve
Verify on the Mac: `query()` against reports/firm_risk_service.json is fresh
and `admit` of a 3% OANDA idea returns ok false. Do not point this at a live
broker account.
"""
import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from veteran_desk.exec_speed.common import append_jsonl, atomic_json, iso, parse_utc, read_json, utc_now

PAPER_ONLY = True
HEAT_CAP = 0.15
US_BETA_CAP_PCT = 0.60
SINGLE_NAME_CAP = 0.40
RISK_A = 0.01
RISK_A_CAPBIND = 0.015
RISK_NON_A = 0.02
RISK_MAX = 0.05
OANDA_RISK_CAP = 0.02
OANDA_MAX_OPEN = 4
STALE_SEC = 90
DEFAULT_STATE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "reports", "firm_risk_service.json")

# Same multiples book.LEVERED_ETF_MULT uses for the US beta cap. Data, not a secret.
LEVERED = {
    "TQQQ": 3, "SQQQ": -3, "UPRO": 3, "SPXU": -3, "SPXL": 3, "SPXS": -3,
    "SSO": 2, "SDS": -2, "QLD": 2, "QID": -2, "SOXL": 3, "SOXS": -3,
    "TNA": 3, "TZA": -3, "UDOW": 3, "SDOW": -3, "TECL": 3, "TECS": -3,
    "FAS": 3, "FAZ": -3, "LABU": 3, "LABD": -3, "SH": -1, "PSQ": -1, "DOG": -1,
}


def floor_for(grade, cap_binds=False, venue=None):
    """Minimum risk fraction for a new trade. OANDA is a ceiling, not a floor."""
    if str(venue or "").upper() == "OANDA":
        return None
    grade = str(grade or "")
    if grade == "A" and cap_binds:
        return RISK_A_CAPBIND
    if grade in ("A", "A+"):
        return RISK_A
    return RISK_NON_A


def _closed(reason, detail):
    return {
        "ok": False, "paper_only": True, "reachable": False,
        "new_entries_allowed": False, "exits_allowed": True,
        "reason": reason, "detail": detail,
    }


def book_equity_aud(book):
    if not isinstance(book, dict):
        return 0.0
    raw = book.get("equity_aud")
    if isinstance(raw, (int, float)) and not isinstance(raw, bool) and raw > 0:
        return float(raw)
    total = 0.0
    for seat in (book.get("seats") or {}).values():
        if not isinstance(seat, dict):
            continue
        val = seat.get("equity_aud")
        if not isinstance(val, (int, float)) or isinstance(val, bool):
            val = seat.get("cash_aud")
        if isinstance(val, (int, float)) and not isinstance(val, bool):
            total += float(val)
    return total


def _px(pos):
    for key in ("mark", "fill", "entry"):
        val = pos.get(key)
        if isinstance(val, (int, float)) and not isinstance(val, bool):
            return float(val)
    return None


def us_beta_usd(book):
    """Net US equity beta in USD. Non-US, FX and crypto legs contribute 0."""
    total = 0.0
    for seat in (book or {}).get("seats", {}).values():
        for pos in (seat.get("positions") or {}).values():
            if pos.get("status") != "open":
                continue
            if (pos.get("venue") or "") != "US":
                continue
            if pos.get("asset") in ("FX", "CRYPTO"):
                continue
            px = _px(pos)
            if px is None:
                continue
            sign = 1.0 if pos.get("side") == "LONG" else -1.0
            mult = LEVERED.get(str(pos.get("ticker") or "").upper(), 1)
            total += sign * px * float(pos.get("shares") or 0) * mult
    return total


def open_risk_aud(book):
    risk = 0.0
    for seat in (book or {}).get("seats", {}).values():
        for pos in (seat.get("positions") or {}).values():
            if pos.get("status") in ("open", "armed"):
                try:
                    risk += float(pos.get("risk_aud") or 0)
                except (TypeError, ValueError):
                    return None
    return risk


def _book_view(book, audusd):
    equity = book_equity_aud(book)
    risk = open_risk_aud(book)
    if book is None or risk is None or not equity or not audusd or audusd <= 0:
        return {"ok": False, "equity_aud": equity, "open_risk_aud": risk,
                "heat": None, "us_beta_usd": None, "us_beta_cap_usd": None}
    beta = us_beta_usd(book)
    return {
        "ok": True,
        "equity_aud": round(equity, 2),
        "open_risk_aud": round(risk, 2),
        "heat": round(risk / equity, 6),
        "us_beta_usd": round(beta, 2),
        "us_beta_cap_usd": round(US_BETA_CAP_PCT * equity * float(audusd), 2),
    }


def _oanda_view(oanda):
    """oanda: {open: [{risk_frac}], nav_aud}. Missing ledger fails closed."""
    if not isinstance(oanda, dict):
        return {"ok": False, "open_n": None, "open_risk": None}
    rows = oanda.get("open")
    if not isinstance(rows, list):
        return {"ok": False, "open_n": None, "open_risk": None}
    try:
        risk = sum(float(r.get("risk_frac") or 0) for r in rows if isinstance(r, dict))
    except (TypeError, ValueError):
        return {"ok": False, "open_n": None, "open_risk": None}
    return {"ok": True, "open_n": len(rows), "open_risk": round(risk, 6),
            "risk_cap": OANDA_RISK_CAP, "max_open": OANDA_MAX_OPEN}


def build_snapshot(vd, thf, oanda, firm_risk, audusd, now=None):
    """Pure snapshot. Either paper book missing, or no AUDUSD, fails closed."""
    vd_v = _book_view(vd, audusd)
    thf_v = _book_view(thf, audusd)
    oanda_v = _oanda_view(oanda)
    books_ok = vd_v["ok"] and thf_v["ok"]
    firm_equity = (vd_v["equity_aud"] or 0) + (thf_v["equity_aud"] or 0)
    firm_risk_aud = None
    if vd_v["open_risk_aud"] is not None and thf_v["open_risk_aud"] is not None:
        firm_risk_aud = vd_v["open_risk_aud"] + thf_v["open_risk_aud"]
    heat = (firm_risk_aud / firm_equity) if books_ok and firm_equity else None
    halted = True
    level = "UNKNOWN"
    if isinstance(firm_risk, dict) and firm_risk:
        halted = bool(firm_risk.get("new_entries_halted") or str(firm_risk.get("level") or "").upper() == "HALT")
        level = firm_risk.get("level") or ("HALT" if halted else "OK")
    else:
        level = "FIRM_RISK_UNREADABLE"
    new_ok = books_ok and not halted and heat is not None and heat <= HEAT_CAP + 1e-12
    return {
        "ok": books_ok,
        "paper_only": True,
        "service": "firm_risk_service",
        "utc": iso(now),
        "reachable": True,
        "new_entries_allowed": bool(new_ok),
        "exits_allowed": True,
        "level": level,
        "new_entries_halted": (not books_ok) or halted or (heat is not None and heat > HEAT_CAP + 1e-12),
        "firm_equity_aud": round(firm_equity, 2) if books_ok else None,
        "firm_open_risk_aud": round(firm_risk_aud, 2) if books_ok else None,
        "firm_heat": heat,
        "heat_cap": HEAT_CAP,
        "us_beta_cap_pct": US_BETA_CAP_PCT,
        "single_name_cap": SINGLE_NAME_CAP,
        "floors": {
            "A": RISK_A,
            "A_when_40pct_binds": RISK_A_CAPBIND,
            "A_plus": RISK_A,
            "non_A": RISK_NON_A,
            "hard_cap": RISK_MAX,
            "oanda_risk_cap": OANDA_RISK_CAP,
            "oanda_max_open": OANDA_MAX_OPEN,
        },
        "books": {"veteran": vd_v, "thf": thf_v},
        "oanda": oanda_v,
        "audusd": audusd,
        "reason": "pass" if books_ok else "books_unreadable",
    }


def publish(path, snapshot):
    atomic_json(path, snapshot)
    return snapshot


def query(path=None, url=None, now=None, timeout=1.5, opener=None, stale_sec=STALE_SEC):
    """Read the service. URL set → HTTP only (no file fallback). Else the state file.

    Missing, stale, invalid, or a failed HTTP GET returns reachable false.
    """
    now = utc_now(now)
    if url is None and "FIRM_RISK_URL" in os.environ:
        url = os.environ.get("FIRM_RISK_URL") or None
    if url:
        try:
            data = json.loads(_http_get(url, timeout, opener))
        except Exception as exc:
            return _closed("firm_risk_service_unreachable", type(exc).__name__)
    else:
        data = read_json(path or DEFAULT_STATE)
    if not isinstance(data, dict):
        return _closed("firm_risk_service_unreachable", "missing_or_invalid")
    try:
        published = parse_utc(data.get("utc"))
    except Exception:
        return _closed("firm_risk_service_unreachable", "no_utc")
    age = (now - published).total_seconds()
    if age > stale_sec or age < -5:
        return _closed("firm_risk_service_unreachable", "stale")
    out = dict(data)
    out["reachable"] = True
    out["paper_only"] = True
    out["age_s"] = round(age, 3)
    return out


def _http_get(url, timeout, opener):
    if opener is not None:
        return opener(url, timeout)
    import urllib.request
    with urllib.request.urlopen(url, timeout=timeout) as resp:
        code = getattr(resp, "status", None) or resp.getcode()
        if int(code) != 200:
            raise ConnectionError("http_%s" % code)
        return resp.read().decode()


def _num(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError
    return float(value)


def _opt_num(proposal, key):
    if proposal.get(key) is None or proposal.get(key) == "":
        return None
    return _num(proposal.get(key))


def _seat_floor(proposal, floor, risk_pct):
    """A seat's own lower rail wins. Never lift a size up to the firm floor.

    seat_risk_min below the firm floor replaces that floor (a 0.5% crypto idea
    stays 0.5%). seat_risk_max is a ceiling: a proposal above it is refused,
    not resized. Both keys are optional.
    """
    try:
        seat_max = _opt_num(proposal, "seat_risk_max")
        seat_min = _opt_num(proposal, "seat_risk_min")
    except (TypeError, ValueError):
        return {"ok": False, "exits_allowed": True, "reason": "proposal_incomplete", "paper_only": True}
    if seat_max is not None and risk_pct > seat_max + 1e-12:
        return {"ok": False, "exits_allowed": True, "reason": "above_seat_rail", "paper_only": True,
                "seat_risk_max": seat_max}
    if seat_min is not None and risk_pct + 1e-12 < seat_min:
        return {"ok": False, "exits_allowed": True, "reason": "below_seat_rail", "paper_only": True,
                "floor": seat_min}
    if seat_min is not None and floor is not None and seat_min + 1e-12 < floor:
        return {"_skip_firm_floor": True}
    return None


_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DECISION_LOG = os.path.join(_ROOT, "state", "firm_risk_decisions.jsonl")
QUERY_FN = None
_EXIT_ACTIONS = {"exit", "stop", "flatten", "reduce", "close", "cover", "stop_update"}


def _stop_ok(proposal):
    """A new trade needs a protective stop. Distance or a price on the correct side of entry."""
    stop = proposal.get("stop", proposal.get("stop_px"))
    entry = proposal.get("entry", proposal.get("px", proposal.get("limit")))
    side = str(proposal.get("side") or "").upper()
    try:
        stop_f = float(stop)
    except (TypeError, ValueError):
        try:
            dist = float(proposal.get("stop_dist", proposal.get("stop_pct")))
        except (TypeError, ValueError):
            return False
        return dist > 0
    if not (stop_f > 0):
        return False
    try:
        entry_f = float(entry)
    except (TypeError, ValueError):
        return True
    if side in ("LONG", "BUY"):
        return stop_f < entry_f
    if side in ("SHORT", "SELL"):
        return stop_f > entry_f
    return True


def _log_decision(proposal, decision, now=None, path=None):
    """Append one allow/deny row. Never writes a ledger. A log failure does not change the decision."""
    row = {
        "utc": iso(now),
        "paper_only": True,
        "ok": bool(decision.get("ok")),
        "reason": decision.get("reason"),
        "desk": (proposal or {}).get("desk"),
        "seat": (proposal or {}).get("seat"),
        "ticker": (proposal or {}).get("ticker"),
        "side": (proposal or {}).get("side"),
        "action": (proposal or {}).get("action") or "entry",
        "risk_pct": (proposal or {}).get("risk_pct"),
        "detail": decision.get("detail"),
    }
    try:
        append_jsonl(path or DECISION_LOG, row)
    except Exception:
        pass
    return row


def pretrade(proposal, snapshot=None, now=None, log_path=None, query_fn=None):
    """One shared check before a desk opens new risk. PAPER ONLY.

    Exits and stops (reduce_only, or action exit/stop/close/cover) always pass,
    even when this service is down. A missing, stale, or unreachable snapshot
    refuses new risk. Does not change the proposed size and does not write a ledger.
    Every allow and deny is appended to state/firm_risk_decisions.jsonl.
    """
    now = utc_now(now)
    proposal = dict(proposal or {})
    action = str(proposal.get("action") or "entry").lower()
    if proposal.get("reduce_only") or action in _EXIT_ACTIONS:
        decision = {"ok": True, "exits_allowed": True, "reason": "exit_or_stop", "paper_only": True}
        _log_decision(proposal, decision, now, log_path)
        return decision
    if not _stop_ok(proposal):
        decision = {"ok": False, "exits_allowed": True, "reason": "stop_required", "paper_only": True}
        _log_decision(proposal, decision, now, log_path)
        return decision
    if snapshot is None:
        fn = query_fn or QUERY_FN or query
        try:
            snapshot = fn(now=now)
        except Exception as exc:
            snapshot = _closed("firm_risk_service_unreachable", type(exc).__name__)
    if not isinstance(snapshot, dict):
        snapshot = _closed("firm_risk_service_unreachable", "missing_or_invalid")
    decision = admit(proposal, snapshot)
    _log_decision(proposal, decision, now, log_path)
    return decision


def admit(proposal, snapshot):
    """Return {ok, reason, exits_allowed}. `proposal` is a new paper idea or a reduce.

    Required on a new risk idea: desk, seat, ticker, side, grade, risk_pct,
    notional_aud, seat_equity_aud. US ideas also need us_beta_usd (the router
    fills it from price x qty). Missing numbers fail closed.
    """
    if not isinstance(snapshot, dict):
        return {"ok": False, "exits_allowed": True, "reason": "firm_risk_service_unreachable", "paper_only": True}
    if snapshot.get("reachable") is False:
        return {"ok": False, "exits_allowed": True,
                "reason": snapshot.get("reason") or "firm_risk_service_unreachable", "paper_only": True}
    reduce_only = bool((proposal or {}).get("reduce_only"))
    if reduce_only:
        return {"ok": True, "exits_allowed": True, "reason": "reduce_only", "paper_only": True}
    if snapshot.get("ok") is not True:
        return {"ok": False, "exits_allowed": True,
                "reason": snapshot.get("reason") or "firm_risk_service_not_ok", "paper_only": True}
    if snapshot.get("new_entries_allowed") is not True or snapshot.get("new_entries_halted"):
        return {"ok": False, "exits_allowed": True, "reason": "firm_halt_or_heat", "paper_only": True}
    try:
        risk_pct = _num(proposal.get("risk_pct"))
        notional = _num(proposal.get("notional_aud"))
        seat_eq = _num(proposal.get("seat_equity_aud"))
        risk_aud = _num(proposal["risk_aud"]) if proposal.get("risk_aud") is not None else risk_pct * seat_eq
    except (TypeError, ValueError, AttributeError):
        return {"ok": False, "exits_allowed": True, "reason": "proposal_incomplete", "paper_only": True}
    if seat_eq <= 0 or risk_pct < 0 or notional < 0:
        return {"ok": False, "exits_allowed": True, "reason": "proposal_incomplete", "paper_only": True}
    venue = str(proposal.get("venue") or "")
    desk = str(proposal.get("desk") or "veteran")
    if risk_pct > RISK_MAX + 1e-12:
        return {"ok": False, "exits_allowed": True, "reason": "above_hard_cap_5pct", "paper_only": True}
    name_frac = notional / seat_eq
    equity_venue = venue.upper() not in ("OANDA", "FX", "CRYPTO", "FUT")
    if equity_venue and name_frac > SINGLE_NAME_CAP + 1e-9:
        return {"ok": False, "exits_allowed": True, "reason": "single_name_40pct", "paper_only": True,
                "name_frac": round(name_frac, 4)}
    cap_binds = str(proposal.get("grade")) == "A" and (
        bool(proposal.get("cap_binds")) or name_frac + 1e-9 >= SINGLE_NAME_CAP)
    floor = floor_for(proposal.get("grade"), cap_binds, venue)
    seat_floor = _seat_floor(proposal, floor, risk_pct)
    if isinstance(seat_floor, dict) and seat_floor.get("_skip_firm_floor"):
        floor = None
    elif seat_floor is not None:
        return seat_floor
    if floor is not None and risk_pct + 1e-12 < floor:
        return {"ok": False, "exits_allowed": True, "reason": "below_risk_floor", "paper_only": True,
                "floor": floor, "cap_binds": bool(cap_binds and str(proposal.get("grade")) == "A")}
    if venue.upper() == "OANDA" or desk == "oanda":
        oanda = snapshot.get("oanda") or {}
        if not oanda.get("ok"):
            return {"ok": False, "exits_allowed": True, "reason": "oanda_unreadable", "paper_only": True}
        if risk_pct > OANDA_RISK_CAP + 1e-12:
            return {"ok": False, "exits_allowed": True, "reason": "oanda_risk_2pct", "paper_only": True}
        if int(oanda.get("open_n") or 0) + 1 > OANDA_MAX_OPEN:
            return {"ok": False, "exits_allowed": True, "reason": "oanda_max_4_open", "paper_only": True}
        if float(oanda.get("open_risk") or 0) + risk_pct > OANDA_RISK_CAP + 1e-12:
            return {"ok": False, "exits_allowed": True, "reason": "oanda_open_risk_2pct", "paper_only": True}
    book_key = "thf" if desk == "thf" else "veteran"
    book = (snapshot.get("books") or {}).get(book_key) or {}
    if venue.upper() == "US":
        try:
            add = _num(proposal.get("us_beta_usd"))
        except (TypeError, ValueError):
            return {"ok": False, "exits_allowed": True, "reason": "us_beta_unknown", "paper_only": True}
        cur = book.get("us_beta_usd")
        cap = book.get("us_beta_cap_usd")
        if cur is None or cap is None:
            return {"ok": False, "exits_allowed": True, "reason": "us_beta_unknown", "paper_only": True}
        new = float(cur) + add
        if abs(new) > float(cap) + 1e-6 and abs(new) > abs(float(cur)) + 1e-6:
            return {"ok": False, "exits_allowed": True, "reason": "us_beta_60pct", "paper_only": True,
                    "us_beta_usd": round(new, 2), "cap_usd": cap}
    firm_eq = snapshot.get("firm_equity_aud")
    firm_open = snapshot.get("firm_open_risk_aud")
    if not firm_eq or firm_open is None:
        return {"ok": False, "exits_allowed": True, "reason": "firm_heat_unknown", "paper_only": True}
    new_heat = (float(firm_open) + risk_aud) / float(firm_eq)
    if new_heat > HEAT_CAP + 1e-12 and risk_aud > 0:
        return {"ok": False, "exits_allowed": True, "reason": "firm_heat", "paper_only": True,
                "firm_heat": round(new_heat, 6)}
    return {"ok": True, "exits_allowed": True, "reason": "pass", "paper_only": True, "floor": floor}


def load_inputs(base):
    """Read the paper books the desks already write. Missing file → None (fail closed)."""
    def _one(path):
        data = read_json(path)
        return data if isinstance(data, dict) else None
    vd = _one(os.path.join(base, "reports", "veteran_desk_book.json"))
    thf = _one(os.path.join(base, "state", "thf", "thf_book.json"))
    firm = _one(os.path.join(base, "reports", "firm_risk.json"))
    oanda_path = os.path.join(base, "state", "exec_speed", "oanda_open.json")
    oanda = _one(oanda_path)
    if oanda is None:
        oanda = _oanda_from_sleeve(os.path.join(base, "state", "oanda", "stats.json"))
    aud = None
    for src in (vd, thf):
        if isinstance(src, dict) and isinstance(src.get("audusd"), (int, float)):
            aud = float(src["audusd"])
            break
        if isinstance(src, dict) and isinstance(src.get("audusd_at_seed"), (int, float)):
            aud = float(src["audusd_at_seed"])
            break
    return vd, thf, oanda, firm, aud


def _oanda_from_sleeve(path):
    """Best-effort read of an existing OANDA stats file. Unknown shape → None."""
    data = read_json(path)
    if not isinstance(data, dict):
        return None
    rows = data.get("open") or data.get("trades")
    if not isinstance(rows, list):
        return None
    open_rows = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        if row.get("status") in ("closed", "cancelled"):
            continue
        frac = row.get("risk_frac", row.get("risk_pct"))
        try:
            frac_f = float(frac)
        except (TypeError, ValueError):
            return None
        if frac_f > 1.5:  # a percent figure, not a fraction
            frac_f = frac_f / 100.0
        open_rows.append({"risk_frac": frac_f, "ins": row.get("ins") or row.get("instrument")})
    return {"open": open_rows, "nav_aud": data.get("nav") or data.get("nav_aud")}


def publish_from_base(base, now=None, audusd=None):
    vd, thf, oanda, firm, aud = load_inputs(base)
    if audusd is not None:
        aud = audusd
    snap = build_snapshot(vd, thf, oanda, firm, aud, now=now)
    path = os.path.join(base, "reports", "firm_risk_service.json")
    publish(path, snap)
    return snap


class _Handler(BaseHTTPRequestHandler):
    """GET /risk and /health. Bound to loopback by the caller. Logs nothing."""
    state_path = DEFAULT_STATE

    def do_GET(self):
        path = self.path.split("?", 1)[0]
        if path not in ("/risk", "/health"):
            self.send_response(404)
            self.end_headers()
            return
        data = read_json(self.state_path)
        if not isinstance(data, dict):
            body = json.dumps(_closed("firm_risk_service_unreachable", "missing_or_invalid")).encode()
            self.send_response(503)
        else:
            body = json.dumps(data).encode()
            self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt, *args):
        return


def serve(path, host="127.0.0.1", port=8766):
    """Blocking loopback HTTP. Host other than loopback is refused."""
    if host not in ("127.0.0.1", "localhost"):
        raise RuntimeError("firm risk service binds loopback only")
    _Handler.state_path = path
    httpd = ThreadingHTTPServer((host, port), _Handler)
    httpd.serve_forever()
    return httpd
