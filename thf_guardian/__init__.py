"""THF guardian (PAPER only). Deterministic watchdog, feed doctor, and funder message box.

Trading decisions in this package do not call an LLM. The message box and
push-traders hunt use the droplet Grok/Claude bridge only while that seat's
brain is up; a down brain falls back to the last good parameters.
"""
PAPER_ONLY = True
__version__ = "2026.11.02"

def assert_paper():
    import os
    if os.environ.get("THF_GUARDIAN_PAPER", "1") != "1":
        raise RuntimeError("thf_guardian refuses to run with THF_GUARDIAN_PAPER disabled")
    if os.environ.get("THF_LIVE_ORDERS") == "1":
        raise RuntimeError("thf_guardian is paper-only and will not place live orders")
