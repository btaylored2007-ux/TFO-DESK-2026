"""Paper session gate. The paper engine fills only a labelled synthetic replay.

A live price licence is not configured. Start without a replay stays stopped.
The word Running is not used: a replay is not a live engine.
"""

import os
import threading

from customer_platform.errors import ResetBlocked
from customer_platform.files import append_jsonl, file_lock, read_json, write_json
from customer_platform.paperbook import new_id

REQUIRED_FEED = "prices"


def default_feeds():
    return [
        {
            "authorized": False,
            "detail": "no price license is configured",
            "feed_id": "prices",
            "fresh": False,
            "label": "Prices",
            "required": True,
            "state": "unconfigured",
        },
        {
            "authorized": False,
            "detail": "no news license is configured",
            "feed_id": "news",
            "fresh": False,
            "label": "News",
            "required": False,
            "state": "unconfigured",
        },
    ]


class Control:
    def __init__(self, tenant_dir, clock, cancel_pending_buys):
        self.dir = os.path.join(tenant_dir, "control")
        self.path = os.path.join(self.dir, "state.json")
        self.audit_path = os.path.join(self.dir, "audit.jsonl")
        self.lock_path = os.path.join(self.dir, "lock")
        self.clock = clock
        self.cancel_pending_buys = cancel_pending_buys
        self.probe = None
        self._thread = threading.Lock()

    def load(self):
        state = read_json(self.path, None)
        if state is None:
            state = _blank()
            write_json(self.path, state)
        return state

    def view(self):
        with self._hold():
            state = self.load()
            feeds = self._current_feeds()
            running = state["state"] == "replay" and not blocked(feeds)
            if state["state"] == "replay" and blocked(feeds):
                headline = "New entries are off. Prices are not available."
            elif running:
                headline = "Paper replay · synthetic prices. Not a live feed."
            else:
                headline = "Stopped · positions protected."
            engine = state.get("engine") or "unconnected"
            if engine == "not_implemented":
                engine = "unconnected"
            return {
                "activity": state["activity"],
                "allow_b_grades": state["allow_b_grades"],
                "engine": engine,
                "engine_note": "Paper replay fills pending orders at a synthetic mark." if engine == "paper_replay" else "No live price source is connected.",
                "feeds": feeds,
                "headline": headline,
                "notice": _customer_notice(state, feeds),
                "orders_sent": int(state.get("orders_sent") or 0),
                "price_source": "synthetic replay, not a live feed" if engine == "paper_replay" else "not connected",
                "queued": len(state.get("queue") or []),
                "state": "replay" if running else "stopped",
                "traders": state["traders"],
            }

    def use_test_probe(self):
        def probe():
            rows = default_feeds()
            for row in rows:
                if row["feed_id"] == REQUIRED_FEED:
                    row.update(authorized=True, detail="test probe, not a live feed", fresh=True, state="ready")
            return rows
        self.probe = probe

    def _current_feeds(self):
        if self.probe:
            return self.probe()
        return default_feeds()

    def start(self):
        with self._hold():
            state = self.load()
            feeds = self._current_feeds()
            state["feeds"] = feeds
            reasons = blocked(feeds)
            if reasons:
                state["engine"] = "unconnected"
                self._write(state, "start_blocked", {"reasons": reasons})
                return {"engine": "unconnected", "headline": "Stopped · positions protected.",
                        "orders_sent": int(state.get("orders_sent") or 0), "reasons": reasons, "started": False, "state": "stopped"}
            state["state"] = "replay"
            state["engine"] = "paper_replay"
            state["replay_armed"] = True
            self._write(state, "start", {"orders_sent": int(state.get("orders_sent") or 0)})
            return {"engine": "paper_replay", "headline": "Paper replay · synthetic prices. Not a live feed.",
                    "orders_sent": int(state.get("orders_sent") or 0), "reasons": [], "started": True, "state": "replay"}

    def stop(self):
        with self._hold():
            state = self.load()
            dropped = list(state.get("queue") or [])
            state["queue"] = []
            state["state"] = "stopped"
            state["engine"] = "unconnected"
            self._write(state, "stop", {"dropped": len(dropped), "flattened": False})
            canceled = self.cancel_pending_buys()
            return {"canceled_entry_orders": canceled, "dropped_queued": len(dropped), "flattened": False,
                    "headline": "Stopped · positions protected", "state": "stopped"}

    def enter(self, callback, *, grade="A", trader_id="desk"):
        with self._hold():
            state = self.load()
            self._check(state, grade, trader_id)
            state["seq"] = int(state.get("seq") or 0) + 1
            write_json(self.path, state)
            return callback(), state["seq"]

    def enqueue(self, payload, *, grade="A", trader_id="desk"):
        with self._hold():
            state = self.load()
            self._check(state, grade, trader_id)
            state["seq"] = int(state.get("seq") or 0) + 1
            item = {"grade": grade, "id": new_id("qe_"), "payload": payload, "seq": state["seq"], "trader_id": trader_id}
            state.setdefault("queue", []).append(item)
            self._write(state, "enqueue", {"id": item["id"], "seq": item["seq"]})
            return item

    def commit_queue(self, callback):
        with self._hold():
            state = self.load()
            if state["state"] not in {"replay", "running"} or blocked(state.get("feeds") or default_feeds()):
                dropped = list(state.get("queue") or [])
                state["queue"] = []
                self._write(state, "queue_dropped", {"count": len(dropped)})
                return {"committed": [], "dropped": [item["id"] for item in dropped]}
            items = list(state.get("queue") or [])
            state["queue"] = []
            committed = []
            for item in items:
                state["seq"] = int(state.get("seq") or 0) + 1
                committed.append({"id": item["id"], "order": callback(item), "seq": state["seq"]})
            self._write(state, "queue_committed", {"count": len(committed)})
            return {"committed": committed, "dropped": []}

    def set_activity(self, level):
        level = int(level)
        if level not in (1, 2, 3):
            raise ResetBlocked("activity", "activity is 1, 2, or 3")
        with self._hold():
            state = self.load()
            state["activity"] = level
            state["settings_version"] = int(state.get("settings_version") or 1) + 1
            self._write(state, "activity", {"to": level, "version": state["settings_version"]})
            return {"activity": level, "version": state["settings_version"]}

    def set_allow_b(self, allowed):
        with self._hold():
            state = self.load()
            state["allow_b_grades"] = bool(allowed)
            state["settings_version"] = int(state.get("settings_version") or 1) + 1
            self._write(state, "allow_b", {"allowed": bool(allowed), "version": state["settings_version"]})
            return {"allow_b_grades": bool(allowed), "version": state["settings_version"]}

    def set_entries(self, trader_id, enabled):
        with self._hold():
            state = self.load()
            found = False
            for trader in state["traders"]:
                if trader["trader_id"] == trader_id:
                    trader["entries_enabled"] = bool(enabled)
                    found = True
            if not found:
                raise ResetBlocked("trader", "trader was not found on this account")
            state["settings_version"] = int(state.get("settings_version") or 1) + 1
            self._write(state, "trader_entries", {"enabled": bool(enabled), "trader_id": trader_id, "version": state["settings_version"]})
            return {"entries_enabled": bool(enabled), "trader_id": trader_id}

    def note_engine_order(self):
        with self._hold():
            state = self.load()
            state["orders_sent"] = int(state.get("orders_sent") or 0) + 1
            self._write(state, "engine_fill", {"orders_sent": state["orders_sent"]})
            return state["orders_sent"]

    def arm_replay(self):
        with self._hold():
            state = self.load()
            state["replay_armed"] = True
            self._write(state, "arm_replay", {"live_feed": False})
            return {"live_feed": False, "source": "synthetic replay, not a live feed"}

    def _check(self, state, grade, trader_id):
        if state["state"] not in {"replay", "running"}:
            raise ResetBlocked("stopped", "Stopped. New entries are blocked. Positions stay protected.")
        feeds = self._current_feeds()
        state["feeds"] = feeds
        if blocked(feeds):
            raise ResetBlocked("feeds", "New entries are off. Prices are not available.")
        grade = str(grade or "A").upper()
        if grade not in {"A", "B"}:
            raise ResetBlocked("grade", "Only A and B grades can be considered.")
        if grade == "B" and (not state["allow_b_grades"] or int(state["activity"]) < 2):
            raise ResetBlocked("grade", "B grades are not allowed at this activity.")
        trader = next((row for row in state["traders"] if row["trader_id"] == trader_id), None)
        if trader is None or not trader["entries_enabled"]:
            raise ResetBlocked("trader_off", "This trader is not taking new entries. Exits still work.")

    def _write(self, state, action, extra):
        write_json(self.path, state)
        row = {"action": action, "state": state["state"], "utc": self.clock()}
        row.update(extra)
        append_jsonl(self.audit_path, row)

    def _hold(self):
        return _Gate(self._thread, self.lock_path)


