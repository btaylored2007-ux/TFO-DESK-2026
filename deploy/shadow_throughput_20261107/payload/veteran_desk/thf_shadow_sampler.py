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
import hashlib
import json
import os
import re
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
    # A transport ValueError is an adapter failure. It is not an account quota.
    if "transport" in text or ("valueerror" in text and "rate limit" not in text):
        return "error"
    if "private key" in text or "status unreadable" in text:
        return "unavailable"
    if any(word in text for word in ("timeout", "deadline", "no time left", "timed out")):
        return "timeout"
    if "busy" in text:
        return "busy"
    if any(word in text for word in ("quota", "rate limit", "usage limit", "too many", "insufficient balance")):
        return "quota"
    if "cooling" in text:
        return "quota"
    if any(word in text for word in ("off", "unavailable", "not verified", "disabled", "restricted",
                                     "permission", "cli unavailable", "bridge", "not logged", "auth",
                                     "unreachable", "connection refused", "restart", "busy")):
        return "unavailable"
    return "error"


def chatgpt_budget(config):
    """The bridge default is 280s. Extra High has taken about 169s. A 90s cap records a timeout."""
    base = int(config.get("provider_timeout_s") or 90)
    if config.get("chatgpt_timeout_s") is not None:
        budget = int(config.get("chatgpt_timeout_s") or base)
    elif base >= 60:
        budget = max(base, 280)
    else:
        budget = base
    return min(300, max(1, budget))


def provider_budgets(config):
    base = max(1, int(config.get("provider_timeout_s") or 90))
    return {name: chatgpt_budget(config) if name == "chatgpt" else base for name in PROVIDERS}


def shadow_request_id(provider, seat, prompt):
    digest = hashlib.sha256(("%s\n%s\n%s" % (provider, seat or "", prompt or "")).encode()).hexdigest()[:32]
    return ("shadow-%s-%s" % (str(provider).replace("_", "-"), digest))[:128]


def call_claude(prompt, seat, timeout):
    from veteran_desk import claude_brain as client
    text, label = client.ask(prompt, seat=seat, timeout=timeout, kind="review", tools=False, prio="low")
    return text, label


def call_chatgpt(prompt, seat, timeout):
    from veteran_desk import chatgpt_bridge as client
    live = client.health()
    # One decision. A restart or an in-flight browser job is not a second POST.
    if not isinstance(live, dict) or not live.get("state"):
        raise RuntimeError("chatgpt bridge unreachable")
    if str(live.get("state")) in ("starting", "restarting"):
        raise RuntimeError("chatgpt bridge restarting")
    if live.get("busy") or live.get("inflight"):
        raise RuntimeError("chatgpt busy")
    text = client.ask(prompt, timeout=timeout, skip_if_busy=True, seat=seat, workload="other",
                      request_id=shadow_request_id("chatgpt", seat, prompt))
    return text, getattr(text, "model", None) or "chatgpt"


def call_grok(prompt, seat, timeout):
    from veteran_desk import ai_cooldown as CD
    from veteran_desk import thf_grok as client
    if CD.cooling("grok"):
        row = CD._load().get("grok") or {}
        raise RuntimeError("grok deferred; local %s cooldown until %s" % (row.get("kind") or "cooldown", row.get("until_wita") or "later"))
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
    if not isinstance(prompt, str) or not prompt.strip():
        return dict(ok=False, availability="invalid", text="", error="empty prompt rejected before provider call",
                    model=None, scored=False, latency_ms=0)
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


def usable_prompt(job):
    text = job.get("prompt") if isinstance(job, dict) else job
    return text.strip() if isinstance(text, str) and text.strip() else None


