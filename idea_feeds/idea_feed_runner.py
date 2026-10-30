#!/Library/Frameworks/Python.framework/Versions/3.14/bin/python3
"""Idea-feed runner: asks an AI CLI (ChatGPT/Codex or Claude Code) for a
WATCH-LIST of ideas and saves JSON + Markdown under idea_feeds/.

IDEAS ONLY. This script never places orders, never writes V1 ledgers / order
files / tickets, and never touches V2 (tfo_v2*). The only files it writes are
inside idea_feeds/ (outputs, raw captures, logs, and a scratch dir the AI
tool may use as its sandboxed working directory).

Usage: idea_feed_runner.py --tool chatgpt|claude --market US|ASX|auto
(auto = ASX before 12:00 Sydney time, US otherwise)

2026-10-09: Claude is switched OFF (Brett). The Claude CLI is never called unless
TFO_CLAUDE_ENABLED=1 is set. The ChatGPT feed now also runs the CRITIC role: after a
successful scan, a second GPT pass red-teams that scan's ideas and writes
critic_chatgpt_<MKT>_<stamp>.json/.md (+ latest_critic_<MKT>.json). `--tool claude`
scans are a logged no-op and `ask --tool claude` is answered by ChatGPT.
"""
import argparse
import datetime as dt
import json
import os
import subprocess
import sys
from pathlib import Path
from zoneinfo import ZoneInfo

FEED_DIR = Path(__file__).resolve().parent
SCRATCH = FEED_DIR / ".scratch"
RAW_DIR = FEED_DIR / "raw"
SYD = ZoneInfo("Australia/Sydney")
TIMEOUT_S = 32 * 60   # launchd US run 21:35 + 32m = 22:07, before the 22:08 Head Trader read

CODEX_BIN = "/Applications/ChatGPT.app/Contents/Resources/codex"
CLAUDE_BIN = "/Users/test/.local/bin/claude"
# Claude OFF by default (Brett, 2026-10-09; repeated 429 "out of usage credits").
# Set TFO_CLAUDE_ENABLED=1 in the environment to bring the Claude CLI back.
CLAUDE_ENABLED = os.environ.get("TFO_CLAUDE_ENABLED", "") == "1"
CRITIC_ENABLED = os.environ.get("TFO_GPT_CRITIC", "1") != "0"
CRITIC_TIMEOUT_S = int(os.environ.get("TFO_CRITIC_TIMEOUT_S", str(20 * 60)))

SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["generated_at", "source", "market", "note", "ideas"],
    "properties": {
        "generated_at": {"type": "string"},
        "source": {"type": "string"},
        "market": {"type": "string"},
        "note": {"type": "string"},
        "ideas": {
            "type": "array",
            "maxItems": 10,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["ticker", "thesis", "catalyst", "adv_usd_10d",
                             "spread_bp_est", "stop", "label"],
                "properties": {
                    "ticker": {"type": "string"},
                    "thesis": {"type": "string"},
                    "catalyst": {"type": "string"},
                    "adv_usd_10d": {"type": ["number", "null"]},
                    "spread_bp_est": {"type": ["number", "null"]},
                    "stop": {"type": "string"},
                    "label": {"type": "string", "enum": ["beta", "edge"]},
                },
            },
        },
    },
}