class _Gate:
    def __init__(self, thread_lock, path):
        self.thread_lock = thread_lock
        self.path = path

    def __enter__(self):
        self.thread_lock.acquire()
        os.makedirs(os.path.dirname(self.path), mode=0o700, exist_ok=True)
        self._file = file_lock(self.path)
        self._file.__enter__()
        return self

    def __exit__(self, exc_type, exc, tb):
        try:
            return self._file.__exit__(exc_type, exc, tb)
        finally:
            self.thread_lock.release()


def _blank():
    return {
        "activity": 2,
        "allow_b_grades": False,
        "engine": "unconnected",
        "feeds": default_feeds(),
        "orders_sent": 0,
        "queue": [],
        "replay_armed": False,
        "seq": 0,
        "settings_version": 1,
        "state": "stopped",
        "traders": [{"entries_enabled": True, "name": "Desk", "trader_id": "desk"}],
    }


def blocked(feeds):
    reasons = []
    for row in feeds:
        if not row.get("required"):
            continue
        if row.get("state") != "ready" or not row.get("fresh") or not row.get("authorized"):
            reasons.append("{0} {1}".format(row.get("label") or "feed", row.get("detail") or row.get("state")))
    return reasons


def _customer_notice(state, feeds):
    if state.get("state") == "replay" and not blocked(feeds):
        return "Synthetic prices only. A live price licence is not configured."
    if blocked(feeds):
        return "Prices are not ready. Start stays off until a synthetic replay is armed. This is not a live feed."
    return ""
