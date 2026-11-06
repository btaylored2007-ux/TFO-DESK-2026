"""Matched shadow comparison for Claude, ChatGPT, Grok and DeepSeek.

Shadow text is scored and logged. It is not parsed into orders. Cost is
recorded only when the transport reports it; otherwise the field is unknown.
A recommendation requires 20 matched rows in a cell. This module does not
start that window by itself.
"""
import hashlib
import json
import os
import re

VERSION = "shadow_bench_20261106"
PROVIDERS = ("claude", "chatgpt", "grok", "deepseek_api")
MIN_N = 20
REVIEW_AT = "2026-11-13T21:00:00+08:00"
SECRET = re.compile(r"(sk-[A-Za-z0-9_\-]{8,}|api[_-]?key\s*[:=]\s*\S+|BEGIN [A-Z ]*PRIVATE KEY)", re.I)


def _hash(text):
    return hashlib.sha256(str(text).encode()).hexdigest()


def redact(text):
    return SECRET.sub("[redacted]", str(text or ""))


def provider_order(task_id, providers=PROVIDERS):
    shift = int(_hash(task_id)[:8], 16) % len(providers)
    return providers[shift:] + providers[:shift]


def score_output(prompt, output, source_pack=None):
    """Blind: no provider name is an argument."""
    text = str(output or "")
    wants_json = "Schema:" in str(prompt) or "JSON" in str(prompt)
    parse_ok = False
    if wants_json:
        try:
            parse_ok = isinstance(json.loads(text), dict)
        except (ValueError, TypeError):
            parse_ok = False
    else:
        parse_ok = bool(text.strip())
    return dict(parse_ok=parse_ok, risk_compliance=parse_ok and "place the order" not in text.lower(),
                correctness="unknown" if not source_pack else "not_checked_against_pack",
                source_validation="unknown")


def run_matched(task, transport):
    """transport(provider, prompt, timeout) -> dict(ok, text, model, usage, error, fallback)."""
    prompt = task.get("prompt")
    asof = task.get("data_asof")
    if not task.get("seat") or not prompt or not asof:
        return dict(eligible=False, reason="missing_seat_prompt_or_data_asof", rows=[])
    prompt_hash = _hash(prompt)
    rows = []
    order = provider_order(str(task.get("task_id") or prompt_hash))
    for name in order:
        started = os.times()
        try:
            result = transport(name, prompt, task.get("timeout_s") or 20) or {}
        except Exception as error:
            result = dict(ok=False, error=type(error).__name__ + ":" + str(error)[:80])
        usage = result.get("usage") if isinstance(result.get("usage"), dict) else None
        tokens = usage.get("total_tokens") if usage and isinstance(usage.get("total_tokens"), int) else "unknown"
        cost = usage.get("cost_usd") if usage and isinstance(usage.get("cost_usd"), (int, float)) else "unknown"
        text = str(result.get("text") or "")
        rows.append(dict(
            version=VERSION, task_id=task.get("task_id"), seat=task.get("seat"), role=task.get("role"),
            task_class=task.get("task_class") or "other", provider_requested=name,
            provider_actual=result.get("model") or ("unknown" if result.get("ok") else None),
            fallback=bool(result.get("fallback")), prompt_hash=prompt_hash, data_asof=asof,
            input_hash=prompt_hash, output_hash=_hash(text) if text else None,
            preview=redact(text)[:180], score=score_output(prompt, text, task.get("source_pack")),
            ok=bool(result.get("ok")), error=result.get("error"), tokens=tokens, cost_usd=cost,
            latency_ms=result.get("latency_ms", "unknown"), first_token_ms=result.get("first_token_ms", "unknown")))
        del started
    return dict(eligible=True, reason="matched", prompt_hash=prompt_hash, data_asof=asof, rows=rows)


def recommend(rows):
    """Per role and task class. Small samples stay a gap. Paper P&L is not an input."""
    cells = {}
    for row in rows:
        key = (row.get("role"), row.get("task_class"), row.get("provider_requested"))
        cells.setdefault(key, []).append(row)
    advice = []
    for (role, task_class, provider), group in sorted(cells.items(), key=lambda item: str(item[0])):
        n = len(group)
        advice.append(dict(role=role, task_class=task_class, provider=provider, n=n,
                           recommendation="gap" if n < MIN_N else "enough_to_compare",
                           uncertainty="sample below 20; no switch" if n < MIN_N else "disclose interval before any switch"))
    return dict(version=VERSION, review_at=REVIEW_AT, global_winner=None, cells=advice,
                note="Seven-day paper P&L is not evidence of edge.")


def note_production(task, path):
    """Record the answer the desk already received. Does not call a provider."""
    text = str(task.get("text") or "")
    row = dict(version=VERSION, task_id=task.get("task_id"), seat=task.get("seat"), role=task.get("role"),
               task_class=task.get("task_class") or "other", provider_requested=task.get("provider") or "unknown",
               provider_actual=task.get("provider") or "unknown", fallback=False,
               prompt_hash=_hash(task.get("prompt") or ""), data_asof=task.get("data_asof"),
               output_hash=_hash(text) if text else None, preview=redact(text)[:180],
               score=score_output(task.get("prompt") or "", text, None), ok=bool(text),
               tokens="unknown", cost_usd="unknown", latency_ms="unknown", first_token_ms="unknown",
               shadow_calls=0)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, default=str) + "\n")
        handle.flush()
        os.fsync(handle.fileno())
    return row


def window_status(rows):
    eligible = [row for row in rows if row.get("prompt_hash") and row.get("data_asof")]
    if not eligible:
        return dict(started=False, first_row_utc=None, n=0, note="window has not started")
    return dict(started=True, n=len(eligible), note="clock starts at the first stored matched row, not before")
