"""Veteran desk seat payouts / commission accrual (PAPER trading desk).

Brett's rules, implemented deterministically and idempotently:

1. Proposer seats earn 15% commission on realised net P&L, high-water-mark
   based: commission accrues only on the amount by which a seat's cumulative
   net P&L rises above its prior peak.  Losses are clawed back first -- no
   payout until the seat is back above its prior peak.
2. ``blind_challenger`` and ``devils_advocate`` earn 2% of the avoided loss on
   each correct veto (a vetoed, closed forward trade whose P&L was negative).
   Every seat listed in ``vetoed_by`` is credited once per trade id.
3. ``risk_gate`` is salaried: zero commission, always listed with 0.
4. Commission accrued in calendar month M (UTC) is 50% paid at month end and
   50% deferred, vesting three months after the end of month M.  Deferred,
   unvested amounts are FORFEITED if the seat is cut; the forfeit is credited
   back to the book.
5. Commission is booked as an expense: every newly accrued A$ is subtracted
   from ``B["cash_aud"]`` and added to ``B["commission_expense_aud"]`` exactly
   once.  Processed trade ids and per-seat accruals are persisted in the
   payouts file so re-running never double-charges.

Stdlib only.  Missing/corrupt input files are treated as empty and never
raise.  All money is rounded to 2dp.
"""

from __future__ import annotations

import json
import os
import tempfile
from datetime import date, datetime, timezone
from decimal import Decimal, ROUND_HALF_UP

# ---------------------------------------------------------------------------
# paths / constants
# ---------------------------------------------------------------------------

HERE = os.path.dirname(os.path.abspath(__file__))
BASE = os.path.dirname(HERE)

DEFAULT_FWD_PATH = os.path.join(BASE, "state", "veteran_desk", "forward_trades.json")
DEFAULT_SCORE_PATH = os.path.join(BASE, "state", "veteran_desk_scorecard.json")
DEFAULT_OUT_PATH = os.path.join(BASE, "state", "veteran_desk_payouts.json")

COMMISSION_RATE = 0.15      # proposer commission on new high-water net P&L
VETO_RATE = 0.02            # challenger commission on avoided loss
DEFERRED_FRACTION = 0.50    # half of each month's accrual is deferred
VEST_LAG_MONTHS = 3         # deferred accrual vests 3 months after month end

PROPOSER_SEATS = ("energy", "essentials_utilities", "banks", "healthcare", "macro", "news_filings", "price_action", "crypto", "fx_commodities", "tech_ai_infra", "trend")
CHALLENGER_SEATS = ("blind_challenger", "devils_advocate")
SALARIED_SEATS = ("risk_gate", "head_of_desk")
NO_BOOK_SEATS = ("head_of_desk",)   # TFO HT 03:35: excluded from pay ranking   # head of desk added 2026-10-28: oversight, salaried

SEATS = tuple(list(PROPOSER_SEATS) + list(CHALLENGER_SEATS) + list(SALARIED_SEATS))

ROLES = {}
for _s in PROPOSER_SEATS:
    ROLES[_s] = "proposer"
for _s in CHALLENGER_SEATS:
    ROLES[_s] = "challenger"
for _s in SALARIED_SEATS:
    ROLES[_s] = "salaried_risk_gate"

RULES_TEXT = (
    "PAPER DESK -- no real money moves. "
    "(1) Proposer seats: 15% of realised net P&L after all trading costs, "
    "high-water-mark based -- commission accrues only on cumulative net P&L "
    "above the seat's prior peak, so losses are clawed back and nothing is "
    "paid until the seat exceeds its prior peak. "
    "(2) blind_challenger and devils_advocate: 2% of avoided loss on each "
    "correct veto (vetoed trade closed at a loss), credited once per trade id "
    "to every seat listed in vetoed_by. "
    "(3) risk_gate is salaried -- zero commission, always. "
    "(4) Commission accrued in calendar month M (UTC): 50% paid at month end, "
    "50% deferred and vesting 3 months after the end of month M. Deferred, "
    "unvested commission is forfeited if the seat is cut, and the forfeit is "
    "credited back to the book. "
    "(5) Commission is an expense: newly accrued A$ is deducted from cash and "
    "added to commission expense exactly once (idempotent)."
)


# ---------------------------------------------------------------------------
# small helpers
# ---------------------------------------------------------------------------

CENT = Decimal("0.01")


def _d(value):
    """Exact Decimal from str(value) (no binary-float drift); 0 on junk."""
    try:
        if value is None or isinstance(value, bool):
            return Decimal(0)
        return value if isinstance(value, Decimal) else Decimal(str(value))
    except Exception:
        return Decimal(0)


def _c(value):
    """Decimal rounded to cents, half-up."""
    return _d(value).quantize(CENT, rounding=ROUND_HALF_UP)


