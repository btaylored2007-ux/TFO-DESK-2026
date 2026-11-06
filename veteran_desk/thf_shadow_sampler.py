"""Isolated matched shadow calls for all 18 seats.

Production inference stays in llm_backend.grok_call. This module calls the four
existing provider clients with the same prompt and writes only its own logs.
It does not enqueue orders or parse them into the book.

Entrypoint: python -m veteran_desk.thf_shadow_sampler once
Timer: tfo-thf-shadow.timer (30 min). Evidence: state/thf/shadow_samples.jsonl
and state/thf/shadow_sampler_status.json. The clock starts on the first task
that has a real answer from all four providers.
"""
import datetime as dt
import json
import os
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeout

from veteran_desk import lesson_trace as LT
from veteran_desk import thf_shadow_bench as BENCH

VERSION = "shadow_sampler_20261106"
PROVIDERS = BENCH.PROVIDERS
ADMIN_TASKS = {
    "head_of_desk": "desk_oversight",
    "head_trader": "challenge_review",
    "cfo": "cost_review",
    "software_engineer": "reliability_review",
    "fixer": "incident_review",
    "blind_challenger": "disconfirm",
    "devils_advocate": "disconfirm",
}


def _now():
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def _append(path, row):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, default=str) + "\n")
        handle.flush()
        os.fsync(handle.fileno())


def jobs_path(root):
    return os.path.join(root, "state", "thf", "shadow_jobs.jsonl")


def samples_path(root):
    return os.path.join(root, "state", "thf", "shadow_samples.jsonl")


def config_path(root):
    return os.path.join(root, "state", "thf", "shadow_sampler.json")


def load_config(root):
    try:
        data = json.load(open(config_path(root), encoding="utf-8"))
    except (OSError, ValueError):
        return dict(enabled=False, reason="missing_or_unreadable_config")
    if not isinstance(data, dict):
        return dict(enabled=False, reason="config_not_object")
    data.setdefault("enabled", False)
    data.setdefault("provider_timeout_s", 90)
    data.setdefault("max_jobs_per_run", 2)
    data.setdefault("paper_only", True)
    return data


def classify_error(error):
    text = str(error).lower()
    if any(word in text for word in ("quota", "rate limit", "cooling", "usage limit", "too many")):
        return "quota"
    if any(word in text for word in ("timeout", "deadline", "no time left", "timed out")):
        return "timeout"
    if any(word in text for word in ("off", "unavailable", "not verified", "disabled", "restricted",
                                     "permission", "cli unavailable", "bridge", "not logged", "auth")):
        return "unavailable"
    return "error"


def call_claude(prompt, seat, timeout):
    from veteran_desk import claude_brain as client
    text, label = client.ask(prompt, seat=seat, timeout=timeout, kind="review", tools=False, prio="low")
    return text, label


def call_chatgpt(prompt, seat, timeout):
    from veteran_desk import chatgpt_bridge as client
    text = client.ask(prompt, timeout=timeout, skip_if_busy=True, seat=seat, workload="other")
    return text, getattr(text, "model", None) or "chatgpt"


def call_grok(prompt, seat, timeout):
    from veteran_desk import thf_grok as client
    text = client.ask(prompt, seat=seat, timeout=timeout)
    return text, getattr(text, "model", None) or "grok"


def call_deepseek(prompt, seat, timeout):
    from veteran_desk import deepseek_bridge as client
    text = client.ask(prompt, seat=seat, timeout=timeout, workload="other")
    return text, getattr(text, "model", None) or "deepseek_api"


CALLS = {
    "claude": call_claude,
    "chatgpt": call_chatgpt,
    "grok": call_grok,
    "deepseek_api": call_deepseek,
}


def call_provider(name, prompt, seat, timeout):
    started = time.time()
    try:
        text, label = CALLS[name](prompt, seat, timeout)
    except Exception as error:
        return dict(ok=False, availability=classify_error(error), text="", error=type(error).__name__ + ": " + str(error)[:180],
                    model=None, scored=False, latency_ms=int((time.time() - started) * 1000))
    body = str(text or "").strip()
    if not body:
        return dict(ok=False, availability="unavailable", text="", error="empty answer", model=label,
                    scored=False, latency_ms=int((time.time() - started) * 1000))
    return dict(ok=True, availability="answered", text=body, error=None, model=label, scored=True,
                latency_ms=int((time.time() - started) * 1000))


