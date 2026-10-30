#!/usr/bin/env python3
"""ASIA MARKET CONFIG for the 48-72h hunt books (PAPER ONLY; no broker, no orders, no keys). Built 2026-10-27 on Brett's
"Do exactly the same setup for the Asian markets" (pt1481u). One book per market: jp (TSE), hk (HKEX), sg (SGX), kr (KRX), tw (TWSE+TPEx).
Costs = IBKR published schedule, https://www.interactivebrokers.com/en/pricing/commissions-stocks-asia-pacific.php (fetched 2026-10-27)
 + third-party pages: HK https://www.interactivebrokers.com.hk/en/accounts/fees/SEHK-StkFees.php,
   KR https://www.interactivebrokers.com/en/accounts/fees/KOREA-StkFees.php, TW https://www.interactivebrokers.com.hk/en/accounts/fees/TAIWAN-StkFees.php
Shortability: https://www.interactivebrokers.com/en/trading/short-securities-availability.php (2026-10-27: Japan 933 shortable, HK 730,
 Singapore 'None available at this time', Korea/Taiwan not offered) + IBKR short-stock FTP japan.txt / hongkong.txt (singapore.txt empty, korea/taiwan absent).
WITA = UTC+8. Holidays: best-effort list to end Jan 2027 (exchange calendars), yfinance bars are the final truth."""
import datetime as dt
# TFO DoH hook 2026-10-28 (Brett item B4: desk_doh process-local DoH for IBKR + Binance/Coinbase/OKX/Alpaca/Polymarket; no system DNS; kill switch TFO_DESK_DOH=0)
try:
    import desk_doh as _doh; _doh.install(extra=True)
except Exception as _e:
    print("desk_doh install skipped:", _e, file=__import__("sys").stderr)