def _r2(value):
    """Round to 2dp, half-up, deterministically (via Decimal(str()))."""
    try:
        return float(_c(value))
    except Exception:
        return 0.0


def _f(value, default=0.0):
    """Best-effort float coercion."""
    try:
        if value is None or isinstance(value, bool):
            return default
        return float(value)
    except Exception:
        return default


def _load_json(path):
    """Load JSON, returning None for any missing/unreadable/invalid file."""
    if not path:
        return None
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return None


def _parse_dt(value):
    """Parse an ISO-ish timestamp or date into an aware UTC datetime (or None)."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, date):
        return datetime(value.year, value.month, value.day, tzinfo=timezone.utc)
    text = str(value).strip()
    if not text:
        return None
    if text.upper().endswith(" UTC"):
        text = text[:-4]
    if text.endswith(("Z", "z")):
        text = text[:-1] + "+00:00"
    try:   # epoch seconds
        if text.replace(".", "", 1).isdigit() and len(text.split(".")[0]) >= 9:
            return datetime.fromtimestamp(float(text), timezone.utc)
    except Exception:
        pass
    import re as _re
    text = _re.sub(r"([+-]\d{2})(\d{2})$", r"\1:\2", text)              # +0800 -> +08:00
    m = _re.match(r"^(.*T\d{2}:\d{2}:\d{2})\.(\d+)(.*)$", text)          # py3.9 needs 3 or 6 frac digits
    if m:
        text = m.group(1) + "." + (m.group(2) + "000000")[:6] + m.group(3)
    if "T" not in text and " " in text:
        text = text.replace(" ", "T", 1)
    parsed = None
    try:
        parsed = datetime.fromisoformat(text)
    except Exception:
        for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%d", "%Y/%m/%d", "%Y-%m"):
            try:
                parsed = datetime.strptime(text, fmt)
                break
            except Exception:
                parsed = None
    if parsed is None:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _now_utc(now=None):
    if now is None:
        return datetime.now(timezone.utc)
    parsed = _parse_dt(now)
    return parsed if parsed is not None else datetime.now(timezone.utc)


def _month_key(dt):
    return "%04d-%02d" % (dt.year, dt.month)


def _shift_month(year, month, delta):
    index = (year * 12 + (month - 1)) + delta
    return index // 12, (index % 12) + 1


def _month_start(month_key):
    try:
        year, month = int(month_key[:4]), int(month_key[5:7])
    except Exception:
        return None
    return datetime(year, month, 1, tzinfo=timezone.utc)


def _month_end_instant(month_key):
    """First instant after month M ends (i.e. start of month M+1)."""
    start = _month_start(month_key)
    if start is None:
        return None
    year, month = _shift_month(start.year, start.month, 1)
    return datetime(year, month, 1, tzinfo=timezone.utc)


def _vest_instant(month_key):
    """Deferred accrual from month M vests VEST_LAG_MONTHS after M's end."""
    end = _month_end_instant(month_key)
    if end is None:
        return None
    year, month = _shift_month(end.year, end.month, VEST_LAG_MONTHS)
    return datetime(year, month, 1, tzinfo=timezone.utc)


def _atomic_write_json(path, payload):
    """Write JSON via tmp file + os.replace. Best effort: never raises."""
    if not path:
        return False
    try:
        directory = os.path.dirname(os.path.abspath(path)) or "."
        os.makedirs(directory, exist_ok=True)
        fd, tmp = tempfile.mkstemp(prefix=".payouts.", suffix=".tmp", dir=directory)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(payload, fh, indent=2, sort_keys=True, default=str)
                fh.write("\n")
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp, path)
            return True
        except Exception:
            try:
                if os.path.exists(tmp):
                    os.unlink(tmp)
            except Exception:
                pass
            return False
    except Exception:
        return False


def _blank_seat_state():
    return {
        "cumulative_net_aud": 0.0,
        "peak_net_aud": 0.0,
        "months": {},             # "YYYY-MM" -> commission accrued in that month
        "ytd": {},                # "YYYY"    -> realised net P&L in that year
        "avoided": {},            # "YYYY"    -> avoided loss credited in that year
        "forfeited_months": [],   # months whose deferred half has been forfeited
        "trade_count": 0,
        "veto_count": 0,
    }


def _coerce_seat_state(raw):
    state = _blank_seat_state()
    if not isinstance(raw, dict):
        return state
    state["cumulative_net_aud"] = _f(raw.get("cumulative_net_aud"))
    state["peak_net_aud"] = _f(raw.get("peak_net_aud"))
    state["trade_count"] = int(_f(raw.get("trade_count")))
    state["veto_count"] = int(_f(raw.get("veto_count")))
    for key, dest in (("months", "months"), ("ytd", "ytd"), ("avoided", "avoided")):
        source = raw.get(key)
        if isinstance(source, dict):
            for k, v in source.items():
                state[dest][str(k)] = _f(v)
    forfeited = raw.get("forfeited_months")
    if isinstance(forfeited, (list, tuple, set)):
        state["forfeited_months"] = sorted({str(m) for m in forfeited})
    return state


