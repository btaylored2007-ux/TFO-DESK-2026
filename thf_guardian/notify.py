"""Alerts. The dashboard queue is always written. Email and Telegram stay off
unless the operator points an env var at a hook executable. No tokens in code.
"""
import os, subprocess

from thf_guardian.util import append_jsonl, wita_str


def emit(cfg, alert, runner=None):
    row = dict(alert, paper_only=True, wita=alert.get("wita") or "")
    append_jsonl(cfg.alerts, row)
    if row.get("level") in ("alert", "escalate", "halt"):
        append_jsonl(cfg.incidents, row)
    runner = runner or subprocess.run
    for flag, hook_env in (("THF_GUARDIAN_EMAIL", "THF_GUARDIAN_EMAIL_HOOK"),
                           ("THF_GUARDIAN_TELEGRAM", "THF_GUARDIAN_TELEGRAM_HOOK")):
        if os.environ.get(flag) != "1":
            continue
        hook = os.environ.get(hook_env) or ""
        if not hook:
            append_jsonl(cfg.incidents, dict(level="alert", kind="notifier", detail="%s=1 but %s is empty" % (flag, hook_env)))
            continue
        try:
            runner([hook], input=__import__("json").dumps(row), text=True, timeout=10, capture_output=True)
        except Exception as exc:
            append_jsonl(cfg.incidents, dict(level="alert", kind="notifier", detail="%s hook failed %s" % (flag, type(exc).__name__)))
    return row