def build_prompt(market: str, source: str, now: dt.datetime) -> str:
    if market == "ASX":
        universe = ("ASX-listed stocks and ETFs (tickers with the .AX suffix, e.g. BHP.AX) "
                    "of any market cap, including micro-caps under A$100M")
        session = "today's ASX session (opens 10:00 AEST)"
        px = "priced at A$5 or more"
        adv = ("10-day average daily dollar volume of at least A$20M (report it in AUD in the "
               "adv_usd_10d field and say so in the note)")
    else:
        universe = ("US-listed stocks and ETFs (NYSE, Nasdaq, NYSE American, Cboe) of any "
                    "market cap, including micro-caps under $100M")
        session = "today's US regular session (opens 09:30 ET)"
        px = "priced at $5 or more"
        adv = "10-day average daily dollar volume of at least US$20M"
    return f"""You are an idea scout for a PAPER-trading desk. Current time: {now.isoformat()} (Australia/Sydney).
WATCH-LIST IDEAS ONLY: do not place orders, do not create, edit or delete any files, do not run trading scripts.
You may run read-only commands (e.g. curl public quote/chart APIs such as Yahoo Finance) and use web search to gather facts.

Task: scan {universe}. Use web search / tools to find what is moving and why (news, earnings, guidance, upgrades,
momentum, sector flows). Return up to 10 ideas for {session} that satisfy ALL of:
- {px}
- {adv}
- bid/ask spread of about 10 basis points or less
For each idea give: ticker, a one-line thesis, the catalyst (news / earnings / momentum - be specific and dated),
the 10-day average dollar volume (number, in dollars), an estimated spread in bp, and a suggested stop (price level
and/or % from last price, with the reference price you used).
Label each idea honestly: "beta" if it is mostly market/sector exposure (index ETFs, sector ETFs, momentum riding the tape),
"edge" only if there is a specific, verifiable, stock-level informational or event reason.
If nothing qualifies, return an empty ideas list and put "NONE" plus the reason in note.
NO INVENTED DATA: if you cannot verify a number, set it to null and say so in the note; never guess tickers, prices, volumes or news.
Output ONLY strict JSON (no markdown fences, no prose) with this shape:
{{"generated_at": "<ISO8601 timestamp>", "source": "{source}", "market": "{market}", "note": "<data basis, caveats, or NONE + reason>",
  "ideas": [{{"ticker": "...", "thesis": "...", "catalyst": "...", "adv_usd_10d": 0, "spread_bp_est": 0, "stop": "...", "label": "beta|edge"}}]}}
"""


def extract_json(text: str):
    text = text.strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.lower().startswith("json"):
            text = text[4:]
    try:
        return json.loads(text)
    except Exception:
        pass
    s, e = text.find("{"), text.rfind("}")
    if s >= 0 and e > s:
        return json.loads(text[s:e + 1])
    raise ValueError("no JSON object found")


def validate(doc):
    errs = []
    if not isinstance(doc, dict):
        return ["top level is not an object"]
    for k in ("generated_at", "source", "ideas"):
        if k not in doc:
            errs.append(f"missing {k}")
    ideas = doc.get("ideas")
    if not isinstance(ideas, list):
        errs.append("ideas is not a list")
        return errs
    if len(ideas) > 10:
        errs.append("more than 10 ideas")
    for i, it in enumerate(ideas):
        if not isinstance(it, dict):
            errs.append(f"idea {i} not an object"); continue
        for k in ("ticker", "thesis", "catalyst", "adv_usd_10d", "spread_bp_est", "stop", "label"):
            if k not in it:
                errs.append(f"idea {i} missing {k}")
        if it.get("label") not in ("beta", "edge"):
            errs.append(f"idea {i} bad label {it.get('label')!r}")
    return errs


def screen_flags(doc):
    """Flag (do not delete) ideas whose self-reported numbers miss the hard filters."""
    flags = []
    advs = [i.get("adv_usd_10d") for i in doc.get("ideas", []) if isinstance(i.get("adv_usd_10d"), (int, float))]
    for it in doc.get("ideas", []):
        f = []
        if isinstance(it.get("adv_usd_10d"), (int, float)) and advs.count(it["adv_usd_10d"]) > 1:
            f.append("adv_duplicated_across_tickers_suspect_copy_error")
        adv = it.get("adv_usd_10d")
        sp = it.get("spread_bp_est")
        if adv is None:
            f.append("adv_unverified")
        elif isinstance(adv, (int, float)) and adv < 20e6:
            f.append("adv_below_20M")
        if sp is None:
            f.append("spread_unverified")
        elif isinstance(sp, (int, float)) and sp > 12:
            f.append("spread_above_10bp")
        if f:
            flags.append({"ticker": it.get("ticker"), "flags": f})
    return flags


