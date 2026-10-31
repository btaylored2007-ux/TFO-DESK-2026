#!/usr/bin/env python3
"""Nightly self-healing test fixer. PAPER only. Never merges. Never edits a risk rail.

launchd/com.tfo.test-fixer.plist runs this once a night on the Mac. It runs tests/
and veteran_desk/tests. Each new red node is classified:

  stale_test    the assertion still describes an old contract
  real_bug      production raised, or a test invariant broke
  environment   missing live file, blocked network, missing module, test guard
  flaky         the node does not fail on every one of 3 retries

Flaky nodes are quarantined. The three results are logged in reports/test_health.json
and appended to reports/test_quarantine.jsonl. They are not patched.

A stale test or a simple bug with a safe mechanical patch is committed on a branch
and opened as a PR with the gh CLI. If there is no safe patch and CURSOR_API_KEY is
set, a Cursor cloud agent is launched and asked to open the PR. This process never
calls `gh pr merge` and never runs tools/safe_deploy.sh. A fix reaches the desk
only when a person deploys that PR through tools/safe_deploy.sh and the test gate.
"""
from __future__ import annotations

import argparse
import base64
import datetime as dt
import json
import os
import re
import sys
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HEALTH_REL = os.path.join("reports", "test_health.json")
QUARANTINE_REL = os.path.join("reports", "test_quarantine.jsonl")
BASELINE_REL = os.path.join("tools", "test_baseline.json")
AGENT_URL = os.environ.get("CURSOR_AGENT_API_URL", "https://api.cursor.com/v1/agents")
REPO_URL = os.environ.get("TFO_GIT_REPO", "https://github.com/btaylored2007-ux/TFO-DESK-2026")

# Files that encode a risk rail. The fixer may read them and may describe a bug in them.
# It must not write them. A cloud-agent prompt repeats the same list.
RAIL_FILES = (
    "veteran_desk/book.py",
    "veteran_desk/gate_v2.py",
    "veteran_desk/desk.py",
    "funder_gate.py",
    "risk_caps.py",
    "desk_caps.py",
    "oanda_sleeve.py",
    "tfo_buy.py",
    "tfo_crypto.py",
    "theme_exposure.py",
    "hunt48_short_paper.py",
)
# A mechanical patch that assigns one of these is refused even in a test file.
RAIL_ASSIGN = re.compile(
    r"(US_BETA_CAP_PCT|SEAT_NOTIONAL_CAP|OPENS_DISABLED|C3_MAX_RISK_PCT|"
    r"RISK_A_DEFAULT|RISK_NON_A_MIN|RISK_A_CAPBIND_MIN|max_trades|total_max_trades)\s*=\s*\S+"
)
# (actual, expected) pairs the local fixer may apply inside one test function.
# actual is what the code returned; expected is the literal the test still asserts.
STALE_PAIRS = (
    ("RETIRED", "PENDING"),
    ("ok (grok)", "ok (codex)"),
)
ENV_TYPES = {
    "FileNotFoundError", "PermissionError", "ModuleNotFoundError",
    "ConnectionError", "URLError", "TimeoutError", "socket.gaierror",
}
ENV_TEXT = ("network blocked in CI", "TEST GUARD", "No module named")


def is_rail(path):
    path = path.replace("\\", "/").lstrip("./")
    return any(path == r or path.endswith("/" + r) for r in RAIL_FILES)


def rail_blocked(path, old, new):
    """True when a proposed edit would touch a risk rail or change a rail assignment."""
    if is_rail(path):
        return True
    old_m, new_m = RAIL_ASSIGN.search(old or ""), RAIL_ASSIGN.search(new or "")
    if new_m and (not old_m or old_m.group(0) != new_m.group(0)):
        return True
    return False


