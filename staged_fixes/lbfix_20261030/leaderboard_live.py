#!/usr/bin/env python3
"""Live VD + THF leaderboard (PAPER). Read-only over books; writes reports/leaderboard_live.json. tfo 20-min push 2026-10-30."""
import json, datetime as dt, os
ROOT=os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
P=lambda *a: os.path.join(ROOT,*a)
WITA=dt.timezone(dt.timedelta(hours=8))
now=dt.datetime.now(WITA)
cut=now.replace(hour=6,minute=0,second=0,microsecond=0)
if now<cut: cut-=dt.timedelta(days=1)
monday=(cut-dt.timedelta(days=cut.weekday())).replace(hour=6)
G={'A+':4,'A':3,'B':2,'C':1}
def ts(s):
    try: return dt.datetime.fromisoformat(s)
    except Exception: return None
def seat_day(s):
    if s.get('equity_aud') is not None and s.get('day_start_equity_aud') is not None: return s['equity_aud']-s['day_start_equity_aud']
    return s.get('today_pnl_aud',0.0) or 0.0
def board(book_path, lb_rows=None, desk=''):
    b=json.load(open(book_path)); rows=[]
    lbm={r['seat']:r for r in (lb_rows or [])}
    for seat,s in b['seats'].items():
        closed=[c for c in s.get('closed',[]) if c.get('status')=='closed']
        wk=[c for c in closed if (ts(c.get('closed_utc','')) or now)>=monday]
        R=lambda cs: round(sum((c.get('net_aud') or 0)/c['risk_aud'] for c in cs if (c.get('risk_aud') or 0)>=500),2)
        wins=sum(1 for c in closed if (c.get('net_aud') or 0)>0)
        pos=list((s.get('positions') or {}).values())
        grades=[G[x.get('grade')] for x in closed+pos if x.get('grade') in G]
        armed=sorted([p for p in pos if p.get('status')=='armed'],key=lambda p:-G.get(p.get('grade'),0))
        L=lbm.get(seat,{})
        eq=s.get('equity_aud') or L.get('equity_aud') or s.get('seed_aud')
        # lbfix 20261030: day P&L comes from the BOOK (equity - day_start_equity) only; the prize leaderboard's
        # day_pnl_aud uses a different window/prize accounting and double-counted vs the book (-A$9-10k vs -A$2.4k).
        day=seat_day(s)
        wtd=L.get('wtd_pnl_aud', s.get('pnl_since_inception_aud',0.0)) or 0.0
        rows.append({'seat':seat,'today_pnl_aud':round(day,2),'week_pnl_aud':round(wtd,2),
          'R_banked_total':R(closed),'R_banked_week':R(wk),'closed_trades':len(closed),
          'win_rate':round(wins/len(closed),2) if closed else None,
          'avg_grade':round(sum(grades)/len(grades),2) if grades else None,
          'open':sum(1 for p in pos if p.get('status')=='open'),'armed':len(armed),
          'armed_shortlist':[{'ticker':p.get('ticker'),'side':p.get('side'),'grade':p.get('grade'),'entry':p.get('entry'),'stop':p.get('stop'),'target':p.get('target'),'note':p.get('exec_note')} for p in armed[:3]],
          'idle':not closed and not pos})
    rows.sort(key=lambda r:(-r['today_pnl_aud'],-r['week_pnl_aud'],-r['R_banked_week']))
    for i,r in enumerate(rows,1): r['rank']=i
    bd=b.get('today_pnl_aud'); tot=round(sum(r['today_pnl_aud'] for r in rows),2)
    rec={'book_today_aud':bd,'board_today_aud':tot,'diff_aud':(round(tot-bd,2) if bd is not None else None),'ok':(bd is None or abs(tot-bd)<=50)}
    return {'desk':desk,'rows':rows,'reconcile':rec,'desk_today_aud':round(sum(r['today_pnl_aud'] for r in rows),2),'desk_week_aud':round(sum(r['week_pnl_aud'] for r in rows),2)}
def main():
    vdlb=json.load(open(P('state/veteran_desk/leaderboard.json')))
    out={'asof_wita':now.strftime('%Y-%m-%d %H:%M WITA'),'today_window_from':cut.strftime('%Y-%m-%d %H:%M WITA'),
     'week_from':monday.strftime('%Y-%m-%d %H:%M WITA'),'basis':'A$ P&L after fees (marks incl. open); R = net/risk on closed trades; PAPER only',
     'veteran_desk':board(P('reports/veteran_desk_book.json'),vdlb.get('rows'),'Veteran Desk'),
     'taylor_hedge_fund':board(P('state/thf/thf_book.json'),None,'Taylor Hedge Fund')}
    tmp=P('reports/leaderboard_live.json.tmp'); json.dump(out,open(tmp,'w'),indent=1); os.replace(tmp,P('reports/leaderboard_live.json'))
    for k in ('veteran_desk','taylor_hedge_fund'):
        B=out[k]; print(f"== {B['desk']} today {B['desk_today_aud']:+,.0f} (book {B['reconcile']['book_today_aud']}, diff {B['reconcile']['diff_aud']}, ok {B['reconcile']['ok']}) week {B['desk_week_aud']:+,.0f}")
        for r in B['rows']:
            print(f"{r['rank']:>2} {r['seat']:<21} d{r['today_pnl_aud']:>+10,.0f} w{r['week_pnl_aud']:>+10,.0f} R{r['R_banked_week']:>+5} n{r['closed_trades']} wr{r['win_rate']} g{r['avg_grade']} o{r['open']} a{r['armed']} {[(a['ticker'],a['grade']) for a in r['armed_shortlist']]}{' IDLE' if r['idle'] else ''}")
if __name__=='__main__': main()
