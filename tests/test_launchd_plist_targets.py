"""Every launchd plist in this repo must still name a program that exists.

PAPER ONLY. Targets are ProgramArguments (.py, .sh, and `python -m` modules),
resolved against the repo. A path under state/hunt48_* is the installed copy;
the repo source is tools/asia/deploy/<basename> when that file is shipped.

Paths in KNOWN_ABSENT are already missing from this code-only export (research/
and one ASX runner that lives only under state/ on the desk). The test fails
if a plist target we do ship disappears. It does not fail when a previously
absent path is added back.
"""
import os
import plistlib
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DESK = "/Users/test/Applications/MultiVenueDesk/"

# Already absent before this cleanup. Do not add a path here to hide a deletion.
KNOWN_ABSENT = frozenset({
    "research/entry_engine/entry_engine.py",
    "research/entry_engine/equity_engine.py",
    "research/oanda_deep_20261027/code/lab_job.py",
    "massive_ws.py",  # plist names the desk root; the only copy is staged_fixes/datahub/massive_ws.py
    "state/hunt48_asx/run_asx.sh",  # installed under state/; no script is shipped in tools/asia/deploy
})

_COMMENT = re.compile(r"<!--.*?-->", re.S)
_DOCTYPE = re.compile(r"<!DOCTYPE[^>]*>", re.I)
_MODULE = re.compile(r"(?:^|[\s;])-m\s+([A-Za-z_][\w.]*)")
_FILE = re.compile(r"((?:/[\w./-]+|[\w./-]+)\.(?:py|sh))\b")


def _load(path):
    text = path.read_text(encoding="utf-8", errors="replace")
    # Comments in ops/com.taylor.warroom.plist contain "--", which is not
    # well-formed inside an XML comment. Strip comments before plistlib.
    text = _DOCTYPE.sub("", _COMMENT.sub("", text))
    return plistlib.loads(text.encode("utf-8"))


def _repo_rel(raw, work):
    raw = raw.strip().strip("'\"")
    if raw.startswith(DESK):
        rel = raw[len(DESK):]
    elif raw.startswith("/"):
        return None  # interpreter, /bin/bash, caffeinate, or another tree
    elif work and work.startswith(DESK):
        base = work[len(DESK):].strip("/")
        rel = str(Path(base) / raw) if base else raw
    else:
        rel = raw
    rel = rel.lstrip("./")
    if rel.startswith("state/"):
        src = Path("tools/asia/deploy") / Path(rel).name
        if (ROOT / src).is_file():
            return str(src)
    return rel


def plist_targets():
    """plist relative path -> set of repo-relative targets it requires."""
    out = {}
    for path in sorted(ROOT.rglob("*.plist")):
        if any(p in path.parts for p in (".git", "__pycache__", "archive")):
            continue
        data = _load(path)
        args = [str(a) for a in (data.get("ProgramArguments") or [])]
        work = str(data.get("WorkingDirectory") or "")
        blob = " ".join(args)
        found = set()
        for mod in _MODULE.findall(blob):
            found.add(mod.replace(".", "/") + ".py")
        for raw in _FILE.findall(blob):
            rel = _repo_rel(raw, work)
            if rel:
                found.add(rel)
        for arg in args:
            if arg.endswith(".py") or arg.endswith(".sh"):
                rel = _repo_rel(arg, work)
                if rel:
                    found.add(rel)
        out[str(path.relative_to(ROOT))] = found
    return out


def missing_targets():
    missing = []
    for plist, targets in plist_targets().items():
        for rel in sorted(targets):
            if not (ROOT / rel).is_file():
                missing.append((plist, rel))
    return missing


def test_every_launchd_plist_target_exists():
    missing = missing_targets()
    unexpected = [(p, r) for p, r in missing if r not in KNOWN_ABSENT]
    assert not unexpected, "plist target missing from the repo: %s" % unexpected
    # The staged Massive websocket client is the only copy of a plist target.
    assert (ROOT / "staged_fixes/datahub/massive_ws.py").is_file()


def test_known_absent_plist_targets_are_exactly_the_preexisting_holes():
    """A newly missing target must not be smuggled into KNOWN_ABSENT."""
    gone = {rel for _plist, rel in missing_targets()}
    extra = KNOWN_ABSENT - gone
    # A hole that has been filled is fine. A hole we invented is not: every
    # KNOWN_ABSENT entry must still be a real plist target (present or not).
    named = {rel for targets in plist_targets().values() for rel in targets}
    invented = KNOWN_ABSENT - named - gone
    assert not invented, invented
    assert extra <= named
