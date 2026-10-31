"""One paper order router. PAPER ONLY. No broker, no live orders.

Limit entries fill at the limit when a print trades through it (the desk's
true-limit rule in book.py). A stop-limit entry that is more than
SLIP_COLLAR_BPS (15, the desk collar) through the plan stays working.
Protective stops always fire, including through a gap, and always alert.
New risk is refused when the firm-risk service is unreachable. Stops are
exits and do not consult the service.

Writes
------
  <root>/paper_blotter.json
  <root>/paper_router.jsonl
  <root>/events.jsonl          wake bus reads fill and stop rows
  reports/paper_router_alerts.json
  reports/paper_router_health.json

Verify on the Mac: submit a paper limit, feed a print through it, and read
the alerts file. The veteran and THF books are not opened for writing.
"""
import os

from veteran_desk.exec_speed import firm_risk_service as risk
from veteran_desk.exec_speed.common import append_jsonl, atomic_json, iso, read_json, utc_now

PAPER_ONLY = True
SLIP_COLLAR_BPS = 15.0
ALERT_KEEP = 200


def slip_bps(plan, fill, side):
    """Same sign as book.py: positive means the fill is worse than the plan."""
    if not plan:
        return 0.0
    sign = 1.0 if side == "LONG" else -1.0
    return (float(fill) / float(plan) - 1.0) * sign * 1e4


def _no(reason):
    return {"ok": False, "paper_only": True, "reason": reason, "status": "rejected"}