def enqueue(root, job):
    if usable_prompt(job) is None:
        return dict(event="refused", reason="empty_prompt", task_id=(job or {}).get("task_id"), queued=False)
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
    queued = {}
    order = []
    for row in _events(root):
        task_id = row.get("task_id")
        if not task_id:
            continue
        if task_id not in latest:
            order.append(task_id)
        if row.get("event") == "queued":
            queued[task_id] = row
        latest[task_id] = row
    out = []
    for task_id in order:
        row = latest[task_id]
        if row.get("event") in ("finished", "incomplete", "invalid"):
            continue
        # A started/deferred status row does not repeat the prompt. Keep the queued text.
        job = dict(queued.get(task_id) or {})
        queued_utc = job.get("utc")
        job.update(row)
        job["queued_utc"] = queued_utc or job.get("utc")
        out.append(job)
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


def is_coverage(job):
    return job.get("source") == "coverage" or str(job.get("task_id") or "").startswith("coverage-")


def _coverage_state(events, seat):
    """Latest coverage job for a seat. A production queued row is not coverage."""
    latest_for_task = {}
    for row in events:
        task_id = row.get("task_id")
        if task_id:
            latest_for_task[task_id] = row
    queued = None
    seated_terminal = None
    for row in events:
        if row.get("seat") != seat or row.get("source") == "production_prompt":
            continue
        if row.get("event") in ("finished", "incomplete", "invalid"):
            seated_terminal = row
        if row.get("event") == "queued" and (row.get("source") == "coverage" or str(row.get("task_id") or "").startswith("coverage-")):
            queued = row
    if queued:
        return latest_for_task.get(queued.get("task_id"), queued)
    return seated_terminal


def ensure_coverage(root, now=None, resample_after_s=6 * 3600):
    """Queue every seat, then again after resample_after_s once that coverage job closed.

    A production_prompt row does not block this. The previous coverage task stays closed.
    """
    now_text = now or _now()
    now_dt = _parse_utc(now_text) or dt.datetime.now(dt.timezone.utc)
    events = _events(root)
    added = []
    for seat in LT.SEATS:
        row = _coverage_state(events, seat)
        if row and row.get("event") not in ("finished", "incomplete", "invalid"):
            continue
        if row and row.get("event") in ("finished", "incomplete", "invalid"):
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


TRANSIENT = ("busy", "cooldown", "timed out", "timeout", "unreachable", "restart", "deferred")
DEFER_LIMIT = 3


def budget_spent(row):
    """A timeout that used the allowance is a finished failure. It is not a reason to pay for the same call again."""
    text = ((row.get("error") or "") + " " + (row.get("availability") or "")).lower()
    if "shadow wait exceeded" in text:
        return True
    if "timeout" not in text:
        return False
    latency = row.get("latency_ms")
    budget = row.get("budget_s")
    if isinstance(budget, (int, float)) and budget > 0 and isinstance(latency, (int, float)):
        return latency >= float(budget) * 800
    match = re.search(r"timeout after (\d+)s", text)
    if match and "claude" in text and int(match.group(1)) >= 80:
        return True
    if "grok timeout" in text and isinstance(latency, (int, float)) and latency >= 80000:
        return True
    return False


def fast_defer(row):
    """An immediate busy or unreachable result did not start a model."""
    if row.get("ok"):
        return False
    text = ((row.get("error") or "") + " " + (row.get("availability") or "")).lower()
    if "timeout" in text or "shadow wait" in text:
        return False
    if not any(word in text for word in ("busy", "restart", "unreachable")):
        return False
    latency = row.get("latency_ms")
    return not isinstance(latency, (int, float)) or latency < 1000


def is_transient(row):
    if row.get("availability") == "quota" or budget_spent(row):
        return False
    text = ((row.get("error") or "") + " " + (row.get("availability") or "")).lower()
    if "usage limit" in text or "sign-in" in text:
        return False
    return any(word in text for word in TRANSIENT)


def terminal_providers(samples, task_id):
    latest = {}
    for row in samples:
        name = row.get("provider_requested")
        if row.get("task_id") == task_id and name:
            latest[name] = row
    return {name for name, row in latest.items() if not is_transient(row)}


def _read_samples(root):
    rows = []
    try:
        lines = open(samples_path(root), encoding="utf-8")
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


