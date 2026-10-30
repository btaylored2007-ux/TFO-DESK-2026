"""2026-10-30 THF review item 3 (HT): Yahoo FX bars carry v=0. Fill FX volume from OANDA PRACTICE daily candles (read-only GET,
tick volume = number of price updates, NOT traded notional). Writes: table fx_tickvol(sym,date,tick_vol,knowable_utc,src) and
bars.v for FX rows where v=0, with src suffixed '+oanda_tickvol' so consumers know the unit. Pairs OANDA lacks are flagged in
data/pit/fx_volume_status.json (no_volume list). Never touches ledgers/orders."""
import sys, os, json, datetime as dt
B = os.path.dirname(os.path.dirname(os.path.abspath(__file__))); sys.path.insert(0, B)
import oanda_sleeve as O
from veteran_desk import pit as P
def main(count=600):
    c = P.con(); c.execute("CREATE TABLE IF NOT EXISTS fx_tickvol(sym TEXT,date TEXT,tick_vol REAL,knowable_utc TEXT,src TEXT,PRIMARY KEY(sym,date))")
    fx = [r[0] for r in c.execute("SELECT DISTINCT sym FROM universe WHERE sym LIKE '%=X'")]
    have_vol, filled, none = [], {}, {}
    for s in sorted(fx):
        if c.execute("SELECT 1 FROM bars WHERE sym=? AND v>0 AND src NOT LIKE '%oanda_tickvol%' AND date>=date('now','-120 day') LIMIT 1", (s,)).fetchone():
            have_vol.append(s); continue
        p = s[:-2]; inst = p[:3] + "_" + p[3:] if len(p) == 6 else None
        if not inst: none[s] = "not a 6-letter pair"; continue
        try:
            J = O.api("GET", "/v3/instruments/%s/candles" % inst, params=dict(granularity="D", count=count, price="M", dailyAlignment=0, alignmentTimezone="UTC"))
        except Exception as e: none[s] = "oanda: " + str(e)[:90]; continue
        rows = [(x["time"][:10], float(x["volume"])) for x in J.get("candles", []) if x.get("complete")]
        if not rows: none[s] = "oanda returned no complete candles"; continue
        c.executemany("INSERT OR REPLACE INTO fx_tickvol VALUES(?,?,?,?,?)", [(s, d, v, P.knowable(d, "FX"), "oanda_practice_D_mid_tickvol") for d, v in rows])
        n = 0
        for d, v in rows:
            n += c.execute("UPDATE bars SET v=?, src=src||'+oanda_tickvol' WHERE sym=? AND date=? AND (v IS NULL OR v=0)", (v, s, d)).rowcount
        filled[s] = dict(inst=inst, candles=len(rows), bars_updated=n, last=rows[-1][0]); c.commit()
    st = dict(utc=dt.datetime.now(P.UTC).isoformat(), fx_syms=len(fx), native_volume=have_vol, filled_oanda_tickvol=filled,
              no_volume=none, unit_note="bars.v for src containing '+oanda_tickvol' = OANDA tick count (price updates), not notional; compare only within FX.")
    json.dump(st, open(os.path.join(B, "data", "pit", "fx_volume_status.json"), "w"), indent=1)
    print(json.dumps(dict(fx=len(fx), native=len(have_vol), filled=len(filled), no_volume=none)))
if __name__ == "__main__": main()