class PaperRouter:
    def __init__(self, root, reports, risk_query=None):
        self.root = root
        self.reports = reports
        self.blotter_path = os.path.join(root, "paper_blotter.json")
        self.log_path = os.path.join(root, "paper_router.jsonl")
        self.events_path = os.path.join(root, "events.jsonl")
        self.alerts_path = os.path.join(reports, "paper_router_alerts.json")
        self.health_path = os.path.join(reports, "paper_router_health.json")
        self.risk_query = risk_query if risk_query is not None else (lambda: risk.query())
        os.makedirs(root, exist_ok=True)
        os.makedirs(reports, exist_ok=True)

    def _load(self):
        data = read_json(self.blotter_path)
        if not isinstance(data, dict):
            data = {"paper_only": True, "orders": {}}
        data.setdefault("orders", {})
        data["paper_only"] = True
        return data

    def _save(self, data, now=None):
        data["utc"] = iso(now)
        atomic_json(self.blotter_path, data)
        self._health(data, now)

    def _health(self, data, now=None):
        orders = data.get("orders") or {}
        counts = {}
        for row in orders.values():
            counts[row.get("status") or "?"] = counts.get(row.get("status") or "?", 0) + 1
        atomic_json(self.health_path, {
            "paper_only": True, "utc": iso(now), "status": "ok",
            "orders": len(orders), "by_status": counts,
            "live_broker": False,
        })

    def _alert(self, row, now=None):
        append_jsonl(self.log_path, row)
        if row.get("kind") in ("fill", "stop"):
            append_jsonl(self.events_path, {
                "utc": row["utc"], "kind": row["kind"], "desk": row.get("desk"),
                "seat": row.get("seat"), "ticker": row.get("ticker"), "id": row.get("id"),
                "px": row.get("px"),
            })
        cur = read_json(self.alerts_path) or {}
        alerts = [a for a in (cur.get("alerts") or []) if isinstance(a, dict)]
        alerts.append(dict(row, acked=False))
        alerts = alerts[-ALERT_KEEP:]
        atomic_json(self.alerts_path, {
            "paper_only": True, "updated_utc": iso(now),
            "note": "unacknowledged paper fill, stop, and reject alerts",
            "alerts": alerts,
        })

    def _proposal(self, order):
        prop = {
            "desk": order.get("desk") or "veteran",
            "seat": order.get("seat"),
            "ticker": order.get("ticker"),
            "side": order.get("side"),
            "venue": order.get("venue") or "",
            "grade": order.get("grade"),
            "risk_pct": order.get("risk_pct"),
            "notional_aud": order.get("notional_aud"),
            "seat_equity_aud": order.get("seat_equity_aud"),
            "risk_aud": order.get("risk_aud"),
            "cap_binds": bool(order.get("cap_binds")),
            "reduce_only": False,
        }
        if str(prop["venue"]).upper() == "US":
            if order.get("us_beta_usd") is not None:
                prop["us_beta_usd"] = order["us_beta_usd"]
            else:
                sign = 1.0 if order.get("side") == "LONG" else -1.0
                mult = risk.LEVERED.get(str(order.get("ticker") or "").upper(), 1)
                prop["us_beta_usd"] = sign * float(order["limit"]) * float(order["qty"]) * mult
        return prop

    def submit(self, order, now=None):
        """Accept a paper order onto the blotter, or reject it. Never calls a broker."""
        now = utc_now(now)
        if not isinstance(order, dict):
            return _no("bad_order")
        need = ("id", "seat", "ticker", "side", "qty", "order_type", "limit", "stop",
                "grade", "risk_pct", "notional_aud", "seat_equity_aud")
        if any(order.get(k) in (None, "") for k in need):
            return _no("proposal_incomplete")
        if order.get("side") not in ("LONG", "SHORT"):
            return _no("bad_side")
        if order.get("order_type") not in ("limit", "stop_limit"):
            return _no("bad_order_type")
        try:
            limit, stop, qty = float(order["limit"]), float(order["stop"]), float(order["qty"])
        except (TypeError, ValueError):
            return _no("bad_numbers")
        if limit <= 0 or stop <= 0 or qty <= 0:
            return _no("bad_numbers")
        long = order["side"] == "LONG"
        if (long and stop >= limit) or ((not long) and stop <= limit):
            return _no("stop_wrong_side")
        try:
            snap = self.risk_query()
        except Exception:
            snap = {"reachable": False, "ok": False, "reason": "firm_risk_service_unreachable"}
        decision = risk.admit(self._proposal(order), snap if isinstance(snap, dict) else {"reachable": False})
        if not decision.get("ok"):
            row = _no(decision.get("reason") or "risk")
            row.update(id=order.get("id"), utc=iso(now), kind="risk_reject", ticker=order.get("ticker"),
                       seat=order.get("seat"), desk=order.get("desk"))
            self._alert(row, now)
            return row
        data = self._load()
        if order["id"] in data["orders"]:
            return _no("duplicate_id")
        stored = {
            "id": order["id"], "desk": order.get("desk") or "veteran", "seat": order["seat"],
            "ticker": str(order["ticker"]), "side": order["side"], "qty": qty,
            "order_type": order["order_type"], "limit": limit, "stop": stop,
            "grade": order["grade"], "venue": order.get("venue") or "",
            "status": "working", "paper_only": True, "submitted_utc": iso(now),
            "risk_pct": float(order["risk_pct"]), "notional_aud": float(order["notional_aud"]),
            "seat_equity_aud": float(order["seat_equity_aud"]),
            "cap_binds": bool(order.get("cap_binds")), "risk_aud": order.get("risk_aud"),
            "us_beta_usd": order.get("us_beta_usd"),
        }
        data["orders"][order["id"]] = stored
        self._save(data, now)
        append_jsonl(self.log_path, {"utc": iso(now), "kind": "accept", "id": order["id"], "paper_only": True})
        return {"ok": True, "paper_only": True, "status": "working", "id": order["id"], "reason": "pass"}

    def on_print(self, ticker, px, now=None):
        """Apply one tape print. Stops first, then working entries. Returns events."""
        now = utc_now(now)
        try:
            px = float(px)
        except (TypeError, ValueError):
            return []
        if px <= 0:
            return []
        data = self._load()
        events = []
        sym = str(ticker)
        for order in list(data["orders"].values()):
            if order.get("ticker") != sym or order.get("status") != "open":
                continue
            hit = (order["side"] == "LONG" and px <= float(order["stop"])) or (
                order["side"] == "SHORT" and px >= float(order["stop"]))
            if not hit:
                order["mark"] = px
                continue
            order.update(status="closed", exit_px=px, exit_utc=iso(now), exit_reason="stop", mark=px)
            row = {"utc": iso(now), "kind": "stop", "id": order["id"], "desk": order["desk"],
                   "seat": order["seat"], "ticker": sym, "side": order["side"], "px": px,
                   "stop": order["stop"], "paper_only": True,
                   "gap_through": abs(slip_bps(order["stop"], px, order["side"])) > SLIP_COLLAR_BPS}
            self._alert(row, now)
            events.append(row)
        for order in list(data["orders"].values()):
            if order.get("ticker") != sym or order.get("status") != "working":
                continue
            ev = self._try_fill(order, px, now)
            if ev:
                events.append(ev)
                if ev.get("kind") in ("fill", "slip_reject", "risk_block", "fill_through_stop"):
                    self._alert(ev, now)
        for order in list(data["orders"].values()):
            if order.get("ticker") != sym or order.get("status") != "open" or order.get("exit_reason"):
                continue
            hit = (order["side"] == "LONG" and px <= float(order["stop"])) or (
                order["side"] == "SHORT" and px >= float(order["stop"]))
            if not hit:
                continue
            order.update(status="closed", exit_px=px, exit_utc=iso(now), exit_reason="stop", mark=px)
            row = {"utc": iso(now), "kind": "stop", "id": order["id"], "desk": order["desk"],
                   "seat": order["seat"], "ticker": sym, "side": order["side"], "px": px,
                   "stop": order["stop"], "paper_only": True, "same_print": True}
            self._alert(row, now)
            events.append(row)
        self._save(data, now)
        return events

    def _try_fill(self, order, px, now):
        long = order["side"] == "LONG"
        limit = float(order["limit"])
        if order["order_type"] == "limit":
            touched = px <= limit if long else px >= limit
            if not touched:
                return None
            fill = limit
        else:
            triggered = px >= limit if long else px <= limit
            if not triggered:
                return None
            through = slip_bps(limit, px, order["side"])
            if through > SLIP_COLLAR_BPS + 1e-9:
                order["exec_note"] = "through the plan by %.1f bps; still working" % through
                return {"utc": iso(now), "kind": "slip_reject", "id": order["id"], "desk": order["desk"],
                        "seat": order["seat"], "ticker": order["ticker"], "px": px, "limit": limit,
                        "slip_bps": round(through, 2), "paper_only": True, "status": "working"}
            fill = limit
        if (long and fill <= float(order["stop"])) or ((not long) and fill >= float(order["stop"])):
            order["exec_note"] = "fill would be through the stop"
            return {"utc": iso(now), "kind": "fill_through_stop", "id": order["id"], "paper_only": True,
                    "status": "working", "ticker": order["ticker"], "seat": order["seat"]}
        try:
            snap = self.risk_query()
        except Exception:
            snap = {"reachable": False, "ok": False, "reason": "firm_risk_service_unreachable"}
        decision = risk.admit(self._proposal(order), snap if isinstance(snap, dict) else {"reachable": False})
        if not decision.get("ok"):
            order["exec_note"] = decision.get("reason")
            return {"utc": iso(now), "kind": "risk_block", "id": order["id"], "paper_only": True,
                    "status": "working", "reason": decision.get("reason"), "ticker": order["ticker"],
                    "seat": order["seat"], "desk": order["desk"]}
        slip = slip_bps(limit, fill, order["side"])
        order.update(status="open", fill=fill, filled_utc=iso(now), mark=px,
                     slippage_bps=round(slip, 2), paper_only=True)
        return {"utc": iso(now), "kind": "fill", "id": order["id"], "desk": order["desk"],
                "seat": order["seat"], "ticker": order["ticker"], "side": order["side"],
                "px": fill, "print": px, "slip_bps": round(slip, 2), "paper_only": True,
                "status": "open"}