def assert_allowed(argv):
    """Refuse merge and refuse running the deploy gate. The fixer only opens a PR.

    A PR body may mention tools/safe_deploy.sh. That is a sentence, not an invocation:
    the script is refused only when it is the program being run.
    """
    if "pr" in argv and "merge" in argv:
        raise RuntimeError("refused: the test fixer never merges")
    for a in argv:
        text = str(a)
        base = os.path.basename(text)
        if base == "safe_deploy.sh" or text.rstrip().endswith("/safe_deploy.sh"):
            raise RuntimeError("refused: fixes deploy only through tools/safe_deploy.sh, and not from this job")
    return argv


def classify(nodeid, message, traceback):
    """stale_test, real_bug, or environment. Flaky is decided by the retry, not here."""
    blob = "%s\n%s" % (message or "", traceback or "")
    exc = _exc_name(blob)
    if exc in ENV_TYPES or any(t in blob for t in ENV_TEXT):
        return "environment"
    if exc == "AssertionError":
        return "stale_test"
    if exc in ("KeyError", "IndexError") and _frame_is_test(traceback or "", nodeid):
        return "stale_test"
    if "TypeError" in blob and "'set' object is not callable" in blob and _frame_is_test(traceback or "", nodeid):
        return "stale_test"
    return "real_bug"


def _exc_name(blob):
    # pytest -tb=short / line: "E   AssertionError: ..." or "E   KeyError: 'x'"
    for line in reversed(blob.splitlines()):
        s = line.strip()
        if s.startswith("E "):
            s = s[2:].strip()
        m = re.match(r"^([A-Za-z_][A-Za-z0-9_.]*)(?::|\s)", s)
        if m and m.group(1).split(".")[-1][0].isupper():
            name = m.group(1)
            return name.split(".")[-1] if "." in name else name
    return ""


def _frame_is_test(traceback, nodeid):
    test_file = (nodeid or "").split("::")[0]
    if test_file and test_file in (traceback or ""):
        return True
    return "/tests/" in (traceback or "").replace("\\", "/") or traceback.replace("\\", "/").startswith("tests/")


def judge_retries(outcomes):
    """outcomes is a list of 'passed' or 'failed' from the quarantine retries.

    A node that does not fail on every retry is flaky and is quarantined.
    A node that fails all three is stable; the caller keeps the original class.
    """
    if not outcomes:
        return None
    if any(o == "passed" for o in outcomes):
        return "flaky"
    return None


def parse_pytest(text):
    """Node ids from pytest -rfE lines. The short message after ' - ' is kept."""
    out = []
    seen = set()
    for line in (text or "").splitlines():
        m = re.match(r"^(FAILED|ERROR)\s+(\S+)", line)
        if not m:
            continue
        node = m.group(2)
        if node in seen:
            continue
        seen.add(node)
        msg = line.split(" - ", 1)[1].strip() if " - " in line else ""
        out.append({"nodeid": node, "kind": m.group(1), "message": msg})
    summary = ""
    for line in reversed((text or "").splitlines()):
        if re.search(r"\b(passed|failed|error)\b", line):
            summary = line.strip()
            break
    return out, summary


def load_known(root):
    path = os.path.join(root, BASELINE_REL)
    try:
        return set(json.load(open(path)).get("known_failures") or [])
    except Exception:
        return set()


def slice_test(src, nodeid):
    name = (nodeid or "").split("::")[-1]
    if not name:
        return None
    key = "def " + name
    i = src.find(key)
    if i < 0:
        return None
    j = len(src)
    for marker in ("\n    def ", "\ndef ", "\nclass "):
        k = src.find(marker, i + len(key))
        if k >= 0:
            j = min(j, k)
    return i, j