def run_job(job, timeout, budgets=None, only=None):
    prompt = usable_prompt(job)
    if prompt is None:
        return []
    seat = job.get("seat")
    prompt_hash = BENCH._hash(prompt)
    budgets = budgets or {name: timeout for name in PROVIDERS}
    rows = []

    def one(name):
        result = call_provider(name, prompt, seat, budgets.get(name, timeout))
        text = result.get("text") or ""
        score = BENCH.score_output(prompt, text, None) if result.get("scored") else None
        return dict(
            version=VERSION, utc=_now(), task_id=job.get("task_id"), seat=seat, role=job.get("role"),
            task_class=job.get("task_class"), provider_requested=name, provider_actual=result.get("model"),
            availability=result.get("availability"), ok=bool(result.get("ok")), error=result.get("error"),
            scored=bool(result.get("scored")), prompt_hash=prompt_hash, data_asof=job.get("data_asof"),
            output_hash=BENCH._hash(text) if text else None, preview=BENCH.redact(text)[:180],
            score=score, tokens="unknown", cost_usd="unknown", latency_ms=result.get("latency_ms"),
            budget_s=int(budgets.get(name, timeout)),
            shadow_calls=1, kind="shadow_evaluation", paper_only=True)

    names = [name for name in BENCH.provider_order(str(job.get("task_id") or prompt_hash)) if only is None or name in only]
    with ThreadPoolExecutor(max_workers=len(PROVIDERS)) as pool:
        futures = [pool.submit(one, name) for name in names]
        for name, future in zip(names, futures):
            try:
                rows.append(future.result(timeout=int(budgets.get(name, timeout)) + 15))
            except FuturesTimeout:
                rows.append(dict(version=VERSION, task_id=job.get("task_id"), seat=seat,
                                 provider_requested=name, availability="timeout",
                                 ok=False, scored=False, error="shadow wait exceeded", shadow_calls=1,
                                 latency_ms=int(budgets.get(name, timeout)) * 1000,
                                 budget_s=int(budgets.get(name, timeout)),
                                 kind="shadow_evaluation", prompt_hash=prompt_hash, data_asof=job.get("data_asof")))
    return rows


def _defer_count(events, task_id):
    """Busy defers wait for a lock. They do not close a comparison."""
    return sum(1 for row in events
               if row.get("task_id") == task_id and row.get("event") == "deferred" and row.get("reason") != "provider_busy")


def _age_s(job, now_dt):
    queued = _parse_utc(job.get("utc"))
    if queued is None or now_dt is None:
        return None
    return (now_dt - queued).total_seconds()


def production_callable(job, now_dt, window_s):
    """A production prompt is one current comparison, not a backlog of old market decisions."""
    if job.get("source") != "production_prompt":
        return False
    age = _age_s(dict(job, utc=job.get("queued_utc") or job.get("utc")), now_dt)
    return age is not None and 0 <= age <= float(window_s)


def _recent_provider_busy(job, samples, now_dt, backoff_s):
    missing = [name for name in PROVIDERS if name not in terminal_providers(samples, job.get("task_id"))]
    if not missing:
        return False
    latest = {}
    for row in samples:
        if row.get("task_id") == job.get("task_id") and row.get("provider_requested"):
            latest[row["provider_requested"]] = row
    if not all(name in latest and fast_defer(latest[name]) for name in missing):
        return False
    times = [_parse_utc(latest[name].get("utc")) for name in missing]
    times = [item for item in times if item is not None]
    if not times or now_dt is None:
        return False
    return (now_dt - max(times)).total_seconds() < float(backoff_s)


def select_jobs(jobs, cap, now_dt, samples, window_s, backoff_s):
    """Coverage first. At most one fresh production row fills a spare slot. Stale production is not called."""
    coverage = [job for job in jobs if is_coverage(job)]
    ready = [job for job in coverage if not _recent_provider_busy(job, samples, now_dt, backoff_s)]
    held = [job for job in coverage if job not in ready]
    other = [job for job in jobs if not is_coverage(job) and job.get("source") != "production_prompt"]
    chosen = []
    for job in ready + held + other:
        if len(chosen) >= cap:
            break
        chosen.append(job)
    if len(chosen) < cap:
        for job in jobs:
            if production_callable(job, now_dt, window_s):
                chosen.append(job)
                break
    return chosen


