"""seatexec_20261103 (D23, helper fixer, PAPER, READ-ONLY): the MY POSITIONS block for every seat prompt (trading rounds in
desk.prop_prompt, chat in thf_msgdesk.prompt). Roll-call 3 Nov: healthcare "no CVS quote in this DATA, so I cannot mark it",
essentials "BGA.AX/KO marks not in DATA", HT/CFO "VEON missing from my positions list" - although state/thf/thf_book.json carries a
mark + mark_utc for every open position (2-min THF tick).
One line per open/armed position: ticker, side, status, grade, fill/trigger, stop, target, the book's tick mark + its age (tick age +
the quote's own age px_age_min), unrealised A$ (mark vs fill notional, same as the money block) and R (price move / initial stop
distance), plus a paid quote that costs NO API call: the EODHD Cboe Europe websocket file for EU names, else the paid intraday bar
store (data/intraday/bars.sqlite: Massive US / EODHD), or the round pack's own live_quotes when the wake already fetched one.
Trader seats see their own book (ids included, for chat "actions"); Marcus/HT/CFO/admins see the whole desk. Rows are never cut
silently: every open/armed row is listed (lines shorten under the cap, never disappear). Never writes, never raises."""
import datetime as dt, json, os, re, time
BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BOOK = os.path.join(BASE, "state", "thf", "thf_book.json")
UTC = dt.timezone.utc; WITA = dt.timezone(dt.timedelta(hours=8))
CAP_OWN = 1800; CAP_DESK = 3200
ROUND_HINT = ("(A round only proposes. To withdraw/close/half-bank/tighten one of these now, tell Marcus (to_seats marcus) - he executes via seat_cli; "
              "in chat you act on your own names directly with \"actions\".)")


def _f(x):
    try: return float(x)
    except Exception: return None


def _n(x):
    v = _f(x)
    return "?" if v is None else "%.5g" % v


def _k(x):
    v = _f(x)
    if v is None: return "?"
    return ("-" if v < 0 else "+") + ("A$%.1fk" % (abs(v) / 1e3) if abs(v) >= 1000 else "A$%.0f" % abs(v))


def _ts(u):
    try:
        t = dt.datetime.fromisoformat(str(u).replace("Z", "+00:00"))
        return t if t.tzinfo else t.replace(tzinfo=UTC)
    except Exception: return None


def _age(minutes):
    if minutes is None: return "?"
    m = max(0.0, float(minutes))
    return "%dm" % round(m) if m < 90 else ("%.1fh" % (m / 60) if m < 48 * 60 else "%dd" % round(m / 1440))


def _msrc(src):
    """fxmark_20261103 (F18): short label of the feed behind a book mark (book row mark_src)."""
    s = str(src or "")
    if s.startswith("oanda_practice_mid"): return "oanda mid" + (" STALE" if "stale" in s else "")
    if "OANDA fallback" in s: return "yahoo 5m, OANDA down"
    for a, b in (("official_close", "official close"), ("massive", "massive 5m"), ("eodhd_eu_ws", "eodhd live"), ("kraken", "kraken"), ("yahoo", "yahoo")):
        if s.startswith(a): return b
    return s[:18]


def fx_line(book, now=None):
    """fxmark_20261103 (F19): one short line - the AUDUSD (and each other A$ cross) this book tick used for its A$ numbers, with source + age:
    'A$ rates (book tick): AUDUSD 0.69837 oanda mid 0m | GBP/A$ 0.52019 1/GBPAUD oanda mid'. '' when the book has no rate."""
    try:
        au = _f((book or {}).get("audusd"))
        if not au: return ""
        now = now or dt.datetime.now(UTC); a = _ts(book.get("audusd_asof"))
        def _s(src):
            s = str(src or "?")
            return s.replace("oanda_practice_mid", "oanda mid").replace("yahoo_yf_5m", "yahoo 5m").replace("[", "").replace("]", "")[:48]
        out = ["AUDUSD %.5g %s%s" % (au, _s(book.get("audusd_src") or "source not recorded"), (" %s old" % _age((now - a).total_seconds() / 60.0)) if a else "")]
        for c, r in sorted(((book or {}).get("fx_rates") or {}).items())[:6]:
            if isinstance(r, dict) and _f(r.get("v")): out.append("%s/A$ %.5g %s%s" % (c, float(r["v"]), _s(r.get("src")), (" %s old" % _age(r["age_min"])) if r.get("age_min") is not None else ""))
        return "A$ rates (book tick): " + " | ".join(out)
    except Exception: return ""


