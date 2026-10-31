"""2026-10-30 THF review item 5 (HT): one engine-readable borrow rule file -> state/borrow_status.json.
Sources: state/ibkr_borrow.json (IBKR public FTP usa/australia: shortable qty + fee) and state/veteran_desk/borrow_intl.json (IBKR FTP intl).
RULE (never loosened): a short whose symbol has no fresh (<24h) borrow row with ok=True and available>0 is CAPPED: max grade 'A' (no A+), and
gate_v2's existing x0.75 size haircut / hard block on refused borrow still applies. Markets with zero rows (e.g. India, Singapore, Korea, Taiwan, NZ)
are short_capped for every name. Usage: from veteran_desk.borrow_status import short_cap; short_cap('BHP.AX') -> dict(capped, max_grade, why)."""
import json, os, datetime as dt
B = os.path.dirname(os.path.dirname(os.path.abspath(__file__))); OUT = os.path.join(B, "state", "borrow_status.json")
SUF = {"": "us", "AX": "asx", "L": "british", "DE": "germany", "PA": "france", "AS": "dutch", "BR": "belgium", "MI": "italy", "MC": "spain", "SW": "swiss",
       "ST": "swedish", "VI": "austria", "TO": "canada", "V": "canada", "HK": "hongkong", "T": "japan", "NS": "india", "BO": "india", "SI": "singapore",
       "KS": "korea", "KQ": "korea", "TW": "taiwan", "TWO": "taiwan", "NZ": "nz", "CO": "denmark", "HE": "finland", "OL": "norway"}
def _j(p):
    try: return json.load(open(p))
    except Exception: return {}
def build():
    now = dt.datetime.now(dt.timezone.utc); us = _j(os.path.join(B, "state", "ibkr_borrow.json")); it = _j(os.path.join(B, "state", "veteran_desk", "borrow_intl.json"))
    mk = {}
    for k in ("us", "asx"):
        n = ((us.get(k) or {}).get("names") or {}); mk[k] = dict(rows=len(n), updated_utc=us.get("updated_utc"), src="ibkr_ftp")
    for k, v in (it.get("markets") or {}).items(): mk[k] = dict(rows=len(v.get("names") or {}), updated_utc=it.get("updated_utc"), src="ibkr_ftp_intl", fee_estimated=bool(it.get("estimated")))
    def fresh(u):
        try: return (now - dt.datetime.fromisoformat(u.replace("Z", "+00:00"))).total_seconds() < 86400
        except Exception: return False
    for k, v in mk.items(): v["fresh_24h"] = fresh(v.get("updated_utc") or ""); v["usable"] = v["rows"] > 0 and v["fresh_24h"]
    for m in set(SUF.values()) - set(mk): mk[m] = dict(rows=0, usable=False, why="no free IBKR borrow file for this market")
    J = dict(built_utc=now.isoformat(), ibkr_gateway="paper gateway :4002 (daily relogin 17:35 WITA); borrow comes from IBKR public FTP, not the gateway",
             rule=dict(no_borrow_row="short capped: max_grade A (no A+); gate_v2 size x0.75", borrow_refused_or_zero="short blocked (gate_v2 hard)",
                       stale_over_24h="treated as no borrow row", never_loosen=True),
             suffix_market=SUF, markets=mk, capped_markets=sorted(k for k, v in mk.items() if not v["usable"]))
    tmp = OUT + ".tmp"; json.dump(J, open(tmp, "w"), indent=1); os.replace(tmp, OUT); return J
def short_cap(sym):
    J = _j(OUT); sfx = sym.rsplit(".", 1)[-1].upper() if "." in sym else ""; m = (J.get("suffix_market") or SUF).get(sfx)
    v = (J.get("markets") or {}).get(m) or {}
    if not v.get("usable"): return dict(capped=True, max_grade="A", why="no usable borrow data for market %s" % m)
    names = {}
    if m in ("us", "asx"): names = ((_j(os.path.join(B, "state", "ibkr_borrow.json")).get(m) or {}).get("names") or {})
    else: names = (((_j(os.path.join(B, "state", "veteran_desk", "borrow_intl.json")).get("markets") or {}).get(m) or {}).get("names") or {})
    r = names.get(sym.split(".")[0])
    if not r: return dict(capped=True, max_grade="A", why="no borrow row for %s" % sym)
    if not (r.get("ok") and (r.get("available") or 0) > 0): return dict(capped=True, blocked=True, max_grade=None, why="borrow refused/none available")
    return dict(capped=False, max_grade="A+", fee_pa=r.get("fee_pa"), available=r.get("available"))
def dash(path=None):
    """Read-only borrow freshness for the THF dashboard. Does not pull FTP or rewrite the cache."""
    now = dt.datetime.now(dt.timezone.utc)
    us = _j(path or os.path.join(B, "state", "ibkr_borrow.json"))
    age = None
    try:
        u = dt.datetime.fromisoformat(str(us.get("updated_utc")).replace("Z", "+00:00"))
        if u.tzinfo is None: u = u.replace(tzinfo=dt.timezone.utc)
        age = (now - u).total_seconds() / 3600.0
    except Exception: age = None
    usn = len(((us.get("us") or {}).get("names") or {})); asxn = len(((us.get("asx") or {}).get("names") or {}))
    fresh = age is not None and -5 / 60 <= age <= 24 and (usn or asxn) > 0
    return dict(status="fresh" if fresh else ("stale" if us.get("updated_utc") else "missing"), fresh_24h=bool(fresh),
                age_h=None if age is None else round(age, 2), updated_utc=us.get("updated_utc"), us_names=usn, asx_names=asxn,
                src="state/ibkr_borrow.json",
                note="IBKR public FTP snapshot already pulled by ibkr_costs.py (state/ibkr_borrow.json). This card only reports freshness.")
if __name__ == "__main__":
    J = build(); print(json.dumps(dict(capped_markets=J["capped_markets"], usable={k: v["rows"] for k, v in J["markets"].items() if v["usable"]})))
    for s in ("AAPL", "BHP.AX", "HSBA.L", "RELIANCE.NS", "005930.KS"): print(s, short_cap(s))