# ---- MAX MODEL / MAX EFFORT (Brett hard rule 2026-09-29 19:14 AEST) ----
# Codex: strongest model in `codex debug models` catalog (priority 1) at its highest
# reasoning level ("ultra" = maximum reasoning + automatic task delegation).
CODEX_MODEL = "gpt-6-astra"
CODEX_EFFORT = "ultra"
# Claude Code: `best` = Fable 5 where the account has access, else latest Opus;
# explicit fallback to opus if Fable is unavailable/overloaded. Effort: max.
CLAUDE_MODEL = "best"
CLAUDE_FALLBACK = "opus"
CLAUDE_EFFORT = "max"


def codex_cmd(schema_path, last_msg):
    cmd = [CODEX_BIN, "exec",
           "--skip-git-repo-check", "--ephemeral", "--color", "never",
           "-C", str(SCRATCH),
           "-m", CODEX_MODEL,
           "-c", f'model_reasoning_effort="{CODEX_EFFORT}"',
           "-s", "workspace-write",                       # writable ONLY in .scratch
           "-c", "sandbox_workspace_write.network_access=true",
           "-c", 'web_search="live"',
           "-c", 'approval_policy="never"']
    if schema_path:
        cmd += ["--output-schema", str(schema_path)]
    return cmd + ["-o", str(last_msg), "-"]


def claude_cmd(prompt, schema, read_tools=False):
    allowed = ["WebSearch", "WebFetch"] + (["Read", "Glob", "Grep"] if read_tools else [])
    cmd = [CLAUDE_BIN, "-p",
           "--model", CLAUDE_MODEL,
           "--fallback-model", CLAUDE_FALLBACK,
           "--effort", CLAUDE_EFFORT,
           "--output-format", "json",
           "--no-session-persistence"]
    if schema:
        cmd += ["--json-schema", json.dumps(schema)]
    return cmd + ["--allowedTools", *allowed,
                  "--disallowedTools", "Bash", "Write", "Edit", "MultiEdit", "NotebookEdit",
                  "--permission-mode", "dontAsk",
                  "--", prompt]


def run_chatgpt(prompt: str, schema_path, last_msg: Path, timeout=None):
    p = subprocess.run(codex_cmd(schema_path, last_msg), input=prompt, capture_output=True,
                       text=True, timeout=timeout or TIMEOUT_S, cwd=str(SCRATCH))
    text = last_msg.read_text() if last_msg.exists() else ""
    return p.returncode, text, p.stdout + "\n--- stderr ---\n" + p.stderr


def run_claude(prompt: str, schema_path, last_msg: Path, timeout=None, schema="scan", read_tools=False):
    st = subprocess.run([CLAUDE_BIN, "auth", "status"], capture_output=True, text=True,
                        timeout=60, cwd=str(SCRATCH))
    try:
        if not json.loads(st.stdout).get("loggedIn"):
            return 3, "", "Claude Code CLI is NOT logged in (claude auth status: loggedIn=false). " \
                          "Brett must run `claude` in Terminal and complete /login.\n" + st.stdout + st.stderr
    except Exception:
        pass
    sch = SCHEMA if schema == "scan" else None
    p = subprocess.run(claude_cmd(prompt, sch, read_tools), capture_output=True, text=True,
                       timeout=timeout or TIMEOUT_S, cwd=str(SCRATCH), stdin=subprocess.DEVNULL)
    text = ""
    try:
        env = json.loads(p.stdout)
        so = env.get("structured_output")
        text = json.dumps(so) if so else (env.get("result") or "")
        if env.get("is_error"):
            return p.returncode or 1, text, p.stdout + "\n--- stderr ---\n" + p.stderr
    except Exception:
        text = p.stdout
    last_msg.write_text(text)
    return p.returncode, text, p.stdout + "\n--- stderr ---\n" + p.stderr


