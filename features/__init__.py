"""Advisory per-ticker feature store. PAPER ONLY.

Writes state/features/<ticker>.json and state/regime.json. Every field carries
an as_of time and a fresh or stale flag. Missing observations stay missing.
This package does not arm trades, change gates, or touch rails.
"""
PAPER_ONLY = True
ADVISORY = True
GATES = False