def propose_fix(failure, src):
    """A mechanical patch dict {path, old, new} or None. Never targets a risk rail."""
    path = (failure.get("nodeid") or "").split("::")[0]
    if not path or is_rail(path):
        return None
    if not path.startswith("tests/") and not path.startswith("veteran_desk/tests/"):
        return None
    msg = failure.get("message") or ""
    tb = failure.get("traceback") or ""
    blob = msg + "\n" + tb
    if "'set' object is not callable" in blob and "self.fail" in src and "self.fail(" not in src:
        new = src.replace("self.fail", "self.blocked")
        if new != src and not rail_blocked(path, src, new):
            return {"path": path, "old": src, "new": new, "why": "test shadowed unittest.TestCase.fail with a set"}
    if failure.get("class") != "stale_test":
        return None
    bounds = slice_test(src, failure.get("nodeid"))
    if not bounds:
        return None
    i, j = bounds
    body = src[i:j]
    for actual, expected in STALE_PAIRS:
        if actual not in blob or expected not in blob:
            continue
        # unittest: AssertionError: 'ACTUAL' != 'EXPECTED'
        if "'%s' != '%s'" % (actual, expected) not in blob and '"%s" != "%s"' % (actual, expected) not in blob and "%s != %s" % (actual, expected) not in blob:
            continue
        needle = '"%s"' % expected
        if needle not in body:
            needle = "'%s'" % expected
        if needle not in body:
            continue
        repl = needle[0] + actual + needle[0]
        new_body = body.replace(needle, repl, 1)
        new = src[:i] + new_body + src[j:]
        if new != src and not rail_blocked(path, body, new_body):
            return {"path": path, "old": src, "new": new, "why": "stale literal %s -> %s in %s" % (expected, actual, failure.get("nodeid"))}
    return None


def agent_prompt(failure, ref):
    rails = ", ".join(RAIL_FILES)
    return (
        "PAPER only. A test went red on the TFO desk.\n"
        "Node: %s\nClass: %s\nMessage: %s\n\nTraceback:\n%s\n\n"
        "Fix this one failure. If the test is stale against the current rules, update the test. "
        "If it is a real bug outside a risk rail, fix the code. "
        "Current rules: OANDA at most 2%% per trade and 4 open, US beta 60%%, single-name notional 40%%, "
        "grade A is 1%% (1.5%% when the 40%% cap binds), non-A floor is 2%%, CoinGlass stays retired.\n"
        "Do not edit these risk-rail files: %s.\n"
        "Do not touch ledgers or secrets. Do not delete a test. Do not merge. "
        "Open a PR only. The PR must say the fix deploys only through tools/safe_deploy.sh and the test gate.\n"
        "Base ref: %s.\n" % (failure.get("nodeid"), failure.get("class"), failure.get("message"), (failure.get("traceback") or "")[:4000], rails, ref)
    )


def launch_cursor_agent(api_key, repository, ref, prompt, http_post, url=None):
    body = {
        "name": "TFO test fixer",
        "prompt": {"text": prompt},
        "repos": [{"url": repository, "startingRef": ref}],
        "autoCreatePR": True,
        "workOnCurrentBranch": False,
    }
    auth = base64.b64encode((api_key + ":").encode()).decode()
    headers = {"Authorization": "Basic " + auth, "Content-Type": "application/json"}
    status, payload = http_post(url or AGENT_URL, body, headers)
    return {"action": "cloud_agent", "http_status": status, "agent": payload, "pr_url": None}


def gh_pr_commands(branch, base, title, body, paths):
    cmds = [
        ["git", "checkout", "-b", branch],
        ["git", "add", "--"] + list(paths),
        ["git", "commit", "-m", title],
        ["git", "push", "-u", "origin", branch],
        ["gh", "pr", "create", "--title", title, "--body", body, "--head", branch, "--base", base],
    ]
    for c in cmds:
        assert_allowed(c)
    return cmds


def pr_body(failure, why):
    return (
        "PAPER only. Opened by tools/test_fixer.py. Do not merge this from the fixer.\n\n"
        "Node: `%s`\nClass: %s\nWhy: %s\n\n"
        "Deploy only through `tools/safe_deploy.sh` inside the existing test gate. "
        "This job does not run that script and does not merge.\n\n"
        "Risk rails were not edited.\n" % (failure.get("nodeid"), failure.get("class"), why)
    )


def _utcnow(now):
    return (now or dt.datetime.now(dt.timezone.utc)).strftime("%Y-%m-%dT%H:%M:%SZ")


