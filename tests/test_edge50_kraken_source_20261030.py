"""edge50kraken 2026-10-30: Kraken daily OHLC primary for CRYPTO_TR, Yahoo fallback; signal unchanged. Mocks only, no network, temp state."""
import os, sys, tempfile, importlib
import numpy as np, pandas as pd
os.environ['EDGE50_STATE']=tempfile.mkdtemp()
FWD=os.environ.get('EDGE50_FWD_DIR',os.path.join(os.path.dirname(os.path.abspath(__file__)),'..','research','edge50','fwd'))
sys.path.insert(0,os.path.abspath(FWD))
E=importlib.import_module('edge50_fwd')
IDX=pd.date_range('2026-06-01','2026-10-30',freq='D')
NOW=pd.Timestamp('2026-10-30 13:00',tz='UTC')
def px(t): return pd.Series(np.linspace(90000,77000,len(IDX)) if t=='BTC-USD' else np.linspace(3000,2226,len(IDX)),index=IDX)
def kfetch(pair):
    t={'XBTUSD':'BTC-USD','ETHUSD':'ETH-USD'}[pair]; s=px(t)
    return {'error':[],'result':{pair:[[int(d.timestamp()),'0','0','0',str(v),'0','0',1] for d,v in s.items()],'last':0}}
def yf_loader(tk,period='1y'):
    cols=pd.MultiIndex.from_product([['Close'],tk])
    return pd.DataFrame({('Close',t):(px(t) if t!='AUDUSD=X' else pd.Series(0.66,index=IDX)) for t in tk},columns=cols)
def test_kraken_daily_parses_utc_bars():
    AC=E.kraken_daily(fetch=kfetch,now=NOW)
    assert list(AC.columns)==['BTC-USD','ETH-USD'] and AC.index[-1]==pd.Timestamp('2026-10-30')
    assert abs(AC['BTC-USD'].iloc[-1]-77000)<1e-6
def test_kraken_primary_used_when_available():
    called=[]
    def yl(tk,period='1y'): called.append(tuple(tk)); return yf_loader(tk,period)
    M=E.load_crypto(kraken=lambda: E.kraken_daily(fetch=kfetch,now=NOW),yf_loader=yl)
    assert M['source']=='KRAKEN_OHLC_DOH' and called==[('AUDUSD=X',)]
    assert M['fx'].notna().all()
def test_yahoo_fallback_on_failure():
    def boom(): raise RuntimeError('dns sinkhole')
    M=E.load_crypto(kraken=boom,yf_loader=yf_loader)
    assert M['source']=='YAHOO_FALLBACK' and list(M['AC'].columns)==['BTC-USD','ETH-USD']
def test_stale_kraken_rejected():
    import pytest
    with pytest.raises(RuntimeError): E.kraken_daily(fetch=kfetch,now=NOW+pd.Timedelta(days=5))
def test_signal_unchanged_between_sources():
    Mk=E.load_crypto(kraken=lambda: E.kraken_daily(fetch=kfetch,now=NOW),yf_loader=yf_loader)
    def boom(): raise RuntimeError('x')
    My=E.load_crypto(kraken=boom,yf_loader=yf_loader)
    for d in pd.date_range('2026-10-24','2026-10-30'):
        assert E.crypto_target(Mk,d)==E.crypto_target(My,d)=={'BTC-USD':0.0,'ETH-USD':0.0}