def load_book(path=None):
    try: return json.load(open(path or BOOK))
    except Exception: return {}


def rows(book, seat=None):
    """Open/armed positions (one seat, or the whole desk when seat is None): seat, open before armed, ticker."""
    out = []
    for s, B in ((book or {}).get("seats") or {}).items():
        if (seat and s != seat) or not isinstance(B, dict): continue
        for pid, p in (B.get("positions") or {}).items():
            if isinstance(p, dict) and str(p.get("status") or "").startswith(("open", "armed")):
                out.append(dict(p, id=p.get("id") or pid, seat=p.get("seat") or s))
    return sorted(out, key=lambda p: (str(p["seat"]), 0 if str(p.get("status")).startswith("open") else 1, str(p.get("ticker"))))


def paid_quote(sym):
    """(px, src, utc_iso) from paid sources that cost no API call, else None. EU names: eodhd_eu_ws.live_px (its state file, only when
    healthy/fresh). Others: newest 1m/5m bar in the paid intraday store (Massive US, EODHD). Crypto/FX: None (the book mark is live)."""
    s = str(sym or "")
    if not s or s.upper().startswith("KRAKEN:") or "=" in s: return None
    try:
        from veteran_desk import eodhd_eu_ws as EU
        q = EU.live_px(s)
        if q and q.get("last"): return float(q["last"]), "eodhd_eu_ws", q.get("asof")
    except Exception: pass
    try:
        from veteran_desk import intraday_store as IS
        best = None
        for v in dict.fromkeys((s, s.replace("-", "."))):   # BRK-B is BRK.B on Massive
            for tf in ("1m", "5m"):
                b = IS.last_bar(v, tf)
                if b and b.get("c") is not None and (best is None or (b.get("ts") or 0) > (best.get("ts") or 0)): best = b
        if best:
            end = _ts(best.get("utc")); end = (end + dt.timedelta(minutes=1 if best.get("tf") == "1m" else 5)) if end else None   # bar close time
            return float(best["c"]), "%s %s" % (best.get("src") or "intraday", best.get("tf")), end.isoformat() if end else best.get("utc")
    except Exception: pass
    return None


FXQ_TTL_S = 60; FXQ_TIMEOUT_S = 4; _FXQ = {}


def _yahoo_5m(s, fetch=None):
    """armfix_20261103: newest Yahoo chart 5m close (px, label, bar_start_utc_iso) or None (fetch = test hook, else one 4 s GET)."""
    try:
        if fetch: J = fetch(s)
        else:
            from veteran_desk.data_layer import yahoo_chart_response
            J = yahoo_chart_response(s, {"range": "1d", "interval": "5m", "includePrePost": "false"}, timeout=FXQ_TIMEOUT_S).json()
        R = ((J.get("chart") or {}).get("result") or [None])[0] or {}
        ts = R.get("timestamp") or []; cl = ((((R.get("indicators") or {}).get("quote") or [{}])[0]) or {}).get("close") or []
        k = [i for i in range(min(len(ts), len(cl))) if cl[i] is not None]
        if k: return (float(cl[k[-1]]), "yahoo 5m = the book's trigger feed", dt.datetime.fromtimestamp(ts[k[-1]], UTC).isoformat())
    except Exception: pass
    return None


def _pf():
    import sys as _sy
    if BASE not in _sy.path: _sy.path.insert(0, BASE)
    import paid_feeds as _PF
    return _PF