def refuses_root():
    return os.geteuid() == 0


WITA = dt.timezone(dt.timedelta(hours=8))


def review_due(started_utc):
    """The review is the next 08:00 WITA at or after seven days from the locked start."""
    started = dt.datetime.fromisoformat(str(started_utc).replace("Z", "+00:00"))
    if started.tzinfo is None:
        started = started.replace(tzinfo=dt.timezone.utc)
    instant = started.astimezone(WITA) + dt.timedelta(days=7)
    due = instant.replace(hour=8, minute=0, second=0, microsecond=0)
    if instant > due:
        due += dt.timedelta(days=1)
    return due


def lock_window(root, computed, now=None):
    """Keep the first paired time. A later run must not move that start forward or clear it."""
    path = os.path.join(root, "state", "thf", "shadow_window.json")
    stored = {}
    try:
        stored = json.load(open(path, encoding="utf-8"))
    except (OSError, ValueError):
        stored = {}
    started = stored.get("started_utc")
    fresh = computed.get("first_row_utc") if computed.get("started") else None
    if fresh and (not started or str(fresh) < str(started)):
        started = fresh
    if not started:
        body = dict(started=False, started_utc=None, seven_day="not_started", seven_day_ready=False,
                    review_due_wita=None, review_due_utc=None,
                    note="comparison clock has not started")
    else:
        due = review_due(started)
        current = now or dt.datetime.now(dt.timezone.utc)
        if current.tzinfo is None:
            current = current.replace(tzinfo=dt.timezone.utc)
        ready = current >= due.astimezone(dt.timezone.utc)
        body = dict(started=True, started_utc=started,
                    seven_day="review_due" if ready else "clock_started",
                    seven_day_ready=bool(ready),
                    review_due_wita=due.strftime("%Y-%m-%d %H:%M WITA"),
                    review_due_utc=due.astimezone(dt.timezone.utc).replace(microsecond=0).isoformat(),
                    note="The start is the first paired row. The 7-day review is a later time, not a claim that the review is ready.")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    temporary = path + ".tmp"
    with open(temporary, "w", encoding="utf-8") as handle:
        json.dump(body, handle, indent=1)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)
    window = dict(computed)
    window["started"] = body["started"]
    window["first_row_utc"] = started
    window["review_due_wita"] = body["review_due_wita"]
    window["seven_day_ready"] = body["seven_day_ready"]
    body["window"] = window
    return body