def shadow_prompt(task_class, seat, role, data_asof, body):
    banner = (
        "SHADOW EVALUATION ONLY. Paper desk. Do not place an order, send a message, or change a rule. "
        "Same facts for every provider. data_asof=%s seat=%s role=%s task=%s.\n" % (data_asof, seat, role, task_class)
    )
    return banner + str(body or "")


def task_body(seat, task_class):
    if task_class == "research_review":
        return (
            "Return one JSON object. A bare stand_aside is not finished research. "
            "An empty answer is valid and is not a trade. Do not invent prices. "
            "Schema: {\"action\":\"stand_aside\"|\"propose\",\"research_candidate\":null}"
        )
    return (
        "Review this %s task for seat %s. Name one risk and one missing fact. "
        "Do not propose an order. Reply in under 120 words." % (task_class, seat)
    )


def enqueue(root, job):
    row = dict(job, event="queued", utc=_now(), version=VERSION)
    _append(jobs_path(root), row)
    return row


def enqueue_production_prompt(root, seat, prompt, data_asof, task_id, role="trader", task_class="production_proposal"):
    """Queue the desk prompt for the four-provider sampler. Does not call a provider."""
    if not root:
        return None
    return enqueue(root, dict(
        task_id=task_id, seat=seat, role=role, task_class=task_class,
        data_asof=data_asof, prompt=prompt, source="production_prompt"))


def _events(root):
    rows = []
    try:
        lines = open(jobs_path(root), encoding="utf-8")
    except OSError:
        return rows
    for line in lines:
        try:
            item = json.loads(line)
        except ValueError:
            continue
        if isinstance(item, dict):
            rows.append(item)
    return rows


def pending_jobs(root):
    latest = {}
    order = []
    for row in _events(root):
        task_id = row.get("task_id")
        if not task_id:
            continue
        if task_id not in latest:
            order.append(task_id)
        latest[task_id] = row
    out = []
    for task_id in order:
        row = latest[task_id]
        if row.get("event") != "finished":
            out.append(row)
    return out


def _parse_utc(value):
    try:
        seen = dt.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if seen.tzinfo is None:
        seen = seen.replace(tzinfo=dt.timezone.utc)
    return seen


def _latest_by_seat(root):
    latest = {}
    for row in _events(root):
        seat = row.get("seat")
        if seat:
            latest[seat] = row
    return latest


def ensure_coverage(root, now=None, resample_after_s=6 * 3600):
    """Queue every seat, then again after resample_after_s once the previous job finished."""
    now_text = now or _now()
    now_dt = _parse_utc(now_text) or dt.datetime.now(dt.timezone.utc)
    latest = _latest_by_seat(root)
    added = []
    for seat in LT.SEATS:
        row = latest.get(seat)
        if row and row.get("event") != "finished":
            continue
        if row and row.get("event") == "finished":
            then = _parse_utc(row.get("utc"))
            if then is not None and (now_dt - then).total_seconds() < float(resample_after_s):
                continue
        if seat in LT.TRADERS:
            role, task_class = "trader", "research_review"
        else:
            role, task_class = "admin", ADMIN_TASKS[seat]
        body = task_body(seat, task_class)
        added.append(enqueue(root, dict(
            task_id="coverage-%s-%s" % (seat, now_text), seat=seat, role=role,
            task_class=task_class, data_asof=now_text, prompt=shadow_prompt(task_class, seat, role, now_text, body),
            source="coverage")))
    return added