CRITIC_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["generated_at", "source", "market", "scan_stem", "note", "reviews"],
    "properties": {
        "generated_at": {"type": "string"},
        "source": {"type": "string"},
        "market": {"type": "string"},
        "scan_stem": {"type": "string"},
        "note": {"type": "string"},
        "reviews": {
            "type": "array",
            "maxItems": 10,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["ticker", "verdict", "objections", "checks", "label_ok"],
                "properties": {
                    "ticker": {"type": "string"},
                    "verdict": {"type": "string", "enum": ["keep", "watch", "reject"]},
                    "objections": {"type": "string"},
                    "checks": {"type": "string"},
                    "label_ok": {"type": "boolean"},
                },
            },
        },
    },
}


def build_critic_prompt(doc, market: str, scan_stem: str, now: dt.datetime) -> str:
    ideas = json.dumps(doc.get("ideas") or [], indent=1)
    return f"""You are the CRITIC (red team) for a PAPER-trading desk's idea feed. Current time: {now.isoformat()} (Australia/Sydney).
CRITIQUE ONLY: do not place orders, do not create, edit or delete any files, do not run trading scripts.
You may run read-only commands (e.g. curl public quote/chart APIs) and use web search to verify facts.

Another scout produced the watch-list below for the {market} session (scan {scan_stem}). Your job is to try to break it.
For EACH idea, independently re-check: does the catalyst exist and is it dated correctly; is price >= 5 and 10-day
average dollar volume >= 20M (AUD for .AX names) and spread about 10bp or less; is the stop sensible versus the reference
price; is the "edge" vs "beta" label honest (edge only for a specific, verifiable stock-level reason).
Verdict per idea: "keep" (survives scrutiny), "watch" (plausible but unverified or weak), "reject" (fails a hard filter,
catalyst wrong/stale, or invented data). Put the specific problems in objections and what you verified (with sources)
in checks. Set label_ok false if the beta/edge label is wrong. Never invent data; say "unverified" instead.
Do not add new ideas. Review every idea below exactly once, by ticker.

Ideas under review (JSON):
{ideas}

Output ONLY strict JSON (no markdown fences, no prose):
{{"generated_at": "<ISO8601>", "source": "chatgpt-codex-cli-critic", "market": "{market}", "scan_stem": "{scan_stem}",
  "note": "<overall view, data basis, caveats>",
  "reviews": [{{"ticker": "...", "verdict": "keep|watch|reject", "objections": "...", "checks": "...", "label_ok": true}}]}}
"""


def validate_critic(doc):
    if not isinstance(doc, dict):
        return ["top level is not an object"]
    errs = [f"missing {k}" for k in ("generated_at", "reviews") if k not in doc]
    revs = doc.get("reviews")
    if not isinstance(revs, list):
        return errs + ["reviews is not a list"]
    for i, r in enumerate(revs):
        if not isinstance(r, dict):
            errs.append(f"review {i} not an object"); continue
        for k in ("ticker", "verdict", "objections", "checks", "label_ok"):
            if k not in r:
                errs.append(f"review {i} missing {k}")
        if r.get("verdict") not in ("keep", "watch", "reject"):
            errs.append(f"review {i} bad verdict {r.get('verdict')!r}")
    return errs


def critic_to_md(doc, stem):
    lines = [f"# {stem}", "",
             "**CRITIC (GPT red team) of a WATCH-LIST - not orders, not approvals. V1 gates still apply.**", "",
             f"- generated_at: {doc.get('generated_at')}", f"- scan: {doc.get('scan_stem')}",
             f"- market: {doc.get('market')}", f"- note: {doc.get('note', '')}", ""]
    revs = doc.get("reviews") or []
    if not revs:
        lines.append("_No reviews returned._")
    else:
        lines += ["| Ticker | Verdict | Label ok | Objections | Checks |", "|---|---|---|---|---|"]
        for r in revs:
            cells = [str(r.get("ticker", "")), str(r.get("verdict", "")), str(r.get("label_ok")),
                     str(r.get("objections", "")), str(r.get("checks", ""))]
            lines.append("| " + " | ".join(c.replace("|", "/").replace("\n", " ") for c in cells) + " |")
    return "\n".join(lines) + "\n"


