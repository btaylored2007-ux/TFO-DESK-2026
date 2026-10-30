"""tforul 2026-10-30 (TFO rulings items 1+6, PAPER). Logged ledger update via the hunt48 ledger API (H.atomic), never hand-edited:
 - URA t29: record Grok second-look verdict as ADVISORY only (no status change; desk PASS stands).
 - MORN t27 / VDE t24: stamp EODHD next_earnings (calendar/earnings via eodhd_earnings.fetch_rows). No EODHD row -> left UNKNOWN.
Backs up the ledger as ledger.json.bak-20261030-HHMM-tforul first. --dry-run prints only. Py3.9-safe."""
import datetime as dt, json, os, shutil, sys
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__))); sys.path.insert(0, ROOT)
import hunt48_short_paper as H, eodhd_earnings as EE
QID = "20261030T150207_7261_3b507c09970f"
def us_dates(today):
    out = {}
    for x in EE.fetch_rows(today):
        c = str((x or {}).get("code") or "")
        if not c.endswith(".US"): continue
        try: d = dt.date.fromisoformat(str(x.get("report_date"))[:10])
        except Exception: continue
        s = c[:-3].upper()
        if d >= today and (s not in out or d.isoformat() < out[s]): out[s] = d.isoformat()
    return out
def main(dry):
    now = H.now_utc(); today = now.date(); ed = us_dates(today)
    ans = json.load(open(os.path.join(ROOT, "state", "llm_queue", "done", QID + ".json")))
    L = json.load(open(H.LEDGER)); ch = []
    for t in L["trades"]:
        if t["ticker"] == "URA" and t.get("id") == 29 and "grok_second_look" not in t:
            v = ans.get("answer", "").strip()
            t["grok_second_look"] = dict(advisory=True, queue_id=QID, utc=ans.get("utc"), engine=ans.get("engine"),
                                         verdict="PASS" if " PASS" in " " + v[:40] else "SEE_TEXT", grade="B" if "Grade B" in v else None, text=v[:600],
                                         note="ADVISORY ONLY (TFO ruling 2026-10-30): no effect on arm/fill; desk PASS stands")
            ch.append("URA t29 grok_second_look PASS grade B (advisory)")
        if (t["ticker"], t.get("id")) in (("MORN", 27), ("VDE", 24)):
            d = ed.get(t["ticker"])
            if d and t.get("next_earnings_utc") != d:
                t["next_earnings_utc"] = d; t["next_earnings_src"] = "eodhd calendar/earnings %s" % today.isoformat(); ch.append("%s t%s next_earnings_utc=%s (EODHD)" % (t["ticker"], t["id"], d))
            elif not d: ch.append("%s t%s: no EODHD earnings row in %dd window -> UNKNOWN, unchanged" % (t["ticker"], t["id"], EE.WINDOW_D))
    print("\n".join(ch) or "no changes")
    if dry or not any("=" in c or "grok" in c for c in ch): return
    b = H.LEDGER + ".bak-%s-tforul" % dt.datetime.now().strftime("%Y%m%d-%H%M"); shutil.copy2(H.LEDGER, b); print("backup", b)
    H.atomic(H.LEDGER, L); H.event("tforul_stamp", changes=ch)
if __name__ == "__main__": main("--dry-run" in sys.argv)
