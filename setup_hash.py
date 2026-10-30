"""Review fix #2 (hunt_review_20261027, Brett-approved): a clearance is bound to the exact setup it reviewed.
setup_hash(row) = short sha256 over direction + trigger/max_fill/stop/t1/t2 (rounded to 4 dp). Share count is NOT hashed
(risk review may resize shares). Any level refresh -> different hash -> the old CLEARED verdict no longer applies."""
import hashlib, json
KEYS = ("trigger", "max_fill", "stop", "t1", "t2")
def _r(v):
    try: return None if v is None else round(float(v), 4)
    except (TypeError, ValueError): return str(v)
def setup_hash(row):
    if not row: return None
    d = [str(row.get("direction") or "short").lower()] + [_r(row.get(k)) for k in KEYS]
    return hashlib.sha256(json.dumps(d).encode()).hexdigest()[:16]