def fx_quote(sym, fetch=None, oanda=None, cfd=None):
    """armfix_20261103 (P0 PART 5, Marcus 21:54: armed EURNOK/EURCAD rows showed only the 11-02 pit close): an ARMED FX (=X) / futures (=F)
    row has no book mark (the tick marks open rows only) and no paid intraday bar, so it fell to the pit daily close. It now gets the SAME
    Yahoo chart 5m quote the book tick arms/fills/stops these symbols on (book.live_px -> yfinance / Yahoo v8 for '=' symbols): free feed,
    one call per symbol per 60s (in-process cache), 4s timeout. (px, src, bar_start_utc_iso) or None; never under tests unless fetch given.
    fxmark_20261103 (F18): the book now marks/triggers/fills OANDA-book FX pairs on the OANDA practice MID, so such a row shows THAT price
    first ('oanda practice mid = the book's mark/trigger feed'); the Yahoo 5m quote is what the book falls back to when OANDA is down.
    Futures keep the Yahoo future (the book's feed) + the OANDA CFD as a 4th item beside it (never the mark). oanda/cfd = test hooks."""
    s = str(sym or "")
    hooks = fetch is not None or oanda is not None or cfd is not None
    if "=" not in s or (not hooks and os.environ.get("PYTEST_CURRENT_TEST")): return None
    c = _FXQ.get(s); t0 = time.time()
    if not hooks and c and t0 - c[0] < FXQ_TTL_S: return c[1]
    q = None
    if s.upper().endswith("=X") and (oanda is not None or fetch is None):   # fxmark_20261103: the book's FX feed (OANDA mid) first
        try:
            if oanda is not None or _pf()._oanda_ins(s):
                o = (oanda or _pf().oanda_book_quote)(s)
                if o and o.get("last"): q = (float(o["last"]), "oanda practice mid = the book's mark/trigger feed", o.get("asof"))
        except Exception: q = None
    if q is None and (fetch is not None or oanda is None): q = _yahoo_5m(s, fetch)
    if q and s.upper().endswith("=F") and (cfd is not None or fetch is None):   # fxmark_20261103: OANDA CFD beside the future (master 22:5x)
        try:
            n = (cfd or _pf().cfd_note)(s, q[0])
            if n: q = q + (n,)
        except Exception: pass
    if not hooks: _FXQ[s] = (t0, q)
    return q


def last_close(sym):
    """Local fallback when no paid intraday quote exists (e.g. BRK-B): newest daily close in data/pit/pit.sqlite, labelled as not live."""
    s = str(sym or "")
    if not s or s.upper().startswith("KRAKEN:"): return None
    try:
        import sqlite3
        c = sqlite3.connect("file:%s?mode=ro" % os.path.join(BASE, "data", "pit", "pit.sqlite"), uri=True, timeout=5)
        try: r = c.execute("select date, c from bars where sym=? order by date desc limit 1", (s,)).fetchone()
        finally: c.close()
        if r and r[1]: return float(r[1]), "daily close %s (pit, not live)" % str(r[0])[5:10], None
    except Exception: pass
    return None


def _pack_quote(quotes, sym):
    q = (quotes or {}).get(sym) if isinstance(quotes, dict) else None
    if isinstance(q, dict) and _f(q.get("px")): return float(q["px"]), "pack " + str(q.get("src") or "paid"), q.get("quote_utc")
    return None