# ---------------------------------------------------------------------------
# input readers
# ---------------------------------------------------------------------------

def _read_closed_trades(B):
    """Normalise B["closed"] into a deterministically ordered list."""
    raw = B.get("closed") if isinstance(B, dict) else None
    if not isinstance(raw, (list, tuple)):
        return []
    rows = []
    for index, item in enumerate(raw):
        if not isinstance(item, dict):
            continue
        tid = item.get("id")
        tid = str(tid) if tid is not None else "closed_%d" % index
        seat = str(item.get("seat") or "unknown")
        closed_dt = _parse_dt(item.get("closed_utc") or item.get("closed"))
        rows.append({
            "id": tid,
            "seat": seat,
            "net_aud": _f(item.get("net_aud")),
            "closed_dt": closed_dt,
            "order": index,
        })
    far_future = datetime(9999, 12, 31, tzinfo=timezone.utc)
    rows.sort(key=lambda r: (r["closed_dt"] or far_future, r["id"], r["order"]))
    return rows


def _read_correct_vetoes(fwd_path):
    """Correct vetoes from forward_trades.json: closed, vetoed, pnl_aud < 0."""
    blob = _load_json(fwd_path)
    trades = None
    if isinstance(blob, dict):
        trades = blob.get("trades")
        if not isinstance(trades, dict) and isinstance(blob.get("forward_trades"), dict):
            trades = blob.get("forward_trades")
    if not isinstance(trades, dict):
        return []
    out = []
    for tid in sorted(trades.keys(), key=lambda k: str(k)):
        row = trades[tid]
        if not isinstance(row, dict):
            continue
        if not bool(row.get("vetoed")):
            continue
        st = str(row.get("status") or "").strip().lower()
        if st == "vetoed":   # vetofix 2026-10-29: vetoed rows carry lifecycle in shadow_status
            st = str(row.get("shadow_status") or "").strip().lower()
        if st != "closed":
            continue
        pnl = _f(row.get("pnl_aud"))
        if pnl >= 0:
            continue
        seats = row.get("vetoed_by")
        if isinstance(seats, str):
            seats = [seats]
        if not isinstance(seats, (list, tuple, set)):
            seats = []
        clean, seen = [], set()
        for seat in seats:
            name = str(seat or "").strip()
            if not name or name in seen or name in SALARIED_SEATS:
                continue
            seen.add(name)
            clean.append(name)
        if not clean:
            continue
        out.append({
            "id": str(tid),
            "avoided_loss_aud": -pnl,
            "seats": clean,
            "closed_dt": _parse_dt(row.get("closed") or row.get("closed_utc")),
        })
    return out


CUT_DATE_KEYS = ("cut_utc", "cut_date", "verdict_utc", "verdict_date")


def _read_cut_dates(score_path):
    """{seat: cut datetime or None} for seats whose scorecard verdict starts with 'CUT'."""
    blob = _load_json(score_path)
    seats = blob.get("seats") if isinstance(blob, dict) else None
    if not isinstance(seats, dict):
        return {}
    cut = {}
    for seat, row in seats.items():
        verdict = row.get("verdict") if isinstance(row, dict) else row
        if isinstance(verdict, str) and verdict.strip().upper().startswith("CUT"):
            when = None
            if isinstance(row, dict):
                for k in CUT_DATE_KEYS:
                    when = _parse_dt(row.get(k))
                    if when is not None:
                        break
            cut[str(seat)] = when
    return cut


def _read_cut_seats(score_path):
    """Seats whose scorecard verdict starts with 'CUT'."""
    return set(_read_cut_dates(score_path))


class _Lock(object):
    """Exclusive advisory lock on <out>.lock so two accrue() runs cannot interleave read-modify-write."""
    def __init__(self, path):
        self.path = (path or DEFAULT_OUT_PATH) + ".lock"; self.fh = None
    def __enter__(self):
        try:
            import fcntl
            os.makedirs(os.path.dirname(os.path.abspath(self.path)) or ".", exist_ok=True)
            self.fh = open(self.path, "a"); fcntl.flock(self.fh.fileno(), fcntl.LOCK_EX)
        except Exception:
            self.fh = None
        return self
    def __exit__(self, *a):
        try:
            if self.fh is not None:
                import fcntl
                fcntl.flock(self.fh.fileno(), fcntl.LOCK_UN); self.fh.close()
        except Exception:
            pass
        return False


# ---------------------------------------------------------------------------
# core
# ---------------------------------------------------------------------------

