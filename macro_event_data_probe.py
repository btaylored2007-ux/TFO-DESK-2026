#!/usr/bin/env python3
"""macro_event_data_probe.py — DATA PROBE for the scheduled-macro-event family.

Answers ONE question before any lab is built: is there a free, honest, verifiable
feed of SCHEDULED macro-event dates deep enough to test on?

Two candidate event families:
  A. FOMC scheduled announcement dates (the seasonality lab of 2026-08-10 declared
     pre-FOMC drift as variant #1 of its N and left it UNTESTED because "the exact
     scheduled-announcement dates are not in this machine's local data lake and
     hand-entering ~200 dates I cannot verify would corrupt the very 24-hour window
     being measured"). This probe tests whether that blocker still holds.
  B. Treasury auction dates (Lou-Yan-Zhang 2013 anticipated-supply effect).

Writes reports/macro_event_data_probe.json. Fetches only public pages/APIs. No
capital, no orders, no secrets. Read-only w.r.t. the desk.
"""
import json
import os
import re
import sys
import urllib.request
from datetime import date, datetime

CACHE = ("/private/tmp/claude-501/-Users-test/"
         "c41d712f-8d5f-4972-8418-fe39fd1414ef/scratchpad/macro_cache")
os.makedirs(CACHE, exist_ok=True)
OUT = "reports/macro_event_data_probe.json"
UA = {"User-Agent": "TFO-paper-research/1.0 (family office paper desk; research only)"}

MONTHS = {m: i + 1 for i, m in enumerate(
    ["January", "February", "March", "April", "May", "June", "July",
     "August", "September", "October", "November", "December"])}


def get(url, tag):
    path = os.path.join(CACHE, tag)
    if os.path.exists(path):
        with open(path, "rb") as f:
            return f.read().decode("utf-8", "replace")
    req = urllib.request.Request(url, headers=UA)
    b = urllib.request.urlopen(req, timeout=45).read()
    with open(path, "wb") as f:
        f.write(b)
    return b.decode("utf-8", "replace")


# ---------------------------------------------------------------- FOMC
def parse_historical(year):
    """<h5>January 27-28 Meeting - 2015</h5> panels, plus any statement links.

    Returns list of dicts: label, scheduled(bool), last_day(date), stmt_dates(list).
    """
    h = get("https://www.federalreserve.gov/monetarypolicy/fomchistorical%d.htm" % year,
            "fomchist%d.htm" % year)
    out = []
    # split on the h5 panel headings so statement links attach to their own meeting
    parts = re.split(r"<h5[^>]*>(.*?)</h5>", h, flags=re.S)
    for i in range(1, len(parts), 2):
        label = " ".join(re.sub(r"<[^>]+>", " ", parts[i]).split())
        body = parts[i + 1] if i + 1 < len(parts) else ""
        stmts = sorted(set(re.findall(r"monetary(\d{8})a?\d*\.htm", body)) |
                       set(re.findall(r"/boarddocs/press/[a-z]+/\d{4}/(\d{8})", body)))
        rec = {"label": label, "year": year, "stmt_dates": stmts}
        rec.update(classify_and_date(label, year))
        out.append(rec)
    return out


def classify_and_date(label, year):
    """A meeting is SCHEDULED iff the Fed's own label says 'Meeting' with no
    disqualifier. Conference calls / notation votes / unscheduled / cancelled are
    excluded -- the pre-FOMC drift is defined on the pre-announced calendar."""
    low = label.lower()
    bad = ("unscheduled" in low or "cancelled" in low or "canceled" in low
           or "notation" in low or "conference call" in low)
    scheduled = ("meeting" in low) and not bad
    # "January 27-28", "March 22", "April/May 30-1", "October 4"
    m = re.match(r"([A-Z][a-z]+)(?:/([A-Z][a-z]+))?\s+(\d{1,2})(?:-(\d{1,2}))?", label)
    last_day = None
    if m:
        m1, m2, d1, d2 = m.group(1), m.group(2), int(m.group(3)), m.group(4)
        try:
            if d2 is None:
                last_day = date(year, MONTHS[m1], d1)
            elif m2:                      # cross-month, e.g. April/May 30-1
                last_day = date(year, MONTHS[m2], int(d2))
            else:
                last_day = date(year, MONTHS[m1], int(d2))
        except (KeyError, ValueError):
            last_day = None
    return {"scheduled": scheduled,
            "last_day": last_day.isoformat() if last_day else None}


