"""TEST STAND-IN for yfinance (reads $TFO_WL_MOCK). Never deployed into the desk root; tests copy it to a temp desk."""
import json, os
__version__ = "1.5.2-teststub"

def _d():
    with open(os.environ["TFO_WL_MOCK"]) as f:
        return json.load(f)

class Ticker:
    def __init__(self, sym):
        self.ticker = sym; self._y = _d()["yf"].get(sym)
    @property
    def fast_info(self):
        if not self._y: raise KeyError("mock: no yfinance data for %s" % self.ticker)
        return dict(self._y["fast_info"])
    @property
    def info(self):
        return dict(self._y.get("info", {})) if self._y else {}
    @property
    def news(self):                        # yfinance 1.5.x shape: [{'id', 'content': {title, summary, pubDate, contentType}}]
        if self._y and self._y.get("news_error"): raise RuntimeError("mock: news fetch failed")
        return list((self._y or {}).get("news", []))
    @property
    def calendar(self):                    # yfinance 1.5.x shape: {'Earnings Date': [datetime.date, ...], ...}
        if self._y and self._y.get("cal_error"): raise RuntimeError("mock: calendar fetch failed")
        import datetime as _dt
        cal = dict((self._y or {}).get("calendar", {}))
        if "Earnings Date" in cal: cal["Earnings Date"] = [_dt.date.fromisoformat(x) for x in cal["Earnings Date"]]
        return cal
    def history(self, period="5d", **kw):
        import pandas as pd
        return pd.DataFrame()

def download(tickers, period=None, interval=None, auto_adjust=None, group_by=None, progress=None, threads=None, **kw):
    import pandas as pd
    d = _d()["yf"]; frames = {}
    for s in ([tickers] if isinstance(tickers, str) else tickers):
        h = (d.get(s) or {}).get("hist")
        if not h: continue
        frames[s] = pd.DataFrame({"Open": [r[1] for r in h], "High": [r[2] for r in h], "Low": [r[3] for r in h],
                                  "Close": [r[4] for r in h], "Adj Close": [r[4] for r in h], "Volume": [r[5] for r in h]},
                                 index=pd.to_datetime([r[0] for r in h]))
    return pd.concat(frames, axis=1) if frames else pd.DataFrame()