def accrue(B, now=None, fwd_path=None, score_path=None, out_path=None):
    """Accrue seat commission, mutate ``B`` in place, write the payouts file (atomic, under an exclusive lock).

    Returns the payload that was written.  Safe and idempotent to re-run.
    """
    with _Lock(out_path or DEFAULT_OUT_PATH):
        return _accrue(B, now, fwd_path, score_path, out_path)


def _accrue(B, now=None, fwd_path=None, score_path=None, out_path=None):
    if not isinstance(B, dict):
        B = {}
    now_dt = _now_utc(now)
    now_iso = now_dt.replace(microsecond=0).isoformat()
    year_key = "%04d" % now_dt.year

    fwd_path = fwd_path or DEFAULT_FWD_PATH
    score_path = score_path or DEFAULT_SCORE_PATH
    out_path = out_path or DEFAULT_OUT_PATH

    for key in ("cash_aud", "commission_expense_aud", "commission_forfeit_credit_aud"):
        B[key] = _f(B.get(key))

    # ---- prior state (idempotency ledger) ---------------------------------
    prior = _load_json(out_path)
    prior_state = prior.get("_state") if isinstance(prior, dict) else None
    if not isinstance(prior_state, dict):
        prior_state = {}

    def _ledger(key):
        raw = prior_state.get(key)
        return {str(v) for v in raw} if isinstance(raw, (list, tuple, set)) else set()

    processed_trades = _ledger("processed_trade_ids")
    processed_vetoes = _ledger("processed_veto_credits")
    expensed_to_date = _f(prior_state.get("expensed_total_aud"))
    forfeit_credited_to_date = _f(prior_state.get("forfeit_credited_aud"))

    raw_seat_state = prior_state.get("seats")
    seat_state = {}
    if isinstance(raw_seat_state, dict):
        for seat, row in raw_seat_state.items():
            seat_state[str(seat)] = _coerce_seat_state(row)
    for seat in SEATS:
        seat_state.setdefault(seat, _blank_seat_state())

    def _seat(name):
        return seat_state.setdefault(str(name), _blank_seat_state())

    # ---- journals: every trade / veto ever seen, so state is REBUILT from full history each run.
    # Fixes: a late-arriving trade with an earlier close date now re-orders the high-water path instead of
    # being applied after later trades. All money in Decimal cents.
    trade_j = prior_state.get("trade_journal") if isinstance(prior_state.get("trade_journal"), dict) else {}
    veto_j = prior_state.get("veto_journal") if isinstance(prior_state.get("veto_journal"), dict) else {}
    legacy_ids = processed_trades - set(trade_j)
    for trade in _read_closed_trades(B):
        if trade["id"] in trade_j:
            continue   # first-seen record wins (idempotent)
        trade_j[trade["id"]] = {"seat": trade["seat"], "net_aud": str(_c(trade["net_aud"])),
                                "closed_utc": (trade["closed_dt"] or now_dt).isoformat()}
        processed_trades.add(trade["id"])
    for veto in _read_correct_vetoes(fwd_path):
        for seat in veto["seats"]:
            key = "%s|%s" % (veto["id"], seat)
            processed_vetoes.add(key)
            if key in veto_j or seat not in CHALLENGER_SEATS:
                continue
            veto_j[key] = {"seat": seat, "avoided_aud": str(_c(veto["avoided_loss_aud"])),
                           "closed_utc": (veto["closed_dt"] or now_dt).isoformat()}
    legacy_seat_state = seat_state if (legacy_ids and not prior_state.get("trade_journal")) else None

    seat_state = {}
    for seat in SEATS:
        seat_state[seat] = _blank_seat_state()
    if legacy_seat_state:   # pre-journal file: keep its rolled-up state as the base (cannot be re-ordered)
        seat_state.update(legacy_seat_state)

    rows = sorted(trade_j.items(), key=lambda kv: (_parse_dt(kv[1]["closed_utc"]) or now_dt, kv[0]))
    dec = {}
    for tid, t in rows:
        seat = str(t["seat"]); state = _seat(seat)
        cum, peak, months = dec.setdefault(seat, [_d(state["cumulative_net_aud"]), _d(state["peak_net_aud"]), {}])
        net = _c(t["net_aud"]); closed_dt = _parse_dt(t["closed_utc"]) or now_dt
        cum = cum + net; state["trade_count"] += 1
        ykey = "%04d" % closed_dt.year
        state["ytd"][ykey] = float(_c(_d(state["ytd"].get(ykey)) + net))
        if seat in PROPOSER_SEATS and cum > peak:
            mkey = _month_key(closed_dt)
            months[mkey] = months.get(mkey, Decimal(0)) + (cum - peak) * _d(COMMISSION_RATE)
            peak = cum
        dec[seat] = [cum, peak, months]
    for seat, (cum, peak, months) in dec.items():
        state = _seat(seat); state["cumulative_net_aud"] = float(_c(cum)); state["peak_net_aud"] = float(_c(peak))
        for mkey, amt in months.items():   # round once per month bucket, not per trade
            state["months"][mkey] = float(_c(_d(state["months"].get(mkey)) + amt))

    for key, v in sorted(veto_j.items()):
        state = _seat(v["seat"]); state["veto_count"] += 1
        credit_dt = _parse_dt(v["closed_utc"]) or now_dt; ykey = "%04d" % credit_dt.year
        state["avoided"][ykey] = float(_c(_d(state["avoided"].get(ykey)) + _d(v["avoided_aud"])))
        credit = _c(_d(v["avoided_aud"]) * _d(VETO_RATE))
        if credit > 0:
            mkey = _month_key(credit_dt)
            state["months"][mkey] = float(_c(_d(state["months"].get(mkey)) + credit))

    # ---- 3/4. cuts + forfeiture keyed to the CUT DATE (not processing time) ----
    # cut date = scorecard cut_utc/cut_date/verdict_utc, else the first run that saw the CUT verdict (persisted).
    cut_dates = _read_cut_dates(score_path)
    cut_first_seen = prior_state.get("cut_first_seen") if isinstance(prior_state.get("cut_first_seen"), dict) else {}
    cut_seats = set(cut_dates) | set(cut_first_seen)
    cut_at = {}
    for seat in sorted(cut_seats):
        when = cut_dates.get(seat) or _parse_dt(cut_first_seen.get(seat)) or now_dt
        cut_first_seen.setdefault(seat, when.isoformat())
        cut_at[seat] = when
    for seat, state in seat_state.items():
        if seat not in cut_at:
            continue
        forfeited = set(state["forfeited_months"])
        for mkey in state["months"]:
            vest_dt = _vest_instant(mkey)
            if vest_dt is None or cut_at[seat] < vest_dt:
                forfeited.add(mkey)
        state["forfeited_months"] = sorted(forfeited)

    # ---- 4. monthly payout buckets ----------------------------------------
    seat_rows = {}
    total_accrued = 0.0
    total_forfeited = 0.0

    for seat in sorted(set(list(SEATS) + list(seat_state.keys()))):
        state = _seat(seat)
        salaried = seat in SALARIED_SEATS

        accrued = paid = pending = deferred = vested = forfeited = 0.0
        schedule = []
        for mkey in sorted(state["months"].keys()):
            amount = _r2(state["months"][mkey])
            if salaried:
                amount = 0.0
            accrued = _r2(accrued + amount)
            immediate = _r2(amount * (1.0 - DEFERRED_FRACTION))
            deferral = _r2(amount - immediate)

            end_dt = _month_end_instant(mkey)
            vest_dt = _vest_instant(mkey)
            month_ended = end_dt is not None and now_dt >= end_dt
            is_forfeited = mkey in set(state["forfeited_months"])
            is_vested = (not is_forfeited) and vest_dt is not None and now_dt >= vest_dt

            if month_ended:
                paid = _r2(paid + immediate)
            else:
                pending = _r2(pending + immediate)
            if is_forfeited:
                forfeited = _r2(forfeited + deferral)
            elif is_vested:
                vested = _r2(vested + deferral)
            else:
                deferred = _r2(deferred + deferral)

            schedule.append({
                "month": mkey,
                "accrued_aud": amount,
                "immediate_aud": immediate,
                "deferred_aud": deferral,
                "paid": bool(month_ended),
                "vests_utc": vest_dt.isoformat() if vest_dt else None,
                "state": "forfeited" if is_forfeited else ("vested" if is_vested else "deferred"),
            })

        cumulative = _r2(state["cumulative_net_aud"])
        peak = _r2(state["peak_net_aud"])

        total_accrued = _r2(total_accrued + accrued)
        total_forfeited = _r2(total_forfeited + forfeited)

        seat_rows[seat] = {
            "seat": seat,
            "role": ROLES.get(seat, "proposer"),
            "salaried": salaried,
            "cut": seat in cut_seats,
            "cut_utc": cut_at[seat].isoformat() if seat in cut_at and cut_at[seat] else None,
            "ytd_earnings_aud": _r2(_f(state["ytd"].get(year_key))),
            "ytd_avoided_loss_aud": _r2(_f(state["avoided"].get(year_key))),
            "cumulative_net_aud": cumulative,
            "peak_net_aud": peak,
            "accrued_commission_aud": accrued,
            "paid_aud": paid,
            "pending_payment_aud": pending,
            "deferred_aud": deferred,
            "vested_aud": vested,
            "forfeited_aud": forfeited,
            "clawback_deficit_aud": _r2(max(0.0, peak - cumulative)),
            "closed_trades": int(state["trade_count"]),
            "correct_vetoes": int(state["veto_count"]),
            "commission_rate": 0.0 if salaried else (
                VETO_RATE if seat in CHALLENGER_SEATS else COMMISSION_RATE),
            "schedule": schedule,
            "last_updated": now_iso,
        }

    # ---- 5. book the expense exactly once ---------------------------------
    new_expense = _r2(total_accrued - expensed_to_date)
    if abs(new_expense) >= 0.01:
        B["cash_aud"] = _r2(B["cash_aud"] - new_expense)
        B["commission_expense_aud"] = _r2(B["commission_expense_aud"] + new_expense)
        expensed_to_date = total_accrued
    else:
        expensed_to_date = _r2(max(expensed_to_date, total_accrued))

    new_forfeit = _r2(total_forfeited - forfeit_credited_to_date)
    if abs(new_forfeit) >= 0.01:
        B["commission_forfeit_credit_aud"] = _r2(B["commission_forfeit_credit_aud"] + new_forfeit)
        B["cash_aud"] = _r2(B["cash_aud"] + new_forfeit)
        forfeit_credited_to_date = total_forfeited
    else:
        forfeit_credited_to_date = _r2(max(forfeit_credited_to_date, total_forfeited))

    B["cash_aud"] = _r2(B["cash_aud"])
    B["commission_expense_aud"] = _r2(B["commission_expense_aud"])
    B["commission_forfeit_credit_aud"] = _r2(B["commission_forfeit_credit_aud"])
    B["commission_accrued_aud"] = total_accrued
    B["commission_paid_aud"] = _r2(sum(r["paid_aud"] + r["vested_aud"] for r in seat_rows.values()))
    B["commission_deferred_aud"] = _r2(sum(r["deferred_aud"] for r in seat_rows.values()))

    ranked = sorted(   # TFO HT 03:35: head of desk (Marcus Hale) owns no book -> not in the trader pay ranking (still listed/paid under seats)
        [r for r in seat_rows.values() if r["seat"] not in NO_BOOK_SEATS],
        key=lambda r: (-r["ytd_earnings_aud"], r["seat"]),
    )
    ranked_out = [{
        "rank": i + 1,
        "seat": r["seat"],
        "role": r["role"],
        "ytd_earnings_aud": r["ytd_earnings_aud"],
        "accrued_commission_aud": r["accrued_commission_aud"],
        "clawback_deficit_aud": r["clawback_deficit_aud"],
        "cut": r["cut"],
    } for i, r in enumerate(ranked)]

    payload = {
        "schema": "veteran_desk_payouts/1",
        "mode": "PAPER",
        "generated_utc": now_iso,
        "calendar_year": year_key,
        "rules": RULES_TEXT,
        "rates": {
            "proposer_commission": COMMISSION_RATE,
            "veto_commission": VETO_RATE,
            "deferred_fraction": DEFERRED_FRACTION,
            "vest_lag_months": VEST_LAG_MONTHS,
        },
        "seats": seat_rows,
        "ranked": ranked_out,
        "total_expense_aud": total_accrued,
        "total_forfeited_aud": total_forfeited,
        "net_expense_after_forfeit_aud": _r2(total_accrued - total_forfeited),
        "book": {
            "cash_aud": B["cash_aud"],
            "commission_expense_aud": B["commission_expense_aud"],
            "commission_forfeit_credit_aud": B["commission_forfeit_credit_aud"],
        },
        "cut_seats": sorted(cut_seats),
        "inputs": {
            "forward_trades": fwd_path,
            "scorecard": score_path,
            "out": out_path,
        },
        "_state": {
            "processed_trade_ids": sorted(processed_trades),
            "processed_veto_credits": sorted(processed_vetoes),
            "expensed_total_aud": expensed_to_date,
            "forfeit_credited_aud": forfeit_credited_to_date,
            "trade_journal": {k: trade_j[k] for k in sorted(trade_j)},
            "veto_journal": {k: veto_j[k] for k in sorted(veto_j)},
            "cut_first_seen": {k: cut_first_seen[k] for k in sorted(cut_first_seen)},
            "seats": {
                seat: {
                    "cumulative_net_aud": _r2(st["cumulative_net_aud"]),
                    "peak_net_aud": _r2(st["peak_net_aud"]),
                    "months": {m: _r2(v) for m, v in sorted(st["months"].items())},
                    "ytd": {y: _r2(v) for y, v in sorted(st["ytd"].items())},
                    "avoided": {y: _r2(v) for y, v in sorted(st["avoided"].items())},
                    "forfeited_months": sorted(st["forfeited_months"]),
                    "trade_count": int(st["trade_count"]),
                    "veto_count": int(st["veto_count"]),
                }
                for seat, st in sorted(seat_state.items())
            },
        },
    }

    _atomic_write_json(out_path, payload)
    return payload