def run_critic(doc, market: str, scan_stem: str, now: dt.datetime, runner=None) -> dict:
    """GPT critic pass over a successful scan. Never raises; never touches the scan's own files."""
    runner = runner or run_chatgpt
    stem = f"critic_chatgpt_{market}_{scan_stem.rsplit('_', 2)[-2]}_{scan_stem.rsplit('_', 1)[-1]}"
    status = {"stem": stem, "tool": "chatgpt", "role": "critic", "market": market, "scan_stem": scan_stem,
              "ok": False}
    if not (doc.get("ideas") or []):
        status.update(ok=True, skipped="scan returned no ideas", finished_at=dt.datetime.now(SYD).isoformat())
        safe_path(FEED_DIR / f"latest_critic_{market}.json").write_text(json.dumps(status, indent=2))
        return status
    out_json = safe_path(FEED_DIR / f"{stem}.json")
    out_md = safe_path(FEED_DIR / f"{stem}.md")
    raw_path = safe_path(RAW_DIR / f"{stem}.raw.txt")
    last_msg = safe_path(RAW_DIR / f"{stem}.last_message.txt")
    schema_path = safe_path(RAW_DIR / "critic_schema.json")
    schema_path.write_text(json.dumps(CRITIC_SCHEMA, indent=2))
    try:
        rc, text, raw = runner(build_critic_prompt(doc, market, scan_stem, now), schema_path, last_msg,
                               timeout=CRITIC_TIMEOUT_S)
    except subprocess.TimeoutExpired as e:
        rc, text, raw = 124, "", f"TIMEOUT after {CRITIC_TIMEOUT_S}s\n{e}"
    except Exception as e:  # critic must never break the scan
        rc, text, raw = 1, "", f"critic runner error: {e}"
    raw_path.write_text(f"rc={rc}\n=== final message ===\n{text}\n=== raw ===\n{raw}")
    status["rc"] = rc
    try:
        if rc != 0 and not text.strip():
            raise RuntimeError(f"CLI exit {rc}; see {raw_path.name}")
        cdoc = extract_json(text)
        errs = validate_critic(cdoc)
        if errs:
            raise ValueError("; ".join(errs))
        cdoc["source"] = cdoc.get("source") or "chatgpt-codex-cli-critic"
        cdoc["scan_stem"] = scan_stem
        cdoc["watch_only"] = True
        out_json.write_text(json.dumps(cdoc, indent=2))
        out_md.write_text(critic_to_md(cdoc, stem))
        status.update(ok=True, json=str(out_json), md=str(out_md),
                      verdicts={str(r.get("ticker")): r.get("verdict") for r in cdoc["reviews"]})
    except Exception as e:
        status.update(error=str(e), raw=str(raw_path))
        safe_path(FEED_DIR / f"{stem}.FAILED.txt").write_text(
            f"Critic validation/run failure: {e}\nRaw output kept at {raw_path}\n")
    status["finished_at"] = dt.datetime.now(SYD).isoformat()
    safe_path(FEED_DIR / f"latest_critic_{market}.json").write_text(json.dumps(status, indent=2))
    return status


ASK_PREAMBLE = """You are a research assistant for a PAPER-trading desk's Head Trader.
Read-only research: do NOT place orders, do NOT create, edit or delete files, do NOT run trading scripts.
You may use web search and read-only commands/files. Cite sources (URLs) for factual claims; never invent data -
say "unverified" if you cannot confirm something. Current time: {now} (Australia/Sydney).

Question:
"""


