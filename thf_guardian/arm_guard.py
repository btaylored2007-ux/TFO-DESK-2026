"""Filter installed in front of desk.enqueue. AI arms for a degraded seat are dropped.
Code-graded and funder-takeover arms are decided elsewhere and are not this filter.
"""
from thf_guardian.ai_mode import filter_ai_arms, load_modes
from thf_guardian.util import utcnow


def install(module, modes_path=None):
    orig = module.enqueue
    import functools

    @functools.wraps(orig)   # fixer 2026-11-02: keep desk.enqueue signature/source (ack_required) visible through the filter
    def enqueue(recs, *args, **kwargs):
        modes = load_modes(modes_path, utcnow()) if modes_path else {}
        if modes_path is None:
            import os
            from thf_guardian.config import Config
            modes = load_modes(Config().ai_mode, utcnow())
        kept, blocked = filter_ai_arms(recs, modes)
        if blocked:
            try:
                module.log("thf_guardian blocked AI arms: %s" % blocked)
            except Exception:
                pass
        return orig(kept, *args, **kwargs)

    module.enqueue = enqueue
    return enqueue
