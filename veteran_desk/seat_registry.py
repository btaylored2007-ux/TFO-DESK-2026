"""seatagents_20261103 (PAPER): ONE data-driven seat roster for THF, read from veteran_desk/seats/*.json.
Drop a new seat json in that folder and it appears in seat mail, the chat desk, the funder seat list and the reporting chain.
Roles: proposer = trader seat; ADMIN_SEATS = the fixed admin trio (run inside rounds); engineer/cfo = STAFF (1:1 chat + mail,
not in ALL broadcasts); head_trader = HT (oversight, Brett's single contact). risk_gate is code only (no mail, no chat).
Reporting chain (Brett 21:45 WITA 2 Nov): traders + staff + admins REPORT TO Marcus (head_of_desk); Marcus reports to HT; HT answers Brett."""
import json, os, time
HERE = os.path.dirname(os.path.abspath(__file__)); SEATS_DIR = os.path.join(HERE, "seats")
HT = "head_trader"; MARCUS = "head_of_desk"; SE = "software_engineer"; CFO = "cfo"
ADMIN_SEATS = ["head_of_desk", "blind_challenger", "devils_advocate"]   # fixed admin trio (round-time challenger / DA / head review)
STAFF_ROLES = ("engineer", "cfo"); CODE_ONLY = ("risk_gate",)
_FALLBACK_TRADE = ["banks", "crypto", "energy", "essentials_utilities", "fx_commodities", "healthcare", "macro", "news_filings", "price_action", "tech_ai_infra", "trend"]
_CACHE = {"sig": None, "S": {}}
def load(force=False):
    """seat id -> seat json (cached on the folder's file mtimes)."""
    try:
        fs = sorted(f for f in os.listdir(SEATS_DIR) if f.endswith(".json"))
        sig = tuple((f, os.path.getmtime(os.path.join(SEATS_DIR, f))) for f in fs)
    except Exception: return dict(_CACHE["S"])
    if not force and sig == _CACHE["sig"]: return dict(_CACHE["S"])
    S = {}
    for f in fs:
        try:
            c = json.load(open(os.path.join(SEATS_DIR, f)))
            if isinstance(c, dict) and c.get("seat"): S[c["seat"]] = c
        except Exception: pass
    _CACHE.update(sig=sig, S=S); return dict(S)
def role(seat, S=None): return str(((S or load()).get(seat) or {}).get("role") or "")
def trade_ids(S=None):
    S = S or load(); out = [s for s, j in S.items() if j.get("role") == "proposer"]
    return sorted(out) or list(_FALLBACK_TRADE)
def admin_ids(S=None):
    S = S or load(); return [s for s in ADMIN_SEATS if s in S] or list(ADMIN_SEATS)
def staff_ids(S=None):
    """Named staff stay addressable without a persona file. risk_gate stays code-only."""
    S = S or load()
    found = [s for s, j in S.items() if j.get("role") in STAFF_ROLES and s not in CODE_ONLY]
    for seat in (SE, CFO):
        if seat not in found and seat not in CODE_ONLY:
            found.append(seat)
    return sorted(found)
def ht_ids(S=None):
    S = S or load(); return [HT] if (HT in S or True) else []
def mail_ids(S=None):
    """Every seat that can send/receive seat mail (traders, admins, staff, head trader); code-only seats excluded."""
    S = S or load(); seen = []
    for s in admin_ids(S) + trade_ids(S) + staff_ids(S) + [HT]:
        if s not in seen and s not in CODE_ONLY: seen.append(s)
    return seen
def chat_all(S=None):
    """Targets of an ALL broadcast in the funder chat: admins + traders (HT and staff are 1:1 only)."""
    S = S or load(); return list(dict.fromkeys(admin_ids(S) + trade_ids(S) + staff_ids(S) + [HT]))
def name(seat, S=None):
    j = (S or load()).get(seat) or {}
    return j.get("trader_name") or {HT: "Head Trader", "fixer": "Fixer (Claude hands)"}.get(seat) or seat
def first_name(seat, S=None):
    n = name(seat, S).replace("(", " ").split(); return n[0] if n else seat
def names(S=None):
    S = S or load(); return {s: name(s, S) for s in mail_ids(S)}
def chat_label(seat, S=None):
    j = (S or load()).get(seat) or {}; r = j.get("role") or ""
    lab = {"proposer": "", "head": " (head of desk)", "head_trader": " (oversight, all AIs)", "challenger": " (blind challenger)", "advocate": " (admin)", "engineer": " (engineer)", "cfo": " (CFO)"}.get(r, " (%s)" % r if r else "")
    n = name(seat, S)
    return n if (lab.strip("() ").lower() in n.lower()) else n + lab
def chat_seats(S=None):
    """[(seat id, label)] for the funder chat seat list: head of desk, head trader, traders, admins, staff, fixer."""
    S = S or load(); order = [MARCUS, HT] + trade_ids(S) + [s for s in admin_ids(S) if s != MARCUS] + staff_ids(S)
    seen = []; out = []
    for s in order:
        if s in seen: continue
        seen.append(s); out.append((s, chat_label(s, S) if s != HT else "HT - Head Trader (oversight, all AIs)"))
    out.append(("fixer", "FIXER - guarded code execution (Simon; ChatGPT Work/Grok/Claude)")); return out
def aliases(S=None):
    """lower-case handle -> seat id: ids, first names, marcus/head/ht/engineer/cfo/all."""
    S = S or load(); A = {s: s for s in mail_ids(S)}
    A.update({"marcus": MARCUS, "head": MARCUS, "hod": MARCUS, "head_of_desk": MARCUS, "ht": HT, "headtrader": HT, "head_trader": HT, "head trader": HT,
              "engineer": SE, "se": SE, "sam": SE, "simon": SE, "software_engineer": SE, "cfo": CFO, "helen": CFO, "news": "news_filings", "tech": "tech_ai_infra",
              "essentials": "essentials_utilities", "fx": "fx_commodities", "pa": "price_action", "all": "all", "everyone": "all", "desk": "all", "seats": "all"})
    for s in mail_ids(S):
        fn = first_name(s, S).lower()
        if fn and fn not in A: A[fn] = s
    return A
def resolve(key, S=None):
    """'@Marcus' / 'marcus' / 'head_of_desk' / 'all' -> seat id | 'all' | None."""
    return aliases(S).get(str(key or "").strip().lstrip("@").lower().replace("-", "_"))
def report_target(seat):
    """Formal reports go UP the chain: traders/admins/staff -> Marcus; Marcus -> Head Trader; Head Trader -> Brett."""
    if seat == HT: return "brett"
    if seat == MARCUS: return HT
    return MARCUS
def schedule(seat, S=None): return dict(((S or load()).get(seat) or {}).get("schedule") or {})
def validate(S=None):
    """Every seat json needs trader_name, role and a schedule. Returns [problem strings] (empty = ok)."""
    S = S or load(); bad = []
    for s, j in S.items():
        for k in ("trader_name", "role", "schedule"):
            if not j.get(k): bad.append("%s: missing %s" % (s, k))
        if j.get("role") in STAFF_ROLES + ("head", "head_trader", "challenger", "advocate", "gate") and j.get("can_arm") not in (False, None): bad.append("%s: admin/staff seat must have can_arm false" % s)
        if j.get("paper_only") is False: bad.append("%s: paper_only must be true" % s)
    return bad
if __name__ == "__main__":
    S = load(); print(json.dumps(dict(trade=trade_ids(S), admin=admin_ids(S), staff=staff_ids(S), mail=mail_ids(S), chat=chat_seats(S), problems=validate(S)), indent=1))
