"""Empty paper ledgers for tests. Eleven seat ids, no positions, no owner book."""
from veteran_desk import book as BK


def skeleton():
    seats = {}
    for seat in BK.TRADERS:
        row = dict(BK._seat_ledger())
        row["equity_aud"] = BK.SEAT_SEED
        row["day_start_equity_aud"] = BK.SEAT_SEED
        seats[seat] = row
    return dict(
        book="synthetic_isolated", paper_only=True,
        label="synthetic empty paper book; no owner positions",
        seed_aud=BK.SEED_AUD, start=round(BK.SEED_AUD * 0.65, 2), audusd_at_seed=0.65,
        seats=seats, holdings={}, capital_events=[],
        hedge={"disabled": True}, kill_switch={"state": "OK"},
        peak_equity_aud=None, day_start_equity_aud=BK.SEED_AUD,
    )
