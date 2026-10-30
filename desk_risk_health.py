"""Read-only health checks for the hourly firm's two risk reports.

Fresh processes/collectors do not prove risk reports succeeded. This module
reports missing, stale or incomplete risk measurement; it changes no risk gate.
"""
import datetime as dt
import json
import os
import time


def check_risk_reports(report_dir, now=None, max_age_sec=2 * 3600):
    now = time.time() if now is None else now
    reports, problems = {}, []
    for filename, stamp_key in (("firm_risk.json", "utc"), ("risk_report.json", "generated")):
        info = {"file": filename, "max_age_sec": max_age_sec, "age_sec": None}
        reports[filename] = info
        try:
            with open(os.path.join(report_dir, filename)) as f:
                data = json.load(f)
            # risk_engine's legacy naive `generated` uses host local time;
            # firm_risk's UTC timestamp is explicit. Never use file mtime:
            # copying/touching an old report must not make it fresh.
            stamp = dt.datetime.fromisoformat(data[stamp_key].replace("Z", "+00:00"))
            age = now - stamp.timestamp()
            info.update(asof=data[stamp_key], age_sec=round(age),
                        stale=age > max_age_sec or age < -300)
            if info["stale"]:
                problems.append("%s is stale or future-dated (%ss old); risk is NOT current" % (filename, round(age)))
            cov = data if filename == "firm_risk.json" else data.get("coverage", {})
            readable = cov.get("books_readable" if filename == "firm_risk.json" else "n_books_readable")
            total = cov.get("books_total" if filename == "firm_risk.json" else "n_books")
            info.update(books_readable=readable, books_total=total,
                        unreadable_books=cov.get("unreadable_books") or [])
            if (not isinstance(readable, int) or not isinstance(total, int)
                    or total <= 0 or readable != total or info["unreadable_books"]
                    or data.get("level") == "UNMEASURED"):
                problems.append("%s has incomplete risk coverage (%s/%s books); NOT an all-clear" % (filename, readable, total))
        except (OSError, ValueError, TypeError, KeyError, AttributeError) as exc:
            info.update(stale=True, error="%s: %s" % (type(exc).__name__, exc))
            problems.append("%s cannot be verified: %s" % (filename, info["error"]))
    return reports, problems
