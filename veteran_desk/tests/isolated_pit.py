"""A pit connection whose directory already exists outside data/pit.

P.con() still creates the schema. The live data/pit guard is unchanged: a connect
against the default path is refused because that directory does not exist here.
"""
import datetime as dt, os
from veteran_desk import pit as P

def open_pit(directory, monkeypatch):
    path = os.path.join(directory, "pit.sqlite")
    monkeypatch.setattr(P, "DB", path)
    return P.con()

def seed_bars(connection, sym, n=80, close=100.0, spread=1.0, end=None):
    """n completed daily bars ending yesterday, all knowable before now. Not a market download."""
    end = end or (dt.date.today() - dt.timedelta(days=1))
    rows = []
    for i in range(n):
        day = end - dt.timedelta(days=n - 1 - i)
        know = dt.datetime(day.year, day.month, day.day, 21, 0, tzinfo=dt.timezone.utc).isoformat()
        rows.append((sym, day.isoformat(), close, close + spread, close - spread, close, 1_000_000, know, "synthetic"))
    connection.executemany(
        "INSERT OR REPLACE INTO bars(sym,date,o,h,l,c,v,knowable_utc,src) VALUES (?,?,?,?,?,?,?,?,?)", rows)
    connection.commit()
