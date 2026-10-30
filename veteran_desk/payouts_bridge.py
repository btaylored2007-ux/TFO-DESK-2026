"""Map payouts.accrue (desk-level, built by Claude) onto the 9 per-seat books: each proposer's commission is an expense in ITS OWN book;
challenger/advocate veto credits are an oversight expense split equally across the 9 books; forfeits credited back the same way."""
from decimal import Decimal
from veteran_desk import payouts as PY
def book_commissions(D, **kw):
    S = D["seats"]; n = len(S)
    view = dict(closed=[dict(c, seat=k) for k, L in S.items() for c in L["closed"]], cash_aud=0.0, commission_expense_aud=0.0, commission_forfeit_credit_aud=0.0)
    PY.accrue(view, **kw)
    out = PY._load_json(kw.get("out_path") or PY.DEFAULT_OUT_PATH)
    seats = (out or {}).get("seats") or {}
    D_ = PY._d; C_ = PY._c   # Decimal cents throughout (rounding-loss fix 2026-10-28)
    over = sum(D_((seats.get(k) or {}).get("accrued_commission_aud")) for k in PY.CHALLENGER_SEATS)
    overf = sum(D_((seats.get(k) or {}).get("forfeited_aud")) for k in PY.CHALLENGER_SEATS)
    keys = sorted(S); share, sharef = C_(over / n) if n else Decimal(0), C_(overf / n) if n else Decimal(0)
    rem, remf = C_(over) - share * n, C_(overf) - sharef * n   # leftover cent(s) go to the first seat so the split sums exactly
    for i, k in enumerate(keys):
        L = S[k]
        own = D_((seats.get(k) or {}).get("accrued_commission_aud")); ownf = D_((seats.get(k) or {}).get("forfeited_aud"))
        want = C_(own + share + (rem if i == 0 else 0)); wantf = C_(ownf + sharef + (remf if i == 0 else 0))
        d = want - C_(L.get("commission_expense_aud") or 0); df = wantf - C_(L.get("commission_forfeit_credit_aud") or 0)
        L["cash_aud"] = float(C_(D_(L.get("cash_aud")) - d + df)); L["commission_expense_aud"] = float(want); L["commission_forfeit_credit_aud"] = float(wantf)
