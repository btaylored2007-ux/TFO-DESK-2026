"""Rate limit for the fill-at-entry auto-clear.

Three clears in a window are the budget. The next one halts auto-clear until
Brett resumes it. Re-ranking the queue is not a clear and is not counted.
"""


def judge(history, now_ts, window=1800, max_n=3, halted_until=None):
    """history: list of unix times. Returns (allow, new_halted_until, recent_count)."""
    if halted_until is not None and now_ts < halted_until:
        return False, halted_until, sum(1 for t in history if now_ts - t < window)
    recent = [t for t in history if now_ts - t < window]
    if len(recent) >= max_n:
        return False, now_ts + window, len(recent)
    return True, None, len(recent)