def ask(tool: str, question: str, outname: str) -> int:
    import re
    if tool == "claude" and not CLAUDE_ENABLED:
        print("Claude is OFF (TFO_CLAUDE_ENABLED!=1); answering with ChatGPT instead.", file=sys.stderr)
        tool = "chatgpt"
    now = dt.datetime.now(SYD)
    adhoc = FEED_DIR / "adhoc"
    for d in (SCRATCH, RAW_DIR, adhoc):
        d.mkdir(parents=True, exist_ok=True)
    name = re.sub(r"[^A-Za-z0-9_.-]+", "_", outname or "ask")[:60] or "ask"
    stem = f"{tool}_{now:%Y%m%d_%H%M%S}_{name}"
    out_md = safe_path(adhoc / f"{stem}.md")
    raw_path = safe_path(RAW_DIR / f"adhoc_{stem}.raw.txt")
    last_msg = safe_path(RAW_DIR / f"adhoc_{stem}.last_message.txt")
    prompt = ASK_PREAMBLE.format(now=now.isoformat()) + question
    timeout = int(os.environ.get("ASK_TIMEOUT_S", str(TIMEOUT_S)))
    try:
        if tool == "chatgpt":
            rc, text, raw = run_chatgpt(prompt, None, last_msg, timeout=timeout)
        else:
            rc, text, raw = run_claude(prompt, None, last_msg, timeout=timeout, schema=None, read_tools=True)
    except subprocess.TimeoutExpired as e:
        rc, text, raw = 124, "", f"TIMEOUT after {timeout}s\n{e}"
    raw_path.write_text(f"rc={rc}\n=== final message ===\n{text}\n=== raw ===\n{raw}")
    model = f"{CODEX_MODEL} effort={CODEX_EFFORT}" if tool == "chatgpt" else f"{CLAUDE_MODEL} (fallback {CLAUDE_FALLBACK}) effort={CLAUDE_EFFORT}"
    body = text.strip() or f"**FAILED (rc={rc})** - raw output kept at {raw_path}"
    out_md.write_text(f"# Ad-hoc research ({tool}) - {now:%Y-%m-%d %H:%M %Z}\n\n"
                      f"_Research only - not an order or approval. Model: {model}._\n\n"
                      f"## Question\n\n{question}\n\n## Answer\n\n{body}\n")
    print(out_md)
    return 0 if (rc == 0 and text.strip()) else 1


def to_md(doc, stem, flags):
    lines = [f"# {stem}", "",
             "**WATCH-LIST IDEAS ONLY - not orders. Must pass V1 gates (TFO Equity / pre-open hunt) before any paper entry.**", "",
             f"- generated_at: {doc.get('generated_at')}", f"- source: {doc.get('source')}",
             f"- market: {doc.get('market')}", f"- note: {doc.get('note', '')}", ""]
    ideas = doc.get("ideas") or []
    if not ideas:
        lines.append("**NONE** - no qualifying ideas returned.")
    else:
        lines += ["| # | Ticker | Label | ADV 10d | Spread bp | Stop | Catalyst | Thesis |",
                  "|---|---|---|---|---|---|---|---|"]
        for i, it in enumerate(ideas, 1):
            adv = it.get("adv_usd_10d")
            advs = f"{adv/1e6:,.1f}M" if isinstance(adv, (int, float)) else "n/a"
            cells = [str(i), it.get("ticker", ""), it.get("label", ""), advs,
                     str(it.get("spread_bp_est")), it.get("stop", ""),
                     it.get("catalyst", ""), it.get("thesis", "")]
            lines.append("| " + " | ".join(c.replace("|", "/").replace("\n", " ") for c in cells) + " |")
    if flags:
        lines += ["", "## Validator flags (self-reported numbers missing hard filters)"]
        lines += [f"- {f['ticker']}: {', '.join(f['flags'])}" for f in flags]
    return "\n".join(lines) + "\n"


