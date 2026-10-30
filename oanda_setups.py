"""oanda_setups.py - TFO OANDA PRACTICE sleeve, 16 Oct 2026 (Brett 20:49 AEDT: widen universe, add proven daily setups, raise utilisation).
Pure signal maths shared by the live sleeve (oanda_designs.signals) and the backtest (work/oanda_v3/backtest.py) so they cannot drift.
Every function looks only at completed daily bars up to and including index i (no look-ahead). Entry = next session, stop = 2 x Wilder ATR20,
exit = 3 x ATR20 chandelier trail from the best close since entry (tighten-only) or the setup's own exit rule.
  OXP1 trend pullback : EMA50 > EMA200 and close > EMA200 (mirror short); within the last 3 bars the low touched EMA20; trigger = close back
                        above the prior bar's high (mirror: below prior low). Exit: close through EMA200 or trail.
  OXB1 vol-filtered breakout : close beyond the prior 55-bar high/low while ATR20 was in the bottom half of its own 250-bar range on the prior
                        bar (compression -> expansion) and EMA50/EMA200 agree with the direction. Exit: 20-bar opposite extreme or trail.
  OXC1 carry-aligned trend : 126-bar momentum sign == EMA50/EMA200 sign == sign of OANDA's financing credit (FX only); entry on the first bar the
                        three agree. Exit: momentum or EMA sign flips, or trail.
"""
def ema_list(xs, n):
    k = 2.0 / (n + 1); out = []; e = None
    for i, x in enumerate(xs):
        if i < n - 1: out.append(None); continue
        if e is None: e = sum(xs[i - n + 1:i + 1]) / n
        else: e = x * k + e * (1 - k)
        out.append(e)
    return out
def atr_list(cs, n=20):
    tr = [cs[0]["h"] - cs[0]["l"]] + [max(c["h"] - c["l"], abs(c["h"] - p["c"]), abs(c["l"] - p["c"])) for p, c in zip(cs, cs[1:])]
    out = [None] * len(cs); a = None
    for i in range(len(cs)):
        if i == n - 1: a = sum(tr[:n]) / n
        elif i >= n: a = (a * (n - 1) + tr[i]) / n
        out[i] = a
    return out
class Ind:
    def __init__(self, cs):
        self.cs = cs; c = [x["c"] for x in cs]; self.c = c
        self.e20, self.e50, self.e200 = ema_list(c, 20), ema_list(c, 50), ema_list(c, 200); self.atr = atr_list(cs)
MIN_BARS = 260
def pullback(I, i):
    cs = I.cs
    if i < MIN_BARS or None in (I.e20[i], I.e50[i], I.e200[i]): return 0
    up = I.e50[i] > I.e200[i] and I.c[i] > I.e200[i]; dn = I.e50[i] < I.e200[i] and I.c[i] < I.e200[i]
    if up and any(cs[j]["l"] <= I.e20[j] for j in range(i - 3, i)) and I.c[i] > cs[i - 1]["h"] and I.c[i] > I.e20[i]: return 1
    if dn and any(cs[j]["h"] >= I.e20[j] for j in range(i - 3, i)) and I.c[i] < cs[i - 1]["l"] and I.c[i] < I.e20[i]: return -1
    return 0
def breakout(I, i, n=55):
    cs = I.cs
    if i < MIN_BARS or None in (I.e50[i], I.e200[i]): return 0
    hist = [a for a in I.atr[i - 250:i] if a]
    if len(hist) < 200: return 0
    lo, hi = min(hist), max(hist); a = I.atr[i - 1]
    if hi <= lo or (a - lo) / (hi - lo) > 0.5: return 0
    H = max(x["h"] for x in cs[i - n:i]); L = min(x["l"] for x in cs[i - n:i])
    if I.c[i] > H and I.e50[i] > I.e200[i]: return 1
    if I.c[i] < L and I.e50[i] < I.e200[i]: return -1
    return 0
def trend_state(I, i):
    if i < MIN_BARS or None in (I.e50[i], I.e200[i]): return 0
    m = 1 if I.c[i] > I.c[i - 126] else -1; e = 1 if I.e50[i] > I.e200[i] else -1
    return m if m == e else 0
def carry_trend(I, i, carry_sign):
    """carry_sign: +1 long earns financing credit, -1 short does, 0 neither."""
    s = trend_state(I, i)
    if not s or s != carry_sign: return 0
    return s if trend_state(I, i - 1) != s else 0
def passive(I, i):
    """OXT1 (16 Oct 2026, GPT idea 3, commodities only): +1 if 63/126/252-bar returns all > 0, -1 if all < 0, else 0. State entry."""
    if i < 260: return 0
    r = [I.c[i] / I.c[i - k] - 1 for k in (63, 126, 252)]
    return 1 if all(x > 0 for x in r) else -1 if all(x < 0 for x in r) else 0
def trigger_level(I, i, d):
    """GPT ideas 1-2: stop-entry level = signal-bar high + 0.05 ATR (long) / low - 0.05 ATR (short); valid for the next session only."""
    a = I.atr[i]; return I.cs[i]["h"] + 0.05 * a if d > 0 else I.cs[i]["l"] - 0.05 * a
def exit_signal(design, I, i, d):
    """True if the setup's own exit rule fires for a position in direction d on bar i."""
    if design == "OXP1": return (d > 0 and I.c[i] < I.e200[i]) or (d < 0 and I.c[i] > I.e200[i])
    if design == "OXB1":   # v2 (16 Oct 2026, GPT idea 1): opposite 55-bar extreme
        cs = I.cs; return (d > 0 and I.c[i] < min(x["l"] for x in cs[i - 55:i])) or (d < 0 and I.c[i] > max(x["h"] for x in cs[i - 55:i]))
    if design == "OXT1": return passive(I, i) != d
    if design == "OXC1": return trend_state(I, i) != d
    return False
