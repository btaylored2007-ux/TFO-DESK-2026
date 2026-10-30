import importlib.util, json, os
ROOT=os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
def _mod():
    p=os.path.join(ROOT,'tools','leaderboard_live.py'); sp=importlib.util.spec_from_file_location('lbl',p); m=importlib.util.module_from_spec(sp); sp.loader.exec_module(m); return m
def test_board_day_equals_book_not_prize_window(tmp_path):
    m=_mod()
    seats={'a':{'equity_aud':101000,'day_start_equity_aud':100000,'today_pnl_aud':1000,'closed':[],'positions':{}},
           'b':{'equity_aud':96500,'day_start_equity_aud':100000,'today_pnl_aud':-3500,'closed':[],'positions':{}}}
    bk=tmp_path/'b.json'; bk.write_text(json.dumps({'seats':seats,'today_pnl_aud':-2500}))
    lb=[{'seat':'a','day_pnl_aud':-5000},{'seat':'b','day_pnl_aud':-6000}]   # stale/prize-window figures must be ignored
    B=m.board(str(bk),lb,'t')
    assert B['desk_today_aud']==-2500 and B['reconcile']['ok'] and abs(B['reconcile']['diff_aud'])<=50
