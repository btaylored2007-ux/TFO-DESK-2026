"""Per-seat full risk (PAPER). Brett 18:16/18:22/18:23 WITA 31 Oct across-board:
Victor banks / Nico tech_ai_infra / Nora essentials_utilities / Quinn news_filings / Iris healthcare = 1%;
Rex energy / Lena fx_commodities / Dex trend+price_action / Theo macro = 2%.
Grade-A size AND the CAP for any grade-B / sub-floor lift (never above this; floor above it -> pass).
Used by VD and THF (and any path through book.risk_plan)."""
SEAT_A_RISK_PCT = {"banks": 0.01, "tech_ai_infra": 0.01, "essentials_utilities": 0.01, "news_filings": 0.01, "healthcare": 0.01,
                   "energy": 0.02, "fx_commodities": 0.02, "trend": 0.02, "price_action": 0.02, "macro": 0.02}
def seat_a_risk(seat):
    return SEAT_A_RISK_PCT.get(str(seat or ""))