def once(root, now=None):
    config = load_config(root)
    status = dict(version=VERSION, utc=_now(), enabled=bool(config.get("enabled")), paper_only=True,
                  providers=list(PROVIDERS), called=False, jobs=0, seven_day="not_started")
    if refuses_root():
        # Do not create root-owned status files. The unit must be User=tfo.
        status.update(enabled=False, called=False, availability="refused_root",
                      error="shadow sampler refuses root; set User=tfo and HOME=/home/tfo")
        return status
    if not config.get("enabled"):
        status["availability"] = "pending_configuration" if config.get("reason") else "disabled"
        _write_status(root, status)
        return status
    window_s = int(config.get("resample_after_s") or 6 * 3600)
    ensure_coverage(root, now=now, resample_after_s=window_s)
    budgets = provider_budgets(config)
    timeout = int(config.get("provider_timeout_s") or 90)
    ran = 0
    invalid = 0
    closed_limit = 0
    fast = 0
    coverage_selected = 0
    production_sampled = 0
    samples = _read_samples(root)
    cap = max(1, int(config.get("max_jobs_per_run") or 1))
    now_dt = _parse_utc(now) or dt.datetime.now(dt.timezone.utc)
    backoff_s = int(config.get("busy_backoff_s") or 1800)
    open_jobs = pending_jobs(root)
    for job in select_jobs(open_jobs, cap, now_dt, samples, window_s, backoff_s):
        task_id = job.get("task_id")
        if usable_prompt(job) is None:
            _append(jobs_path(root), dict(event="invalid", reason="empty_prompt", task_id=task_id, utc=_now(), providers=[]))
            invalid += 1
            continue
        prior_defers = _defer_count(_events(root), task_id)
        if prior_defers >= DEFER_LIMIT:
            _append(jobs_path(root), dict(event="incomplete", reason="defer_limit", task_id=task_id, utc=_now(), providers=[]))
            closed_limit += 1
            continue
        missing = [name for name in PROVIDERS if name not in terminal_providers(samples, task_id)]
        _append(jobs_path(root), dict(event="started", task_id=task_id, utc=_now()))
        rows = run_job(job, timeout, budgets, only=missing) if missing else []
        for row in rows:
            _append(samples_path(root), row)
        samples.extend(rows)
        done = terminal_providers(samples, task_id)
        deferred = [name for name in PROVIDERS if name not in done]
        new_by = {row.get("provider_requested"): row for row in rows}
        busy_only = bool(deferred) and all(fast_defer(new_by.get(name) or {}) for name in deferred)
        if not deferred:
            event = "finished"
            reason = None
        elif busy_only:
            event = "deferred"
            reason = "provider_busy"
        else:
            waits = _defer_count(_events(root), task_id)
            event = "incomplete" if waits + 1 >= DEFER_LIMIT else "deferred"
            reason = "defer_limit" if event == "incomplete" else None
        _append(jobs_path(root), dict(event=event, reason=reason, task_id=task_id, utc=_now(),
                                     providers=[row.get("provider_requested") for row in rows], deferred=deferred))
        ran += 1
        if is_coverage(job):
            coverage_selected += 1
        elif job.get("source") == "production_prompt":
            production_sampled += 1
        if rows and all(fast_defer(row) for row in rows):
            fast += 1
    status.update(called=True, jobs=ran, fast_deferred=fast, invalid_empty_prompt=invalid, closed_at_defer_limit=closed_limit, availability="ran", provider_timeout_s=timeout,
                  chatgpt_timeout_s=budgets.get("chatgpt"),
                  identity=dict(uid=os.geteuid(), home=os.path.expanduser("~")))
    samples = []
    try:
        for line in open(samples_path(root), encoding="utf-8"):
            samples.append(json.loads(line))
    except (OSError, ValueError):
        samples = []
    window = BENCH.window_status([row for row in samples if row.get("kind") == "shadow_evaluation"])
    clock = lock_window(root, window)
    status["window"] = clock["window"]
    status["seven_day"] = clock["seven_day"]
    status["seven_day_ready"] = clock["seven_day_ready"]
    try:
        LT.publish_authority(os.path.join(root, "state", "thf", "learning"), LT.load(os.path.join(root, "state", "thf", "lesson_trace.jsonl")))
    except Exception:
        pass
    status["seats_expected"] = len(LT.SEATS)
    still_open = pending_jobs(root)
    still_audit = [job for job in still_open if job.get("source") == "production_prompt" and not production_callable(job, now_dt, window_s)]
    status["pending_jobs"] = len(still_open) - len(still_audit)
    status["production_audit_held"] = len(still_audit)
    status["production_sampled"] = production_sampled
    status["coverage_selected"] = coverage_selected
    status["throughput"] = "ran_not_a_matched_comparison"
    status["comparison"] = "paired rows in shadow_samples.jsonl only; a production log with shadow_calls 0 is not the trial"
    status["recommendation"] = BENCH.recommend(
        [row for row in samples if row.get("scored") and row.get("kind") == "shadow_evaluation"],
        review_at=clock.get("review_due_utc"))
    status["recommendation"]["global_winner"] = None
    _write_status(root, status)
    return status


def _write_status(root, status):
    path = os.path.join(root, "state", "thf", "shadow_sampler_status.json")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    temporary = path + ".tmp"
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
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
                          window_started=(status.get("window") or {}).get("started"),
                          seven_day=status.get("seven_day")), default=str))
    return 1 if status.get("availability") == "refused_root" else 0


if __name__ == "__main__":
    raise SystemExit(main())