# ---------------------------------------------------------------------------
# self-test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import shutil

    tmp = tempfile.mkdtemp(prefix="veteran_payouts_selftest_")
    try:
        OUT = os.path.join(tmp, "veteran_desk_payouts.json")
        FWD = os.path.join(tmp, "forward_trades.json")
        SCORE = os.path.join(tmp, "veteran_desk_scorecard.json")
        NOW = "2026-10-28T06:00:00+00:00"

        def fresh_book():
            return {
                "cash_aud": 1_000_000.0,
                "commission_expense_aud": 0.0,
                "commission_forfeit_credit_aud": 0.0,
                "closed": [],
            }

        def clean():
            for p in (OUT, FWD, SCORE):
                if os.path.exists(p):
                    os.unlink(p)

        # --- missing files are tolerated; every seat listed -----------------
        clean()
        B = fresh_book()
        out = accrue(B, now=NOW, fwd_path=FWD, score_path=SCORE, out_path=OUT)
        assert os.path.exists(OUT), "payouts file must be written"
        assert set(SEATS).issubset(set(out["seats"])), "all seats must be listed"
        assert out["total_expense_aud"] == 0.0
        assert B["cash_aud"] == 1_000_000.0
        assert len(out["ranked"]) == len([k for k in out["seats"] if k not in NO_BOOK_SEATS]) and all(r["seat"] not in NO_BOOK_SEATS for r in out["ranked"])
        assert isinstance(out["rules"], str) and out["rules"]

        # --- 1. clawback: loss first, then a win that stays below the peak --
        clean()
        B = fresh_book()
        B["closed"] = [
            {"id": "t1", "seat": "energy", "net_aud": -1000.0, "closed_utc": "2026-05-04T03:00:00Z"},
            {"id": "t2", "seat": "energy", "net_aud": 600.0, "closed_utc": "2026-05-20T03:00:00Z"},
        ]
        out = accrue(B, now=NOW, fwd_path=FWD, score_path=SCORE, out_path=OUT)
        energy = out["seats"]["energy"]
        assert energy["cumulative_net_aud"] == -400.0, energy
        assert energy["peak_net_aud"] == 0.0, energy
        assert energy["accrued_commission_aud"] == 0.0, "no commission below prior peak"
        assert energy["clawback_deficit_aud"] == 400.0, energy
        assert B["cash_aud"] == 1_000_000.0 and B["commission_expense_aud"] == 0.0

        # --- 2. high-water accrual: only the rise above the prior peak ------
        B["closed"].append(
            {"id": "t3", "seat": "energy", "net_aud": 900.0, "closed_utc": "2026-05-28T03:00:00Z"})
        out = accrue(B, now=NOW, fwd_path=FWD, score_path=SCORE, out_path=OUT)
        energy = out["seats"]["energy"]
        assert energy["cumulative_net_aud"] == 500.0, energy
        assert energy["peak_net_aud"] == 500.0, energy
        assert energy["accrued_commission_aud"] == 75.0, energy  # 15% of 500, not of 900
        assert energy["clawback_deficit_aud"] == 0.0, energy
        assert B["commission_expense_aud"] == 75.0 and B["cash_aud"] == 999_925.0

        # May 2026 has ended and vested (vests 2026-09-01): 50/50 paid/vested
        assert energy["paid_aud"] == 37.5 and energy["vested_aud"] == 37.5, energy
        assert energy["deferred_aud"] == 0.0 and energy["pending_payment_aud"] == 0.0, energy
        assert energy["ytd_earnings_aud"] == 500.0, energy

        # a fresh peak only earns on the new increment
        B["closed"].append(
            {"id": "t4", "seat": "energy", "net_aud": 200.0, "closed_utc": "2026-10-02T03:00:00Z"})
        out = accrue(B, now=NOW, fwd_path=FWD, score_path=SCORE, out_path=OUT)
        energy = out["seats"]["energy"]
        assert energy["accrued_commission_aud"] == 105.0, energy  # 75 + 15% of 200
        # October 2026 is the current month: not yet month-end, deferred unvested
        assert energy["pending_payment_aud"] == 15.0, energy
        assert energy["deferred_aud"] == 15.0, energy
        assert _r2(energy["paid_aud"] + energy["pending_payment_aud"] + energy["deferred_aud"]
                   + energy["vested_aud"] + energy["forfeited_aud"]) == energy["accrued_commission_aud"]

        # --- 3. veto credit: 2% of avoided loss, both challengers ----------
        with open(FWD, "w", encoding="utf-8") as fh:
            json.dump({"trades": {
                "f1": {"seat": "banks", "status": "closed", "vetoed": True,
                       "vetoed_by": ["blind_challenger", "devils_advocate"],
                       "pnl_aud": -2000.0, "closed": "2026-05-15"},
                "f2": {"seat": "macro", "status": "closed", "vetoed": True,
                       "vetoed_by": ["blind_challenger"],
                       "pnl_aud": 500.0, "closed": "2026-05-16"},   # bad veto: no credit
                "f3": {"seat": "macro", "status": "open", "vetoed": True,
                       "vetoed_by": ["devils_advocate"],
                       "pnl_aud": -900.0, "closed": None},           # not closed: no credit
            }}, fh)
        out = accrue(B, now=NOW, fwd_path=FWD, score_path=SCORE, out_path=OUT)
        for seat in CHALLENGER_SEATS:
            row = out["seats"][seat]
            assert row["accrued_commission_aud"] == 40.0, (seat, row)  # 2% of 2000
            assert row["correct_vetoes"] == 1, (seat, row)
            assert row["paid_aud"] == 20.0 and row["vested_aud"] == 20.0, (seat, row)
        assert B["commission_expense_aud"] == 185.0, B      # 105 + 40 + 40
        assert B["cash_aud"] == 999_815.0, B

        # --- 4. idempotency: re-running changes nothing --------------------
        cash_before = B["cash_aud"]
        expense_before = B["commission_expense_aud"]
        snapshot = accrue(B, now=NOW, fwd_path=FWD, score_path=SCORE, out_path=OUT)
        again = accrue(B, now=NOW, fwd_path=FWD, score_path=SCORE, out_path=OUT)
        assert B["cash_aud"] == cash_before, B
        assert B["commission_expense_aud"] == expense_before, B
        assert again["total_expense_aud"] == snapshot["total_expense_aud"] == 185.0
        assert again["seats"] == snapshot["seats"], "repeat runs must be identical"

        # --- 5. forfeiture on cut: unvested deferred is clawed back --------
        with open(SCORE, "w", encoding="utf-8") as fh:
            json.dump({"seats": {
                "energy": {"verdict": "CUT - three straight losing months"},
                "banks": {"verdict": "KEEP"},
            }}, fh)
        cash_before = B["cash_aud"]
        out = accrue(B, now=NOW, fwd_path=FWD, score_path=SCORE, out_path=OUT)
        energy = out["seats"]["energy"]
        assert energy["cut"] is True, energy
        assert energy["forfeited_aud"] == 15.0, energy     # the unvested Oct half
        assert energy["deferred_aud"] == 0.0, energy
        assert energy["vested_aud"] == 37.5, energy        # already vested, safe
        assert B["commission_forfeit_credit_aud"] == 15.0, B
        assert B["cash_aud"] == _r2(cash_before + 15.0), B
        assert out["total_forfeited_aud"] == 15.0, out["total_forfeited_aud"]

        # forfeiture is itself idempotent
        cash_after = B["cash_aud"]
        accrue(B, now=NOW, fwd_path=FWD, score_path=SCORE, out_path=OUT)
        assert B["cash_aud"] == cash_after and B["commission_forfeit_credit_aud"] == 15.0, B

        # --- 6. risk_gate: salaried, always zero ---------------------------
        B["closed"].append(
            {"id": "t5", "seat": "risk_gate", "net_aud": 5000.0, "closed_utc": "2026-05-30T03:00:00Z"})
        with open(FWD, "r", encoding="utf-8") as fh:
            fwd = json.load(fh)
        fwd["trades"]["f4"] = {"seat": "banks", "status": "closed", "vetoed": True,
                               "vetoed_by": ["risk_gate"], "pnl_aud": -4000.0,
                               "closed": "2026-05-18"}
        with open(FWD, "w", encoding="utf-8") as fh:
            json.dump(fwd, fh)
        out = accrue(B, now=NOW, fwd_path=FWD, score_path=SCORE, out_path=OUT)
        gate = out["seats"]["risk_gate"]
        assert gate["role"] == "salaried_risk_gate", gate
        assert gate["accrued_commission_aud"] == 0.0, gate
        assert gate["paid_aud"] == gate["deferred_aud"] == gate["vested_aud"] == 0.0, gate
        assert gate["commission_rate"] == 0.0, gate
        assert out["total_expense_aud"] == 185.0, out["total_expense_aud"]

        # --- ranking + persisted file is valid JSON ------------------------
        ytds = [r["ytd_earnings_aud"] for r in out["ranked"]]
        assert ytds == sorted(ytds, reverse=True), ytds
        assert out["ranked"][0]["seat"] == "risk_gate", out["ranked"][0]  # 5000 YTD net
        reread = _load_json(OUT)
        assert reread["total_expense_aud"] == out["total_expense_aud"]

        # --- state survives a cold start from file only --------------------
        B2 = fresh_book()
        B2["closed"] = list(B["closed"])
        out2 = accrue(B2, now=NOW, fwd_path=FWD, score_path=SCORE, out_path=OUT)
        assert B2["cash_aud"] == 1_000_000.0, "nothing new to book on a cold re-run"
        assert out2["seats"]["energy"]["accrued_commission_aud"] == 105.0

        print("veteran_desk/payouts.py self-test OK  (expense A$%.2f, forfeit A$%.2f)"
              % (out["total_expense_aud"], out["total_forfeited_aud"]))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