def parse_calendar():
    """Current calendar page (recent + future years): statement links carry the
    exact announcement date, e.g. /pressreleases/monetary20260128a.htm."""
    h = get("https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm",
            "fomccalendars.htm")
    rows = []
    for blk in re.split(r'<div class="[^"]*fomc-meeting[^"]*row', h)[1:]:
        mon = re.search(r'fomc-meeting__month[^>]*>\s*<strong>(.*?)</strong>', blk, re.S)
        dat = re.search(r'fomc-meeting__date[^>]*>(.*?)</div>', blk, re.S)
        if not (mon and dat):
            continue
        month = " ".join(re.sub(r"<[^>]+>", " ", mon.group(1)).split())
        dtxt = " ".join(re.sub(r"<[^>]+>", " ", dat.group(1)).split())
        stmts = sorted(set(re.findall(r"monetary(\d{8})a?\d*\.htm", blk)))
        rows.append({"month": month, "date_text": dtxt, "stmt_dates": stmts,
                     "unscheduled": False})  # '*' on this page = press conference, not an unscheduled meeting
    return rows


def fomc_probe():
    hist_years = list(range(1994, 2021))
    hist, fetch_errors = [], []
    for y in hist_years:
        try:
            hist.extend(parse_historical(y))
        except Exception as e:
            fetch_errors.append("%d: %s" % (y, str(e)[:60]))
    sched = [r for r in hist if r["scheduled"]]
    unsched = [r for r in hist if not r["scheduled"]]

    # VERIFICATION: where the Fed links a statement, does it fall on the last day
    # of the meeting we inferred? This is the check that makes the inference honest.
    checked = agree = 0
    disagreements = []
    for r in sched:
        if r["stmt_dates"] and r["last_day"]:
            checked += 1
            iso = [datetime.strptime(s, "%Y%m%d").date().isoformat()
                   for s in r["stmt_dates"]]
            if r["last_day"] in iso:
                agree += 1
            else:
                disagreements.append({"label": r["label"], "inferred": r["last_day"],
                                      "statements": iso})
    per_year = {}
    for r in sched:
        per_year[r["year"]] = per_year.get(r["year"], 0) + 1

    cal = parse_calendar()
    cal_dates = sorted({datetime.strptime(s, "%Y%m%d").date().isoformat()
                        for r in cal for s in r["stmt_dates"]})
    cal_no_stmt = [r for r in cal if not r["stmt_dates"]]

    return {
        "source_historical": "federalreserve.gov/monetarypolicy/fomchistorical<YEAR>.htm",
        "source_calendar": "federalreserve.gov/monetarypolicy/fomccalendars.htm",
        "years_historical": [hist_years[0], hist_years[-1]],
        "fetch_errors": fetch_errors,
        "panels_total": len(hist),
        "scheduled_meetings": len(sched),
        "excluded_nonscheduled": len(unsched),
        "excluded_examples": [r["label"] for r in unsched[:8]],
        "scheduled_per_year": per_year,
        "years_not_eight": {k: v for k, v in per_year.items() if v != 8},
        "date_parse_failures": [r["label"] for r in sched if not r["last_day"]],
        "verification_statement_link_vs_inferred_last_day": {
            "checked": checked, "agree": agree,
            "agreement_pct": round(100.0 * agree / checked, 2) if checked else None,
            "disagreements": disagreements[:10]},
        "calendar_rows": len(cal),
        "calendar_statement_dates": len(cal_dates),
        "calendar_first_last": [cal_dates[0], cal_dates[-1]] if cal_dates else None,
        "calendar_rows_without_statement_future": len(cal_no_stmt),
    }


