"""Event-driven seat wakes for Veteran Desk and THF. PAPER ONLY.

Cron and the THF engine keep their own clocks. This bus adds wakes when a
price, a news item, or a fill/stop arrives, and it writes an idle heartbeat
so a quiet tape is still visible to the dead-man.

It does not start a seat run unless dispatch is requested. The launchd plist
ships with EXEC_SPEED_DISPATCH unset. Set it, or pass dispatch=True, only
after reports/exec_speed_wake_health.json is fresh.

Caps match vd_engine (6/seat/day, 40/day) and are read from the engine's
wake_counts.json when that file is present. Fills bypass the price debounce
and still respect the cap. Idle wakes do not spend the event cap: at most
one seat per idle interval, and not the same seat inside four hours.

Corrupt local state fails closed (no wake). The heartbeat file is still written.
"""
import datetime as dt
import json
import os
import sys

from veteran_desk.exec_speed.common import append_jsonl, atomic_json, iso, parse_utc, read_json, utc_now

WITA = dt.timezone(dt.timedelta(hours=8))

PAPER_ONLY = True
CAP_SEAT, CAP_TOTAL = 6, 40
PRICE_MOVE = 0.0075
DEBOUNCE_S = {"crypto": 2 * 3600, "default": 4 * 3600}
IDLE_S = 900
SEATS = (
    "crypto", "news_filings", "essentials_utilities", "trend", "price_action",
    "macro", "energy", "tech_ai_infra", "banks", "fx_commodities", "healthcare",
)


def seat_for(sym, kind, explicit=None, holdings=None):
    if explicit in SEATS:
        return explicit
    if holdings and sym in holdings and holdings[sym] in SEATS:
        return holdings[sym]
    text = str(sym or "").upper()
    if text.startswith("KRAKEN:") or text.endswith("-USD") or "/USD" in text:
        return "crypto"
    if "_" in text:
        return "fx_commodities"
    if kind == "news":
        return "news_filings"
    return "price_action"


def holdings_from_book(book):
    out = {}
    for seat, body in ((book or {}).get("seats") or {}).items():
        for pos in (body.get("positions") or {}).values():
            if pos.get("status") in ("open", "armed") and pos.get("ticker") and seat in SEATS:
                out.setdefault(str(pos["ticker"]), seat)
    return out


def desk_cmd(desk, seats, python=None):
    """Argv and env for the sanctioned seat trigger. Does not start it."""
    env = dict(os.environ)
    env["PYTHONUNBUFFERED"] = "1"
    if desk == "thf":
        env["THF_DESK"] = "1"
    else:
        env.pop("THF_DESK", None)
    argv = [python or sys.executable, "-m", "veteran_desk.desk", "run", "--seats=" + ",".join(seats)]
    return argv, env


