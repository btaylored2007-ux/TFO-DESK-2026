"""tfo_hub_20261101: read-only hub aggregator for THF / War Room / VD (PAPER). No trading logic, GET-only."""
import json, urllib.request, time
VD_API = "http://127.0.0.1:28860/api/state"
WR_API = "http://100.120.8.11:18812/api/m"
WR_POS = "http://100.120.8.11:18812/api/positions"
LINKS = dict(thf="http://100.120.8.11:18870/desk/", thf_phone="http://100.120.8.11:18870/desk/phone",
             war_room="http://100.120.8.11:18812/warroom", war_room_home="http://100.120.8.11:18812/",
             vd="http://100.120.8.11:18860/", hub="http://100.120.8.11:18870/hub")
def _get(u, t=6):
    with urllib.request.urlopen(u, timeout=t) as r: return json.loads(r.read().decode())
def _book(s):
    t = s.get("today") or {}; lb = (s.get("leaderboard") or {}).get("rows") or []
    cards = {c.get("seat"): c for c in (s.get("cards") or [])}
    rows = []
    for r in lb:
        b = (cards.get(r.get("seat")) or {}).get("best") or {}
        rows.append(dict(seat=r.get("seat"), trader=(cards.get(r.get("seat")) or {}).get("trader"), rank=r.get("rank"),
            equity=r.get("equity_aud"), day=r.get("day_pnl_aud"), since=r.get("pnl_since_inception_aud"), open=r.get("open_trades"),
            best=dict(sym=b.get("sym"), side=b.get("side"), grade=b.get("grade"), score=b.get("score"), why=b.get("why_no_wake")) if b else None))
    return dict(ok=True, label=t.get("book_label"), equity=t.get("equity_aud"), today=t.get("today_pnl_aud"), since=t.get("pnl_since_inception_aud"),
                cash=t.get("cash_aud"), updated=t.get("book_updated"), built=s.get("built_utc"), broken=len(s.get("broken") or []), seats=rows)
def hub(thf_state):
    out = dict(asof=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), links=LINKS)
    try: out["thf"] = _book(thf_state)
    except Exception as e: out["thf"] = dict(ok=False, error=str(e)[:160])
    try: out["vd"] = _book(_get(VD_API))
    except Exception as e: out["vd"] = dict(ok=False, error=str(e)[:160])
    try:
        m = _get(WR_API); mn = m.get("main") or {}
        try: pos = (_get(WR_POS).get("rows") or [])
        except Exception: pos = []
        out["wr"] = dict(ok=True, generated=m.get("generated_utc"), equity=mn.get("equity_net_usd"), today=mn.get("today_change_usd"),
            dd_pct=mn.get("dd_pct"), level=mn.get("level"), scoreboard=m.get("scoreboard"),
            sleeves=[dict(name=x.get("name"), value=x.get("value_usd"), label=x.get("value_label"), badge=x.get("badge"), detail=x.get("detail")) for x in (m.get("sleeves") or [])],
            feeds=[dict(label=f.get("label"), status=f.get("status"), age_s=f.get("age_s")) for f in ((m.get("health") or {}).get("feeds") or [])],
            positions=[dict(book=p.get("book"), sym=p.get("symbol"), side=p.get("side"), value=p.get("value"), upnl=p.get("upnl"), upnl_pct=p.get("upnl_pct")) for p in pos])
    except Exception as e: out["wr"] = dict(ok=False, error=str(e)[:160])
    return out