# ---------------------------------------------------------------- Treasury
def treasury_probe():
    url = ("https://www.treasurydirect.gov/TA_WS/securities/auctioned"
           "?format=json&pagesize=20000")
    raw = get(url, "treasury_auctioned.json")
    data = json.loads(raw)
    by_type, years = {}, {}
    fields_present = set()
    for row in data[:50]:
        fields_present |= set(row.keys())
    parsed = 0
    for row in data:
        st = row.get("securityType")
        ad = row.get("auctionDate")
        if not ad:
            continue
        y = ad[:4]
        by_type[st] = by_type.get(st, 0) + 1
        years[y] = years.get(y, 0) + 1
        parsed += 1
    ys = sorted(years)
    return {
        "source": "treasurydirect.gov/TA_WS/securities/auctioned (public, free)",
        "rows": len(data),
        "rows_with_auction_date": parsed,
        "by_security_type": by_type,
        "year_range": [ys[0], ys[-1]] if ys else None,
        "auctions_per_year_sample": {y: years[y] for y in ys[-8:]},
        "has_announcement_date": "announcementDate" in fields_present,
        "has_issue_date": "issueDate" in fields_present,
        "has_security_term": "securityTerm" in fields_present,
    }


# ---------------------------------------------------------------- prices
def price_probe():
    import yfinance as yf
    out = {}
    for t in ["SPY", "QQQ", "IWM", "EFA", "TLT", "IEF", "SHY", "GLD"]:
        try:
            h = yf.Ticker(t).history(period="max", auto_adjust=True)
            out[t] = {"rows": int(len(h)),
                      "first": str(h.index[0].date()) if len(h) else None,
                      "last": str(h.index[-1].date()) if len(h) else None}
        except Exception as e:
            out[t] = {"error": str(e)[:80]}
    # intraday depth: the 24h Lucca-Moench window needs intraday bars
    try:
        h1 = yf.Ticker("SPY").history(period="max", interval="1h")
        out["SPY_1h"] = {"rows": int(len(h1)),
                         "first": str(h1.index[0].date()) if len(h1) else None}
    except Exception as e:
        out["SPY_1h"] = {"error": str(e)[:80]}
    return out


def main():
    probe = {"generated": datetime.utcnow().isoformat() + "Z",
             "question": "Is there a free, verifiable feed of scheduled macro-event "
                         "dates deep enough to test pre-FOMC drift and the Treasury "
                         "auction cycle COLD?",
             "fomc": fomc_probe(),
             "treasury": treasury_probe(),
             "prices": price_probe()}
    f = probe["fomc"]
    ok = (f["scheduled_meetings"] > 200
          and (f["verification_statement_link_vs_inferred_last_day"]["agreement_pct"] or 0) >= 99.0
          and not f["date_parse_failures"])
    probe["verdict_fomc"] = ("VIABLE" if ok else "NOT VIABLE")
    probe["verdict_treasury"] = ("VIABLE" if probe["treasury"]["rows_with_auction_date"] > 1000
                                 else "NOT VIABLE")
    probe["intraday_note"] = (
        "Yahoo 1h history is short-window only, so the literal Lucca-Moench 24h "
        "(2pm T-1 -> 2pm T) window is NOT reconstructable over the full sample. The "
        "lab must test the DAILY-CLOSE proxy and say so; it must not claim the 24h "
        "number.")
    os.makedirs("reports", exist_ok=True)
    with open(OUT, "w") as fh:
        json.dump(probe, fh, indent=2, default=str)
    print(json.dumps({k: v for k, v in probe.items() if k != "prices"},
                     indent=2, default=str)[:4000])
    print("\nPRICES:", json.dumps(probe["prices"], indent=2))
    print("\nwrote", OUT)


if __name__ == "__main__":
    sys.exit(main())