def build_report(rows, summary, generated):
    n_flaky = sum(1 for r in rows if r["class"] == "flaky")
    n_bug = sum(1 for r in rows if r["class"] in ("stale_test", "real_bug"))
    n_env = sum(1 for r in rows if r["class"] == "environment")
    if not rows:
        status, tone, headline = "green", "ok", "Both suites green"
    elif n_bug:
        status, tone, headline = "alarm", "alarm", "%d new red, %d flaky" % (n_bug + n_env, n_flaky)
    elif n_env and not n_flaky:
        status, tone, headline = "watch", "watch", "%d environment failure(s)" % n_env
    elif n_flaky and not n_env:
        status, tone, headline = "watch", "watch", "%d flaky, quarantined" % n_flaky
    else:
        status, tone, headline = "watch", "watch", "%d environment, %d flaky" % (n_env, n_flaky)
    return {
        "paper_only": True,
        "generated_utc": generated,
        "suites": ["tests/", "veteran_desk/tests"],
        "pytest_summary": summary,
        "summary": {"new_red": n_bug + n_env, "flaky": n_flaky, "rows": len(rows)},
        "status": status,
        "card": {"title": "Test health", "headline": headline, "tone": tone, "paper_only": True},
        "failures": rows,
        "deploy": "Fixes deploy only through tools/safe_deploy.sh and the test gate. This job never merges and never edits a risk rail.",
    }


def write_report(root, report, write_text):
    report = dict(report)
    write_text(HEALTH_REL, json.dumps(report, indent=2) + "\n")
    for row in report["failures"]:
        if row["class"] == "flaky" or row.get("retries"):
            write_text(QUARANTINE_REL, json.dumps({
                "utc": report["generated_utc"], "nodeid": row["nodeid"], "class": row["class"],
                "retries": row.get("retries") or [], "action": row.get("action"),
            }) + "\n", append=True)
    return report


def act(failure, *, root, read_text, write_text, env, run_cmd, http_post, dry_run, ref):
    """Decide the follow-up for one stable failure. Returns the action fields."""
    if failure["class"] in ("flaky", "environment"):
        return {"action": "quarantined" if failure["class"] == "flaky" else "logged", "pr_url": None, "agent_url": None}
    src = ""
    path = (failure.get("nodeid") or "").split("::")[0]
    try:
        src = read_text(path) if path else ""
    except Exception:
        src = ""
    patch = propose_fix(failure, src) if src else None
    if patch and rail_blocked(patch["path"], patch["old"], patch["new"]):
        return {"action": "needs_human", "reason": "patch would edit a risk rail", "pr_url": None, "agent_url": None}
    if patch:
        branch = "cursor/test-fixer-%s" % re.sub(r"[^a-z0-9]+", "-", failure["nodeid"].split("::")[-1].lower())[:48]
        title = "Test fixer: %s" % failure["nodeid"].split("::")[-1][:72]
        body = pr_body(failure, patch["why"])
        cmds = gh_pr_commands(branch, ref, title, body, [patch["path"]])
        if not dry_run:
            write_text(patch["path"], patch["new"])
            for c in cmds:
                run_cmd(c)
        return {"action": "pr_opened", "branch": branch, "why": patch["why"], "pr_url": None, "agent_url": None, "commands": [" ".join(c) for c in cmds]}
    key = (env or {}).get("CURSOR_API_KEY") or ""
    if key and not dry_run:
        launched = launch_cursor_agent(key, env.get("TFO_GIT_REPO") or REPO_URL, ref, agent_prompt(failure, ref), http_post)
        agent = launched.get("agent") or {}
        url = agent.get("url") or (agent.get("agent") or {}).get("url") or agent.get("target", {}).get("url")
        return {"action": "cloud_agent", "http_status": launched.get("http_status"), "agent_url": url, "pr_url": None, "reason": "no safe mechanical patch; cloud agent asked to open a PR"}
    if key and dry_run:
        return {"action": "cloud_agent", "dry_run": True, "pr_url": None, "agent_url": None, "reason": "would launch a Cursor cloud agent"}
    return {"action": "needs_human", "reason": "no safe mechanical patch and CURSOR_API_KEY is unset", "pr_url": None, "agent_url": None}