def line(p, now=None, quote=None, show_seat=False, show_id=True, slim=False):
    """One compact line for one open/armed position."""
    now = now or dt.datetime.now(UTC)
    st = str(p.get("status") or "?"); is_open = st.startswith("open"); side = str(p.get("side") or "?").upper(); sg = -1.0 if side == "SHORT" else 1.0
    fill = _f(p.get("fill")) if is_open else None; e = fill or _f(p.get("entry")); stop = _f(p.get("stop")); mk = _f(p.get("mark"))
    head = "%s%s %s %s %s: %s %s sl%s%s" % ((str(p.get("seat")) + " ") if show_seat else "", p.get("ticker"), side, st, p.get("grade") or "?",
                                         "fill" if is_open else ("trigger(%s)" % (p.get("order_type") or p.get("order") or "?")), _n(e), _n(stop), "" if slim else " tp" + _n(p.get("target")))
    parts = [head]
    if is_open:
        if mk is not None:
            mu = _ts(p.get("mark_utc")); age = ((now - mu).total_seconds() / 60.0 + (_f(p.get("px_age_min")) or 0.0)) if mu else None
            un = None
            try:
                if p.get("mark_notional_aud") is not None and p.get("fill_notional_aud") is not None: un = (float(p["mark_notional_aud"]) - float(p["fill_notional_aud"])) * sg
            except Exception: un = None
            is_fx=str(p.get("venue") or "")=="FX" or str(p.get("ticker") or "").endswith("=X")
            if is_fx:
                # The FX book settles only price P/L. Full-notional currency translation is not owned cash.
                try:
                    conversion=float(p["mark_notional_aud"])/(mk*float(p["shares"]))
                    un=(mk-e)*float(p["shares"])*sg*conversion
                except (TypeError,ValueError,KeyError,ZeroDivisionError):un=None
            r0 = _f(p.get("r0")) or (abs(e - (_f(p.get("initial_stop")) or stop)) if (e and (_f(p.get("initial_stop")) or stop)) else None)
            R = ((mk - e) * sg / r0) if (r0 and e) else None
            parts.append("mark %s (%s old%s)" % (_n(mk), _age(age), (", " + _msrc(p.get("mark_src"))) if p.get("mark_src") else ""))   # fxmark_20261103 (F18): the mark says its feed
            parts.append("unreal %s %sR" % (_k(un), ("%+.2f" % R) if R is not None else "?"))
            if not slim and not is_fx and un is not None and e and mk and _f(p.get("shares")) and p.get("mark_notional_aud") is not None:
                conversion=float(p["mark_notional_aud"])/(mk*float(p["shares"]))
                price=(mk-e)*float(p["shares"])*sg*conversion;translation=un-price
                if abs(translation)>=1:parts.append("price %s; FX translation %s (book valuation)"%(_k(price),_k(translation)))
        else: parts.append("UNMARKED in book")
    else: parts.append("risk %s" % _k(p.get("risk_aud")).lstrip("+"))
    if quote and not slim:
        px, src, u = quote[:3]; qt = _ts(u); qa = ((now - qt).total_seconds() / 60.0) if qt else None   # fxmark_20261103: optional 4th item = a side note (OANDA CFD beside a future)
        q = ("%s %s" % (src, _n(px))) if str(src).startswith("daily close") else ("%s %s %s (%s old)" % ("live" if str(src).startswith("yahoo") else "paid", src, _n(px), _age(qa)))   # armfix_20261103: Yahoo is live, not paid
        if not is_open and e:
            q += " = %+.2f%% vs trigger" % ((px / e - 1.0) * 100.0)
            if stop and ((side == "LONG" and px <= stop) or (side == "SHORT" and px >= stop)):   # armfix_20261103: SHELL.AS 46.625 < sl 46.85, EURNOK 10.38 > sl 10.345 before the trigger
                q += " | PRICE IS THROUGH sl %s BEFORE THE TRIGGER (stop printed first: withdraw or re-cut)" % _n(stop)
        if len(quote) > 3 and quote[3]: q += " | " + str(quote[3])   # fxmark_20261103: e.g. the OANDA CFD next to a Yahoo future (never the book's mark)
        parts.append(q)
    elif not is_open and not slim: parts.append("no paid quote")
    if not is_open and not slim:   # armfix_20261103 (P0 PART 3): the B / confirm_rvol volume rule is CODE at fill time (b_tight.fill_confirm) - show it + the book's hold note
        req = _f(p.get("confirm_rvol"))
        if req or str(p.get("grade")) == "B": parts.append("fills only on intraday rvol >= %.1fx%s (code)" % (req or 1.5, "" if req else " or a dated catalyst"))
        nt = re.sub(r"^[^:]{1,40}: ", "", str(p.get("exec_note") or "")).strip()
        if nt and any(w in nt for w in ("held", "wait", "not chasing", "session closed", "no fill")): parts.append("book: " + nt[:110])
    if show_id: parts.append("id " + str(p.get("id")))
    return "- " + " | ".join(parts)


def _arm_tail(seat, desk, now=None):
    """armfix_20261103 (P0 ch-1793714505-3f49): the seat's LAST ARM RESULTS (trader: own, newest 3 in 24h) / RECENT ARM RESULTS (desk view:
    newest per seat, 6h) - the VET id the book holds, or the reason it did not arm (gate, book-tick rail, no confirm, withdrawn). '' when none."""
    try:
        from veteran_desk import arm_last as AL
        t = AL.desk_line(now=now) if desk else AL.line(seat, now=now)
        return ("\n" + t[:900 if desk else 600]) if t else ""
    except Exception: return ""