D = dt.date
MARKETS = {
 "jp": dict(name="Japan (TSE)", tz="Asia/Tokyo", sessions=[("09:00", "11:30"), ("12:30", "15:30")], idx="^N225", ccy="JPY", fx="JPYAUD=X", lot=100,
            suffix=".T", shorts=True, ibkr_file="japan", open_wita="08:00", close_wita="14:30",
            short_rule="SHORTS OK only with IBKR borrow confirmed in japan.txt (FEERATE/AVAILABLE). JP price restriction (uptick rule) applies after a 10% fall - paper fill assumes trigger is a sale at/above last price.",
            comm=dict(plan="IBKR Pro Fixed", pct=0.0008, min=80.0), third=dict(), sell_tax=0.0,
            cost_src="IBKR Fixed Japan 0.08% min JPY80/order; Fixed third-party fees: None; no stamp/transaction tax",
            holidays={D(2026, 11, 3), D(2026, 11, 23), D(2026, 12, 31), D(2027, 1, 1), D(2027, 1, 11)}),
 "hk": dict(name="Hong Kong (HKEX)", tz="Asia/Hong_Kong", sessions=[("09:30", "12:00"), ("13:00", "16:00")], idx="^HSI", ccy="HKD", fx="HKDAUD=X", lot=None,
            suffix=".HK", shorts=True, ibkr_file="hongkong", open_wita="09:30", close_wita="16:00",
            short_rule="SHORTS OK only if (a) on HKEX Designated Securities Eligible for Short Selling (ListOfSecurities 'Shortsell Eligible'=Y) AND (b) IBKR borrow confirmed in hongkong.txt. HK tick rule: short sale not below best ask (paper fill noted). Naked shorts illegal.",
            comm=dict(plan="IBKR Pro Fixed", pct=0.0008, min=18.0), third=dict(sfc=0.000027, frc=0.0000015), stamp=0.001, sell_tax=0.0,
            cost_src="IBKR Fixed HK 0.08% min HKD18/order + regulatory: stamp duty 0.1% each side rounded up to HKD1, SFC levy 0.0027%, FRC levy 0.00015% (exchange/clearing included in Fixed)",
            holidays={D(2026, 12, 25), D(2026, 12, 28), D(2027, 1, 1)}),
 "sg": dict(name="Singapore (SGX)", tz="Asia/Singapore", sessions=[("09:00", "17:00")], idx="^STI", ccy="SGD", fx="SGDAUD=X", lot=100,
            suffix=".SI", shorts=False, ibkr_file="singapore", open_wita="09:00", close_wita="17:00",
            short_rule="LONG-ONLY: IBKR lists Singapore shortable stocks 'None available at this time' and singapore.txt is empty.",
            comm=dict(plan="IBKR Pro Fixed", pct=0.0008, min=2.5), third=dict(), sell_tax=0.0,
            cost_src="IBKR Fixed SGX 0.08% min SGD2.50/order; Fixed third-party fees: None (GST may apply to SG-resident accounts - not modelled, Brett not SG resident)",
            holidays={D(2026, 11, 9), D(2026, 12, 25), D(2027, 1, 1)}),
 "kr": dict(name="Korea (KRX: KOSPI .KS + KOSDAQ .KQ)", tz="Asia/Seoul", sessions=[("09:00", "15:30")], idx="^KS11", ccy="KRW", fx="KRWAUD=X", lot=1,
            suffix=".KS", shorts=False, ibkr_file="korea", open_wita="08:00", close_wita="14:30",
            short_rule="LONG-ONLY: IBKR offers no Korean shortable list/borrow (absent from short-securities page and FTP); Korea also requires pre-borrow + naked-short monitoring system for short sellers.",
            comm=dict(plan="IBKR Pro Tiered (only plan offered)", pct=0.0006, min=4000.0), third=dict(), sell_tax=0.002,
            cost_src="IBKR Tiered Korea Tier I 0.06% min KRW4,000/order + Securities Transaction Tax 0.2% on sells (IBKR KOREA-StkFees page)",
            holidays={D(2026, 12, 25), D(2026, 12, 31), D(2027, 1, 1)}),
 "tw": dict(name="Taiwan (TWSE .TW + TPEx .TWO)", tz="Asia/Taipei", sessions=[("09:00", "13:30")], idx="^TWII", ccy="TWD", fx="TWDAUD=X", lot=1000,
            suffix=".TW", shorts=False, ibkr_file="taiwan", open_wita="09:00", close_wita="13:30",
            short_rule="LONG-ONLY: IBKR offers no Taiwan short selling (absent from short-securities page/FTP); TWSE disposition stocks are not tradable via IBKR.",
            comm=dict(plan="IBKR Pro Tiered (only plan offered)", pct=0.0008, min=80.0), third=dict(exec_fee=0.0002), sell_tax=0.003,
            cost_src="IBKR Tiered Taiwan Tier I 0.08% min TWD80/order + execution fee 0.02% + Securities Transaction Tax 0.3% on stock sales (0.15% day trades; IBKR TAIWAN-StkFees page)",
            holidays={D(2026, 12, 25), D(2027, 1, 1)}),
 # 2026-10-27 19:14 WITA Brett pt1522u "Build it and add the same capital": LONDON 48-72h hunt book (PAPER ONLY), same engine/rules as jp/hk/sg/kr/tw.
 # Prices: yfinance '.L' bars are quoted in PENCE (GBp) -> fx = GBPAUD=X / px_div(100) = A$ per penny. Universe: FTSE 350 (FTSE 100 + FTSE 250), liquid only (A$5m 20d ADV).
 # Costs (local = pence): IBKR Pro Fixed UK 0.05% min GBP3/order (https://www.interactivebrokers.co.uk/en/pricing/commissions-stocks.php, fetched 2026-10-27; Fixed = no
 # third-party fees) + UK stamp duty/SDRT 0.5% on EVERY BUY (incl. a short's covering buy, conservative) + PTM levy GBP1.50 per order > GBP10k
 # (https://www.interactivebrokers.co.uk/en/includes/disclosures/eu-stk-ptm-levy.php) + half the quoted spread per side (FTSE100 8bp / FTSE250 30bp full spread, conservative est.)
 # + engine's 10bp slip/side + half tick/side. Session 08:00-16:30 London = 16:00-00:30 WITA (GMT) / 15:00-23:30 WITA (BST); tz Europe/London so DST is automatic.
 "lse": dict(name="London (LSE, FTSE 350)", tz="Europe/London", sessions=[("08:00", "16:30")], idx="^FTSE", ccy="GBp", fx="GBPAUD=X", px_div=100.0, lot=1,
             suffix=".L", shorts=True, ibkr_file="british", open_wita="16:00", close_wita="00:30", min_adv_aud=5e6,
             hours_note="London 08:00-16:30 = 16:00-00:30 WITA while UK is on GMT (15:00-23:30 WITA during BST)",
             entry_rule="Plan built pre-open (13:15 WITA chain). Opening auction 07:50-08:00 London: fills only on COMPLETED 1h bars from the 08:00 continuous open (first possible fill 09:00 London), so no entry inside the auction window.",
             short_rule="SHORTS only with IBKR borrow confirmed in british.txt (file unavailable -> no short arms, fail closed). Stamp duty 0.5% charged on the covering BUY too (conservative).",
             comm=dict(plan="IBKR Pro Fixed (UK SmartRouting)", pct=0.0005, min=300.0), third=dict(), sell_tax=0.0, buy_stamp=0.005, ptm=dict(levy=150.0, over=1_000_000.0),
             spread_bp=dict(ftse100=8.0, ftse250=30.0, default=30.0),
             cost_src="IBKR Fixed UK 0.05% min GBP3/order + UK stamp duty (SDRT) 0.5% on buys + PTM levy GBP1.50/order >GBP10k + half quoted spread/side (FTSE100 8bp, FTSE250 30bp full spread est.)",
             cost_R_max=0.30,  # Brett 2026-10-27 19:44 WITA: London-only (stamp 0.5%); every other hunt48 book stays 0.15
             holidays={D(2026, 12, 25), D(2026, 12, 28), D(2027, 1, 1)}),
}
def tick(mkt, p):
    """minimum price step (local). Used for the half-tick/side slippage term."""
    if mkt == "jp": return 1 if p <= 3000 else 5 if p <= 5000 else 10 if p <= 30000 else 50 if p <= 50000 else 100
    if mkt == "hk":
        for lim, t in ((0.25, 0.001), (0.5, 0.005), (10, 0.01), (20, 0.02), (100, 0.05), (200, 0.1), (500, 0.2), (1000, 0.5), (2000, 1), (5000, 2)):
            if p <= lim: return t
        return 5
    if mkt == "sg": return 0.001 if p < 0.2 else 0.005 if p < 1 else 0.01
    if mkt == "kr": return 1 if p < 2000 else 5 if p < 5000 else 10 if p < 20000 else 50 if p < 50000 else 100 if p < 200000 else 500 if p < 500000 else 1000
    if mkt == "tw": return 0.01 if p < 10 else 0.05 if p < 50 else 0.1 if p < 100 else 0.5 if p < 500 else 1 if p < 1000 else 5
    if mkt == "lse":     # pence; coarse MiFID II liquid-band tick table (approximation, conservative side)
        for lim, t in ((10, 0.002), (20, 0.005), (50, 0.01), (100, 0.02), (200, 0.05), (500, 0.1), (1000, 0.2), (2000, 0.5), (5000, 1), (10000, 2)):
            if p < lim: return t
        return 5
    return 0.01
def side_cost(mkt, shares, px, side, spread_bp=None):
    """IBKR cost (local ccy) of one order: commission + third-party/regulatory + stamp + sell tax. side = buy|sell (short entry = sell).
    LSE adds: stamp duty 0.5% on buys, PTM levy on orders > GBP10k, half the quoted spread (spread_bp full spread; default = conservative FTSE250 figure)."""
    import math
    M = MARKETS[mkt]; v = abs(shares) * px
    if not v: return 0.0
    c = max(M["comm"]["pct"] * v, M["comm"]["min"]) + sum(M["third"].values()) * v
    if M.get("stamp"): c += math.ceil(M["stamp"] * v)
    if side == "sell": c += M["sell_tax"] * v
    if side == "buy" and M.get("buy_stamp"): c += M["buy_stamp"] * v
    if M.get("ptm") and v > M["ptm"]["over"]: c += M["ptm"]["levy"]
    if M.get("spread_bp"): c += v * float(spread_bp or M["spread_bp"]["default"]) / 2 / 1e4
    return round(c, 4)