class WakeBus:
    def __init__(self, root, reports, engine_counts_path=None, mirror_queue=None):
        self.root = root
        self.queue_path = os.path.join(root, "wake_queue.jsonl")
        self.state_path = os.path.join(root, "wake_state.json")
        self.counts_path = os.path.join(root, "wake_counts.json")
        self.health_path = os.path.join(reports, "exec_speed_wake_health.json")
        self.engine_counts_path = engine_counts_path
        self.mirror_queue = mirror_queue
        self.last_px = {}
        self.news_offset = 0
        self.fill_offset = 0
        os.makedirs(root, exist_ok=True)
        os.makedirs(reports, exist_ok=True)

    def _state(self):
        data = read_json(self.state_path)
        if data is None:
            return {"debounce": {}, "idle": {}, "seen_news": [], "last_event_utc": None}
        if not isinstance(data, dict):
            return None
        data.setdefault("debounce", {})
        data.setdefault("idle", {})
        data.setdefault("seen_news", [])
        return data

    def _counts(self, now):
        day = utc_now(now).astimezone(WITA).date().isoformat()
        data = read_json(self.counts_path)
        if data is None:
            return {"day": day, "total": 0, "seat": {}}
        if not isinstance(data, dict) or not isinstance(data.get("total"), int) or isinstance(data.get("total"), bool):
            return None
        if not isinstance(data.get("seat"), dict):
            return None
        if data.get("day") != day:
            return {"day": day, "total": 0, "seat": {}}
        return data

    def _engine_used(self, seat, now):
        data = read_json(self.engine_counts_path) if self.engine_counts_path else None
        if not isinstance(data, dict):
            return 0, 0
        day = utc_now(now).astimezone(WITA).date().isoformat()
        if data.get("day") != day:
            return 0, 0
        try:
            return int((data.get("seat") or {}).get(seat) or 0), int(data.get("total") or 0)
        except (TypeError, ValueError):
            return None

    def _room(self, seat, now):
        local = self._counts(now)
        if local is None:
            return False, "counts_corrupt"
        used = self._engine_used(seat, now)
        if used is None:
            return False, "engine_counts_corrupt"
        seat_n, total_n = used
        if local["total"] + total_n >= CAP_TOTAL:
            return False, "cap_total"
        if int(local["seat"].get(seat) or 0) + seat_n >= CAP_SEAT:
            return False, "cap_seat"
        return True, local

    def _enqueue(self, seat, kind, sym, desk, now, state, counts, extra=None):
        wid = "EVT-%s-%s-%s" % (kind, seat, utc_now(now).strftime("%Y%m%dT%H%M%S"))
        row = {
            "wake_id": wid, "utc": iso(now), "desk": desk, "seats": [seat],
            "status": "queued", "source": "exec_speed", "kind": kind, "sym": sym,
            "paper_only": True,
        }
        if extra:
            row.update(extra)
        append_jsonl(self.queue_path, row)
        if self.mirror_queue and desk == "thf":
            append_jsonl(self.mirror_queue, {
                "wake_id": wid, "utc": row["utc"], "seats": [seat], "status": "queued",
                "source": "exec_speed", "kind": kind,
            })
        counts["seat"][seat] = int(counts["seat"].get(seat) or 0) + 1
        counts["total"] = int(counts["total"]) + 1
        atomic_json(self.counts_path, counts)
        state["debounce"][seat + "|" + str(sym)] = iso(now)
        state["last_event_utc"] = iso(now)
        atomic_json(self.state_path, state)
        return row

    def _debounced(self, state, seat, sym, kind, now):
        if kind in ("fill", "stop"):
            return False
        key = seat + "|" + str(sym)
        prev = (state.get("debounce") or {}).get(key)
        if not prev:
            return False
        try:
            age = (utc_now(now) - parse_utc(prev)).total_seconds()
        except Exception:
            return True
        limit = DEBOUNCE_S["crypto"] if seat == "crypto" else DEBOUNCE_S["default"]
        return age < limit

    def on_price(self, sym, px, seat=None, desk="veteran", holdings=None, now=None, move=PRICE_MOVE):
        now = utc_now(now)
        try:
            px = float(px)
        except (TypeError, ValueError):
            return None
        prev = self.last_px.get(sym)
        self.last_px[sym] = px
        if prev is None or prev <= 0:
            return None
        if abs(px / prev - 1.0) < move:
            return None
        return self._event(seat_for(sym, "price", seat, holdings), "price", sym, desk, now)

    def on_news(self, item, desk="veteran", holdings=None, now=None):
        now = utc_now(now)
        if not isinstance(item, dict):
            return None
        ident = str(item.get("id") or item.get("url") or item.get("title") or "")
        if not ident:
            return None
        state = self._state()
        if state is None:
            self._heartbeat(now, status="degraded", detail="state_corrupt")
            return None
        if ident in state.get("seen_news", []):
            return None
        state["seen_news"] = (state.get("seen_news") or [])[-500:] + [ident]
        atomic_json(self.state_path, state)
        tickers = item.get("tickers") or item.get("ticker") or []
        if isinstance(tickers, str):
            tickers = [tickers]
        sym = tickers[0] if tickers else ident[:40]
        return self._event(seat_for(sym, "news", None, holdings), "news", sym, desk, now)

    def on_fill(self, event, now=None):
        now = utc_now(now)
        if not isinstance(event, dict):
            return None
        kind = event.get("kind") if event.get("kind") in ("fill", "stop") else "fill"
        seat = event.get("seat")
        if seat not in SEATS:
            return None
        desk = event.get("desk") if event.get("desk") in ("veteran", "thf") else "veteran"
        return self._event(seat, kind, event.get("ticker"), desk, now)

    def _event(self, seat, kind, sym, desk, now):
        state = self._state()
        if state is None:
            self._heartbeat(now, status="degraded", detail="state_corrupt")
            return None
        if self._debounced(state, seat, sym, kind, now):
            self._heartbeat(now, status="ok", detail="debounced")
            return None
        ok, info = self._room(seat, now)
        if not ok:
            self._heartbeat(now, status="degraded" if "corrupt" in info else "ok", detail=info)
            return None
        row = self._enqueue(seat, kind, sym, desk, now, state, info)
        self._heartbeat(now, status="ok", detail="woke")
        return row

    def idle(self, now=None, seats=None):
        """Write the heartbeat. Wake at most one quiet seat, outside the event cap."""
        now = utc_now(now)
        state = self._state()
        if state is None:
            self._heartbeat(now, status="degraded", detail="state_corrupt")
            return None
        last = state.get("last_idle_utc")
        if last:
            try:
                if (now - parse_utc(last)).total_seconds() < IDLE_S:
                    self._heartbeat(now, status="ok", detail="idle_waiting")
                    return None
            except Exception:
                self._heartbeat(now, status="degraded", detail="state_corrupt")
                return None
        order = list(seats or SEATS)
        chosen = None
        for seat in order:
            if seat not in SEATS:
                continue
            prev = (state.get("idle") or {}).get(seat)
            if not prev:
                chosen = seat
                break
            try:
                quiet = (now - parse_utc(prev)).total_seconds() >= DEBOUNCE_S["default"]
            except Exception:
                quiet = False
            if quiet:
                chosen = seat
                break
        state["last_idle_utc"] = iso(now)
        row = None
        if chosen:
            state.setdefault("idle", {})[chosen] = iso(now)
            row = {
                "wake_id": "IDLE-%s-%s" % (chosen, now.strftime("%Y%m%dT%H%M%S")),
                "utc": iso(now), "desk": "veteran", "seats": [chosen], "status": "queued",
                "source": "exec_speed", "kind": "idle", "paper_only": True,
            }
            append_jsonl(self.queue_path, row)
        atomic_json(self.state_path, state)
        self._heartbeat(now, status="ok", detail="idle")
        return row

    def poll(self, tape_last, news_path=None, events_path=None, holdings=None, now=None, desk="veteran"):
        """Diff a tape snapshot and tail news + router events. Returns new wakes."""
        now = utc_now(now)
        woke = []
        tape = tape_last if isinstance(tape_last, dict) else (read_json(tape_last) or {})
        if isinstance(tape, dict):
            for sym, row in tape.items():
                if not isinstance(row, dict) or row.get("px") is None:
                    continue
                hit = self.on_price(sym, row["px"], holdings=holdings, desk=desk, now=now)
                if hit:
                    woke.append(hit)
        if news_path:
            woke.extend(self._tail(news_path, "news", holdings, desk, now) or [])
        if events_path:
            for item in self._tail_raw(events_path, "fill"):
                hit = self.on_fill(item, now=now)
                if hit:
                    woke.append(hit)
        self.idle(now=now, seats=list(holdings.values()) if holdings else None)
        return woke

    def _tail_raw(self, path, slot):
        try:
            with open(path) as fh:
                lines = fh.read().splitlines()
        except OSError:
            return []
        attr = "news_offset" if slot == "news" else "fill_offset"
        start = getattr(self, attr)
        setattr(self, attr, len(lines))
        out = []
        for line in lines[start:]:
            if not line.strip():
                continue
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        return out

    def _tail(self, path, kind, holdings, desk, now):
        woke = []
        for item in self._tail_raw(path, kind):
            hit = self.on_news(item, desk=desk, holdings=holdings, now=now)
            if hit:
                woke.append(hit)
        return woke

    def _heartbeat(self, now, status, detail):
        atomic_json(self.health_path, {
            "paper_only": True, "utc": iso(now), "status": status, "detail": detail,
            "stale_after_s": IDLE_S * 2, "pid": os.getpid(),
            "caps": {"seat": CAP_SEAT, "total": CAP_TOTAL},
            "dispatch": os.environ.get("EXEC_SPEED_DISPATCH") == "1",
        })

    def dispatch(self, row, popen, cwd):
        """Start one seat run. `popen` is subprocess.Popen, or a fake in tests."""
        if os.environ.get("EXEC_SPEED_DISPATCH") != "1":
            return None
        argv, env = desk_cmd(row.get("desk") or "veteran", row.get("seats") or [])
        return popen(argv, cwd=cwd, env=env)