def block(seat, desk=None, book=None, quotes=None, cap=None, now=None, hint="", paid=None, show_id=True, fxq=None):
    """MY POSITIONS (trader seat: own book) / DESK POSITIONS (desk=True or a non-trader seat: every seat). '' only on an error."""
    try:
        book = book if book is not None else load_book()
        now = now or dt.datetime.now(UTC)
        if desk is None: desk = seat not in ((book.get("seats") or {}) if isinstance(book, dict) else {})
        R = rows(book, None if desk else seat)
        tick = _ts(book.get("updated")); tk = tick.astimezone(WITA).strftime("%H:%M:%S WITA") if tick else "?"
        no = sum(1 for p in R if str(p.get("status")).startswith("open")); na = len(R) - no
        name = "DESK POSITIONS (all seats)" if desk else "MY POSITIONS"
        hdr = ("%s (%d open, %d armed; book tick %s; mark = book 2-min tick price, age incl. the quote's own age; unreal = A$ vs fill; "
               "R = move / initial stop distance; paid = no-API paid quote):" % (name, no, na, tk))
        if not R: return "%s: none open or armed (book tick %s)." % (name, tk) + _arm_tail(seat, desk, now)   # armfix_20261103
        _fl = fx_line(book, now)   # fxmark_20261103 (F19): which FX rate turned each A$ number into A$
        if _fl: hdr = hdr + "\n" + _fl
        pq = paid or paid_quote
        Q = {}; afx = {p.get("ticker") for p in R if "=" in str(p.get("ticker")) and not str(p.get("status")).startswith("open")}   # armfix_20261103: armed FX/futures rows
        for p in R:
            t = p.get("ticker")
            if t not in Q: Q[t] = _pack_quote(quotes, t) or pq(t) or ((fxq or fx_quote)(t) if t in afx and (paid is None or fxq) else None) or (last_close(t) if paid is None else None)
        cap = cap or (CAP_DESK if desk else CAP_OWN)
        for slim in (False, True):
            L = [line(p, now, Q.get(p.get("ticker")), show_seat=desk, show_id=show_id, slim=slim) for p in R]
            txt = hdr + "\n" + "\n".join(L) + (("\n" + hint) if hint else "")
            if len(txt) <= cap: return txt + _arm_tail(seat, desk, now)   # armfix_20261103
        L = [line(p, now, None, show_seat=desk, show_id=show_id, slim=True) for p in R]   # never drop a row: last resort = slim lines past the cap
        return hdr + "\n" + "\n".join(L) + _arm_tail(seat, desk, now)   # armfix_20261103
    except Exception: return ""


_OA = re.compile(r"^OPEN/ARMED: .*$", re.M)


def compact_dash(text, seat=None, desk=False, book=None, drop=()):
    """The dash snapshot (dash_context.block) repeats every open/armed row with 8-digit entry/stop and no marks. MY/DESK POSITIONS now
    carry them, so the snapshot line shrinks: desk view -> a pointer; trader view -> the other seats' names only (one open/armed order
    per symbol+side desk-wide). Frees ~0.7KB per prompt for the new block. Returns the text unchanged on any problem."""
    if not text or "OPEN/ARMED: " not in text: return text
    try:
        if desk: rep = "OPEN/ARMED: every row is in DESK POSITIONS (marks, age, unreal, R, ids)."
        else:
            by = {}
            for p in rows(book if book is not None else load_book()):
                if p.get("seat") == seat: continue
                by.setdefault(p["seat"], []).append("%s %s%s" % (p.get("ticker"), str(p.get("side") or "?")[:1], "" if str(p.get("status")).startswith("open") else " armed"))
            rep = "OTHER SEATS OPEN/ARMED (yours: MY POSITIONS; one order per symbol+side desk-wide): " + ("; ".join("%s %s" % (s, ", ".join(v)) for s, v in by.items()) or "none")
        out = _OA.sub(lambda m: rep, text, count=1)
        if drop: out = "\n".join(l for l in out.split("\n") if not l.startswith(tuple(drop)))   # rounds: AI plumbing (BRAINS) is not trading data
        return out
    except Exception: return text
