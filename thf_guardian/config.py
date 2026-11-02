"""Droplet paths. Tests pass an explicit Config rooted at a temp dir."""
import os
from dataclasses import dataclass, field

from thf_guardian import assert_paper

# Per-venue feed-delay floors (minutes). Same numbers as veteran_desk.feed_delay.KNOWN_FLOOR.
# book.VENUE_DELAY_MIN omits ASX; the fill gate therefore treats ASX as 15 min while the
# measured floor is 20. The doctor watches the feed against floor+5 and does not edit the gate.
VENUE_FLOOR_MIN = {
    "LSE": 15, "Euronext": 15, "XETRA": 15, "BME": 15, "SIX": 15, "MILAN": 20, "NORDIC": 0,
    "US": 0, "ASX": 20, "TSE": 20, "HKEX": 15, "KRX": 20, "TWSE": 20, "SGX": 10, "NSE": 15,
    "CRYPTO": 0, "FX": 0,
}
# Venues whose quotes are exchange-delayed. A stale-price block here is a stuck order, not a fresh miss.
DELAYED_VENUES = {v for v, m in VENUE_FLOOR_MIN.items() if m >= 10}

# Approved gate_v2.TH values the lag detector compares. A mismatch is an alert, never a rewrite.
APPROVED_GATE = {
    "rr_min": 1.5,
    "stop_min_atr": 0.5,
    "stop_max_atr_hard": 6.0,
    "adv_hard": 0.05,
    "heat_cap": 0.15,
    "min_value_aud": 500000.0,
    "grades_arm": ["A+", "A"],
    "grade_b_arms": False,
    "risk_max": 0.05,
    "risk_a_default": 0.01,
}

LONG_UNITS = {
    "engine": "tfo-thf-engine.service",
    "dash": "tfo-dash-thf.service",
    "wstape": "tfo-thf-wstape.service",
    "news": "tfo-thf-news.service",
}
TIMERS = {
    "tick": "tfo-thf-tick.timer",
    "feed": "tfo-thf-feed.timer",
    "deadman": "tfo-thf-deadman.timer",
    "health": "tfo-thf-health.timer",
    "overlord": "tfo-thf-overlord.timer",
    "borrow": "tfo-thf-borrow.timer",
    "kraken": "tfo-thf-kraken-daily.timer",
}
RESTARTABLE = {
    "engine": ("tfo-thf-engine.service", "restart"),
    "dash": ("tfo-dash-thf.service", "restart"),
    "dashboard": ("tfo-dash-thf.service", "restart"),
    "feed": ("tfo-thf-feed.service", "start"),
    "wstape": ("tfo-thf-wstape.service", "restart"),
    "tape": ("tfo-thf-wstape.service", "restart"),
    "news": ("tfo-thf-news.service", "restart"),
    "tick": ("tfo-thf-tick.service", "start"),
    "queue-answerer": ("tfo-thf-queue-answerer.service", "restart"),
    "answerer": ("tfo-thf-queue-answerer.service", "restart"),
    "borrow": ("tfo-thf-borrow.service", "start"),
    "kraken": ("tfo-thf-kraken-daily.service", "start"),
}
DRIFT_FILES = (
    "veteran_desk/book.py",
    "veteran_desk/desk.py",
    "veteran_desk/gate_v2.py",
    "veteran_desk/vd_engine.py",
    "veteran_desk/thf_dash.py",
    "run_thf_tick_sgp.sh",
)
DASH_PAGES = ("/", "/desk/phone", "/healthz", "/api/state", "/api/ticker")


@dataclass
class Config:
    live: str = "/opt/tfo/live/thf"
    dash_base: str = "http://127.0.0.1:28870"
    tailscale_base: str = "http://100.120.8.11:18870"
    bridge: str = "http://100.120.8.11:8790"
    inbox_host: str = "127.0.0.1"
    inbox_port: int = 18871
    interval_sec: int = 90
    book_fresh_sec: int = 420
    feed_ok_sec: int = 300
    stuck_after_sec: int = 300
    heal_max: int = 3
    heal_window_sec: int = 1800
    heal_backoff_sec: tuple = (60, 180, 600)
    push_timeout_sec: int = 480
    ai_probe_timeout_sec: int = 8
    lock_stale_sec: int = 720
    queue_overflow: int = 40
    auto_fill_max: int = 3
    paper_only: bool = True

    def __post_init__(self):
        assert_paper()
        if self.inbox_host not in ("127.0.0.1", "::1", "localhost"):
            raise RuntimeError("guardian inbox must bind to localhost only")

    def p(self, *parts):
        return os.path.join(self.live, *parts)

    @property
    def book(self):
        return self.p("state", "thf", "thf_book.json")

    @property
    def engine_kill(self):
        return self.p("state", "thf", "ENGINE_KILL")

    @property
    def kill_events(self):
        return self.p("reports", "kill_events.jsonl")

    @property
    def status_path(self):
        return self.p("state", "thf", "guardian_status.json")

    @property
    def incidents(self):
        return self.p("logs", "guardian_incidents.jsonl")

    @property
    def alerts(self):
        return self.p("state", "thf", "guardian_alerts.jsonl")

    @property
    def thread(self):
        return self.p("state", "thf", "guardian_thread.jsonl")

    @property
    def actions(self):
        return self.p("logs", "guardian_actions.jsonl")

    @property
    def heal_state(self):
        return self.p("state", "thf", "guardian_heal.json")

    @property
    def ai_mode(self):
        return self.p("state", "thf", "seat_ai_mode.json")

    @property
    def takeover(self):
        return self.p("state", "thf", "FUNDER_TAKEOVER")

    @property
    def approved_code(self):
        return self.p("state", "thf", "approved_code.json")

    @property
    def funder_messages(self):
        return self.p("state", "funder_messages.jsonl")

    @property
    def funder_queue(self):
        return self.p("state", "thf", "funder_queue.json")

    @property
    def feed_active(self):
        return self.p("state", "thf", "feed_active.json")

    @property
    def pushes(self):
        return self.p("state", "thf", "guardian_pushes.json")

    @property
    def pending_actions(self):
        return self.p("state", "thf", "guardian_pending_actions.json")

    def seat_pause(self, seat):
        return self.p("state", "thf", "seat_pause", seat)

    def last_good(self, seat):
        return self.p("state", "thf", "seat_last_good", seat + ".json")

    def feed_ok_candidates(self):
        env = os.environ.get("THF_GUARDIAN_FEED_OK")
        paths = [env] if env else []
        paths += [
            self.p(".feed_ok"),
            self.p("state", "thf", ".feed_ok"),
            self.p("state", ".feed_ok"),
            "/opt/tfo/data/.feed_ok",
        ]
        return [p for p in paths if p]


def config_for(root):
    """Temp-dir config for tests. Does not touch /opt/tfo."""
    root = os.fspath(root)
    for rel in ("state/thf", "state/thf/seat_pause", "state/thf/seat_last_good", "state/veteran_desk",
                "state/vd_engine/top10", "state/vd_engine/hb/brains", "logs", "reports", "veteran_desk"):
        os.makedirs(os.path.join(root, rel), exist_ok=True)
    return Config(live=root, dash_base="http://127.0.0.1:9", bridge="http://127.0.0.1:9", inbox_port=0)