def safe_path(p: Path) -> Path:
    p = p.resolve()
    assert FEED_DIR in p.parents, f"refusing to write outside idea_feeds: {p}"
    assert "tfo_v2" not in p.name, "refusing to write a tfo_v2* file"
    return p


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tool", required=True, choices=["chatgpt", "claude"])
    ap.add_argument("--market", choices=["US", "ASX", "auto"])
    ap.add_argument("--ask", help="free-form research question (ad-hoc mode, see ask.sh)")
    ap.add_argument("--outname", default="ask")
    a = ap.parse_args()
    if a.ask:
        return ask(a.tool, a.ask, a.outname)
    if not a.market:
        ap.error("--market is required for scans")
    now = dt.datetime.now(SYD)
    if a.market == "auto":  # launchd: morning run = ASX, evening run = US pre-open
        a.market = "ASX" if now.hour < 12 else "US"
    if a.tool == "claude" and not CLAUDE_ENABLED:
        # Claude is OFF: do not call the Claude CLI (no more 429s, no FAILED files).
        # The critic role is run by the ChatGPT feed (critic_chatgpt_* files).
        status = {"stem": f"claude_{a.market}_{now:%Y%m%d_%H%M}", "tool": "claude", "market": a.market,
                  "ok": False, "disabled": True, "finished_at": now.isoformat(),
                  "note": "Claude CLI disabled (set TFO_CLAUDE_ENABLED=1 to re-enable); critic role runs in the ChatGPT feed"}
        safe_path(FEED_DIR / f"latest_claude_{a.market}.json").write_text(json.dumps(status, indent=2))
        print(json.dumps(status), flush=True)
        return 0
    for d in (SCRATCH, RAW_DIR):
        d.mkdir(parents=True, exist_ok=True)
    stem = f"{a.tool}_{a.market}_{now:%Y%m%d_%H%M}"
    source = {"chatgpt": "chatgpt-codex-cli", "claude": "claude-code-cli"}[a.tool]
    out_json = safe_path(FEED_DIR / f"{stem}.json")
    out_md = safe_path(FEED_DIR / f"{stem}.md")
    raw_path = safe_path(RAW_DIR / f"{stem}.raw.txt")
    last_msg = safe_path(RAW_DIR / f"{stem}.last_message.txt")
    schema_path = safe_path(RAW_DIR / "idea_schema.json")
    schema_path.write_text(json.dumps(SCHEMA, indent=2))
    prompt = build_prompt(a.market, source, now)
    print(f"[{now:%Y-%m-%d %H:%M:%S %Z}] start {stem}", flush=True)
    try:
        runner = run_chatgpt if a.tool == "chatgpt" else run_claude
        rc, text, raw = runner(prompt, schema_path, last_msg)
    except subprocess.TimeoutExpired as e:
        rc, text, raw = 124, "", f"TIMEOUT after {TIMEOUT_S}s\n{e}"
    raw_path.write_text(f"rc={rc}\n=== final message ===\n{text}\n=== raw ===\n{raw}")
    status = {"stem": stem, "tool": a.tool, "market": a.market, "rc": rc,
              "finished_at": dt.datetime.now(SYD).isoformat(), "ok": False}
    try:
        if rc != 0 and not text.strip():
            raise RuntimeError(f"CLI exit {rc}; see {raw_path.name}")
        doc = extract_json(text)
        errs = validate(doc)
        if errs:
            raise ValueError("; ".join(errs))
        doc.setdefault("source", source)
        doc["source"] = doc.get("source") or source
        flags = screen_flags(doc)
        doc["watch_only"] = True
        doc["validator_flags"] = flags
        out_json.write_text(json.dumps(doc, indent=2))
        out_md.write_text(to_md(doc, stem, flags))
        status.update(ok=True, json=str(out_json), md=str(out_md),
                      tickers=[i.get("ticker") for i in doc["ideas"]])
    except Exception as e:
        status.update(error=str(e), raw=str(raw_path))
        fail = safe_path(FEED_DIR / f"{stem}.FAILED.txt")
        fail.write_text(f"Validation/run failure: {e}\nRaw output kept at {raw_path}\n")
    safe_path(FEED_DIR / f"latest_{a.tool}_{a.market}.json").write_text(json.dumps(status, indent=2))
    print(json.dumps(status), flush=True)
    if status["ok"] and a.tool == "chatgpt" and CRITIC_ENABLED:
        # Scan status/files are already written; the critic is additive and cannot change them.
        cstat = run_critic(doc, a.market, stem, dt.datetime.now(SYD))
        print(json.dumps(cstat), flush=True)
    return 0 if status["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