def run_job(job, timeout):
    prompt = job.get("prompt") or ""
    seat = job.get("seat")
    prompt_hash = BENCH._hash(prompt)
    rows = []

    def one(name):
        result = call_provider(name, prompt, seat, timeout)
        text = result.get("text") or ""
        score = BENCH.score_output(prompt, text, None) if result.get("scored") else None
        return dict(
            version=VERSION, utc=_now(), task_id=job.get("task_id"), seat=seat, role=job.get("role"),
            task_class=job.get("task_class"), provider_requested=name, provider_actual=result.get("model"),
            availability=result.get("availability"), ok=bool(result.get("ok")), error=result.get("error"),
            scored=bool(result.get("scored")), prompt_hash=prompt_hash, data_asof=job.get("data_asof"),
            output_hash=BENCH._hash(text) if text else None, preview=BENCH.redact(text)[:180],
            score=score, tokens="unknown", cost_usd="unknown", latency_ms=result.get("latency_ms"),
            shadow_calls=1, kind="shadow_evaluation", paper_only=True)

    with ThreadPoolExecutor(max_workers=len(PROVIDERS)) as pool:
        futures = [pool.submit(one, name) for name in BENCH.provider_order(str(job.get("task_id") or prompt_hash))]
        for future in futures:
            try:
                rows.append(future.result(timeout=timeout + 5))
            except FuturesTimeout:
                rows.append(dict(version=VERSION, task_id=job.get("task_id"), seat=seat, availability="timeout",
                                 ok=False, scored=False, error="shadow wait exceeded", shadow_calls=1,
                                 kind="shadow_evaluation", prompt_hash=prompt_hash, data_asof=job.get("data_asof")))
    return rows


def once(root, now=None):
    config = load_config(root)
    status = dict(version=VERSION, utc=_now(), enabled=bool(config.get("enabled")), paper_only=True,
                  providers=list(PROVIDERS), called=False, jobs=0)
    if not config.get("enabled"):
        status["availability"] = "pending_configuration" if config.get("reason") else "disabled"
        _write_status(root, status)
        return status
    ensure_coverage(root, now=now, resample_after_s=int(config.get("resample_after_s") or 6 * 3600))
    timeout = int(config.get("provider_timeout_s") or 90)
    ran = 0
    for job in pending_jobs(root)[:int(config.get("max_jobs_per_run") or 1)]:
        _append(jobs_path(root), dict(event="started", task_id=job.get("task_id"), utc=_now()))
        rows = run_job(job, timeout)
        for row in rows:
            _append(samples_path(root), row)
        _append(jobs_path(root), dict(event="finished", task_id=job.get("task_id"), utc=_now(),
                                     providers=[row.get("provider_requested") for row in rows]))
        ran += 1
    status.update(called=True, jobs=ran, availability="ran")
    samples = []
    try:
        for line in open(samples_path(root), encoding="utf-8"):
            samples.append(json.loads(line))
    except (OSError, ValueError):
        samples = []
    window = BENCH.window_status([row for row in samples if row.get("kind") == "shadow_evaluation"])
    status["window"] = window
    status["seats_expected"] = len(LT.SEATS)
    status["pending_jobs"] = len(pending_jobs(root))
    status["comparison"] = "paired rows in shadow_samples.jsonl only; a production log with shadow_calls 0 is not the trial"
    status["recommendation"] = BENCH.recommend([row for row in samples if row.get("scored") and row.get("kind") == "shadow_evaluation"])
    status["recommendation"]["global_winner"] = None
    _write_status(root, status)
    return status


def _write_status(root, status):
    path = os.path.join(root, "state", "thf", "shadow_sampler_status.json")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    temporary = path + ".tmp"
    with open(temporary, "w", encoding="utf-8") as handle:
        json.dump(status, handle, default=str)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


def main(argv=None):
    import sys
    args = list(sys.argv[1:] if argv is None else argv)
    root = os.environ.get("THF_SHADOW_ROOT") or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if args[:1] != ["once"]:
        print("usage: python -m veteran_desk.thf_shadow_sampler once")
        return 2
    status = once(root)
    print(json.dumps(dict(enabled=status.get("enabled"), jobs=status.get("jobs"),
                          availability=status.get("availability"),
                          window_started=(status.get("window") or {}).get("started")), default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