def nightly(*, root, run_suite, run_node, read_text, write_text, env=None, run_cmd=None, http_post=None, dry_run=False, now=None, ref=None):
    """Run both suites, classify new reds, write the War Room card, maybe open a PR."""
    env = env or {}
    ref = ref or env.get("TFO_FIX_BASE") or "main"
    text = run_suite()
    found, summary = parse_pytest(text)
    known = load_known(root)
    rows = []
    for item in found:
        if item["nodeid"] in known:
            continue
        retries = []
        for n in range(1, 4):
            rc, out = run_node(item["nodeid"])
            outcome = "passed" if rc == 0 else "failed"
            retries.append({"n": n, "outcome": outcome, "tail": (out or "")[-500:]})
        kind = judge_retries([r["outcome"] for r in retries])
        tb = "\n".join(r["tail"] for r in retries)
        message = item.get("message") or ""
        if not message:
            message = tb[-300:]
        cls = kind or classify(item["nodeid"], message, tb)
        failure = {"nodeid": item["nodeid"], "class": cls, "message": message, "traceback": tb, "retries": retries, "stable": cls != "flaky"}
        extra = act(failure, root=root, read_text=read_text, write_text=write_text, env=env, run_cmd=run_cmd or (lambda a: 0), http_post=http_post or (lambda *a, **k: (0, {})), dry_run=dry_run, ref=ref)
        failure.update(extra)
        rows.append(failure)
    report = build_report(rows, summary, _utcnow(now))
    write_report(root, report, write_text)
    return report


def _http_post(url, body, headers):
    data = json.dumps(body).encode()
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=60) as resp:
        raw = resp.read().decode()
        return resp.status, (json.loads(raw) if raw else {})


def _run_cmd(argv):
    import subprocess
    assert_allowed(argv)
    return subprocess.call(argv, cwd=ROOT)


def _read(path):
    with open(os.path.join(ROOT, path)) as f:
        return f.read()


def _write(path, text, append=False):
    dest = os.path.join(ROOT, path)
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    mode = "a" if append else "w"
    with open(dest, mode) as f:
        f.write(text)


def _run_suite():
    import subprocess
    env = dict(os.environ)
    env["TFO_TEST_OFFLINE"] = "1"
    chunks = []
    for target in ("tests", "veteran_desk/tests"):
        p = subprocess.run([sys.executable, "-m", "pytest", target, "-q", "-p", "no:cacheprovider", "-rfE", "--tb=line", "--continue-on-collection-errors"], cwd=ROOT, env=env, capture_output=True, text=True)
        chunks.append(p.stdout or "")
        chunks.append(p.stderr or "")
    return "\n".join(chunks)


def _run_node(nodeid):
    import subprocess
    env = dict(os.environ)
    env["TFO_TEST_OFFLINE"] = "1"
    p = subprocess.run([sys.executable, "-m", "pytest", nodeid, "-q", "-p", "no:cacheprovider", "--tb=short"], cwd=ROOT, env=env, capture_output=True, text=True)
    return p.returncode, (p.stdout or "") + "\n" + (p.stderr or "")


def main(argv=None):
    ap = argparse.ArgumentParser(description="Nightly TFO test fixer (PAPER). Never merges.")
    ap.add_argument("--dry-run", action="store_true", help="classify and write the report; do not edit or open a PR")
    ap.add_argument("--base", default=os.environ.get("TFO_FIX_BASE", "main"))
    args = ap.parse_args(argv)
    report = nightly(root=ROOT, run_suite=_run_suite, run_node=_run_node, read_text=_read, write_text=_write, env=dict(os.environ), run_cmd=_run_cmd, http_post=_http_post, dry_run=args.dry_run, ref=args.base)
    print(json.dumps({"status": report["status"], "card": report["card"], "rows": report["summary"]}, indent=2))
    return 0 if report["status"] != "alarm" else 1


if __name__ == "__main__":
    sys.exit(main())
