#!/usr/bin/env python3
"""Incremental, base-hash-guarded copy for the 2026-11-06 paper upgrade.

Dry-run is the default. It never talks to a broker and never restarts a service.
The live book is special: sha256 b4453b50… is ALREADY_PROTECTED and is not rewritten.
Any other hash on that path is BLOCKED. Nothing is copied when the book is blocked.

Other files copy only when the destination is missing or already byte-identical.
A different destination hash is BLOCKED so a newer droplet file is not replaced.
"""
import argparse
import hashlib
import json
import shutil
import sys
from pathlib import Path

BOOK = "veteran_desk/book.py"
BOOK_SHA = "b4453b503f33e34110519bf4d7a9bf391ecc2802edbd87acca3e77dc96b7e891"
PAYLOADS = (
    "veteran_desk/research_mandate.py",
    "veteran_desk/thf_shadow_bench.py",
    "veteran_desk/thf_shadow_sampler.py",
    "veteran_desk/lesson_trace.py",
    "veteran_desk/feed_health.py",
    "veteran_desk/seat_brain.py",
    "veteran_desk/thf_round.py",
    "veteran_desk/thf_msgdesk.py",
    "veteran_desk/desk.py",
    "veteran_desk/session_expiry.py",
    "veteran_desk/b_tight.py",
    "veteran_desk/bridge_maintenance.py",
    "veteran_desk/thf_http.py",
    "veteran_desk/seat_risk.py",
    "veteran_desk/pit_fx_sgp.py",
    "asia_intraday_sgp.py",
    "thf_feedkeeper.py",
    "tools/intraday_bars_pull.py",
    "tools/thf_chat.js",
    "tools/thf_eli10.html",
    "tools/thf_eli10.js",
    "tools/thf_chat_page.html",
    "tools/thf_chat.html",
    "assets/brand/mark.svg",
    "assets/brand/wordmark.svg",
    "assets/brand/favicon.svg",
    "assets/brand/tokens.css",
    "deploy/incremental_20261106/tfo-thf-shadow.service",
    "deploy/incremental_20261106/tfo-thf-shadow.timer",
    "state/thf/shadow_sampler.json",
    "tfo_equity_refresh.py",
    "thf_guardian/__init__.py",
    "thf_guardian/ai_mode.py",
    "thf_guardian/arm_guard.py",
    "thf_guardian/config.py",
    "thf_guardian/seats.py",
    "thf_guardian/util.py",
    "tools/bars_snapshot.py",
    "tools/thf_dash_refresh.sh",
    "veteran_desk/ai_readiness.py",
    "veteran_desk/arm_last.py",
    "veteran_desk/brain_hb.py",
    "veteran_desk/catalyst_stream.py",
    "veteran_desk/cfo_report.py",
    "veteran_desk/crypto_live.py",
    "veteran_desk/day_clock.py",
    "veteran_desk/engineering_deepseek.py",
    "veteran_desk/execution_rules.py",
    "veteran_desk/filing_freshness.py",
    "veteran_desk/fixer_work.py",
    "veteran_desk/fut_roll.py",
    "veteran_desk/gate_v2.py",
    "veteran_desk/headline_subject.py",
    "veteran_desk/leaderboard.py",
    "veteran_desk/live_ctx.py",
    "veteran_desk/my_positions.py",
    "veteran_desk/news_desk.py",
    "veteran_desk/pack_fit.py",
    "veteran_desk/pit.py",
    "veteran_desk/pit_splits.py",
    "veteran_desk/pnl_light.py",
    "veteran_desk/provider_rotation.py",
    "veteran_desk/scan_universe.py",
    "veteran_desk/seat_registry.py",
    "veteran_desk/sec_forms.py",
    "veteran_desk/small_coin.py",
    "veteran_desk/strategy_lab.py",
    "veteran_desk/thf_charts.py",
    "veteran_desk/thf_crypto_feed.py",
    "veteran_desk/thf_exdiv.py",
    "veteran_desk/thf_paid_pack.py",
    "veteran_desk/thf_prompt_catalog.py",
    "veteran_desk/thf_scan.py",
    "veteran_desk/thf_ticker.py",
    "veteran_desk/thf_chat_home.py",
    "queue_answerer_sgp.py",
)
SRC_OVERRIDE = {
    "state/thf/shadow_sampler.json": "deploy/incremental_20261106/shadow_sampler.json",
    # The working tree book is the separate t2808u paper delta. This installer still carries only the live pin.
    "veteran_desk/book.py": "docs/live_bank07/book.py",
}
# Live snapshot hashes the destination may already have. Any other hash blocks the copy.
BASE_SHA = {
    "veteran_desk/book.py": ("b4453b503f33e34110519bf4d7a9bf391ecc2802edbd87acca3e77dc96b7e891",),
    "veteran_desk/desk.py": ("c5d24291f0418a7e64bccc749b73e6e21ebd930147b6b0fae16a2d81eba4b677",),
    "veteran_desk/thf_msgdesk.py": ("a81024f6f58c11855abada95c0f61b6497155c23c76517635c00aa1c2b58000b",),
    "veteran_desk/thf_round.py": ("883e38fd12a411f6c813b043d331b70d6e7e41dc59f5c7da9fe72cca969dad94",
                                 "de11e34e68d068ed21b33c631daad965e6660422e1349cdd0e4b155e01b38121"),
    "veteran_desk/seat_brain.py": ("a41b554fdca333365a1c2ba1aa9fff5fc3228696f490f2c141b809f3a53b5624",
                                  "dcb25dddadc5b8c2bfd3e36607b98a785912596cba94911baf39f8b7eb87cc31"),
    "tools/thf_chat.js": ("a83e22766f11850ec02ebbbefa41b2693475309ca9e2bd574b38e6deb7ab28f6",
                          "711c78961ac08b3416705023820154beae6e43d9fa7094a72ad2e6b24ec79a0a"),
    "tfo_equity_refresh.py": ("438c93940ae7a4ddde0a4e7c471072f94fe782a99dfbe6fe25d9732ba6541137",),
    "veteran_desk/execution_rules.py": ("61abf73035bd88a471695009eda9bdb329efd97dcc0747c89e1d1a5fcdefb77f",),
    "veteran_desk/fut_roll.py": ("fbb71eb33b5d9cacf0d42cbf2e54bc5c0e1194c9e65cc9d72874ae127e5a13d9",),
    "veteran_desk/gate_v2.py": ("6a76df04ecda13287d212afa0adb5706967603ceb44293f412444cdc9cc2d476",),
    "veteran_desk/leaderboard.py": ("b502c7d66ab7f347c6c35f7c94abf0b123a5f5a28fa4b027ac875e46207b7e64",),
    "veteran_desk/live_ctx.py": ("54f448cdd731502f862b332a12a14441351fa9f5e22bff92cf9035a71169f34b",),
    "veteran_desk/news_desk.py": ("ada173eb29b9194927debe071f5f127b2b25502787e0d2ae2fb6db789c67dab3",),
    "veteran_desk/pit.py": ("2b2578bdc76ab4d46e4fcea89b719f6a8ee137afa70ba7ba36b497cf1dc7cb42",),
    "veteran_desk/scan_universe.py": ("cb0c069e09b2eb48f7c4fc15b0608b1f182f7fa6f6c11b90b95090d6c59fd9b9",),
    "veteran_desk/thf_paid_pack.py": ("155a908e8c7cd8a968bd0220bc8240beef7c79c8a99cab20fc9f140dc838d935",),
    "veteran_desk/thf_chat_home.py": ("d50f4c4ad1169c5da52f6b3e1eb7c756d337a9d6d06930b222bfda7b8b157a76",),
    "queue_answerer_sgp.py": ("0059287299eb6a41596461290a7a4a7295f3fdd917faa56c82a531ab56c3e4af",),
    # Reconciled against the live bytes, not added as a blind hash. Payload is that file plus the feed or brand edit.
    "asia_intraday_sgp.py": ("be9bc8592f1d6e0792a9e3b58aa20c57c572952a2d8e2d3787e94e39f8e5a053",),
    "thf_feedkeeper.py": ("a6b435ff1c00af63c2d275382709b90bc14ea72c778a8e4e12bceafa604023db",),
    "tools/intraday_bars_pull.py": ("e382d170ec55c6eb2a93be99049687e6ce6467281650402449f891967faa825d",),
    "tools/thf_chat.html": ("2209a95bfa5b0856fb5d4d13144f4672f4049a67e6699484c1ac6ebf791fd1f4",),
    "tools/thf_chat_page.html": ("d2cf8aa0b9fd7cc6beaeeecd5f6e689f1c881086937f1bb1ebc6eb56d4344bd1",),
    # Live hub kept; brand and chat height only. Not the short replacement.
    "tools/thf_eli10.html": ("503a2c6aecb908904aa464d3cf3d7aaf173c988471ce2cba094d1c27e24601f9",),
    "tools/thf_eli10.js": ("c085f878b7c0fbc8eda40c757b312fcbfd1853cf0f6397b2d7d5a13015270bc8",),
}

_HERE = Path(__file__).resolve().parent
ROOT = _HERE / "payload" if (_HERE / "payload").is_dir() else _HERE.parents[1]


def sha256(path):
    digest = hashlib.sha256()
    with open(path, "rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 16), b""):
            digest.update(chunk)
    return digest.hexdigest()


def payload_file(rel):
    override = ROOT / SRC_OVERRIDE.get(rel, rel)
    direct = ROOT / rel
    return override if override.is_file() else direct


def classify(src, dest, book=False, bases=()):
    if not src.is_file():
        return "MISSING_PAYLOAD", None
    payload = sha256(src)
    if book and payload != BOOK_SHA:
        return "PAYLOAD_HASH_MISMATCH", payload
    if not dest.exists():
        return "INSTALL", payload
    current = sha256(dest)
    if book:
        if current == BOOK_SHA:
            return "ALREADY_PROTECTED", current
        return "BLOCKED", current
    if current == payload:
        return "CURRENT", current
    if current in bases:
        return "REPLACE", current
    return "BLOCKED", current


def plan(live_root):
    live = Path(live_root)
    rows = []
    src = payload_file(BOOK)
    dest = live / BOOK
    state, digest = classify(src, dest, book=True, bases=BASE_SHA.get(BOOK, ()))
    rows.append(dict(path=BOOK, state=state, sha256=digest, base_sha256=list(BASE_SHA.get(BOOK, ())),
                     payload_sha256=sha256(src) if src.is_file() else None))
    for rel in PAYLOADS:
        payload = payload_file(rel)
        state, digest = classify(payload, live / rel, book=False, bases=BASE_SHA.get(rel, ()))
        rows.append(dict(path=rel, state=state, sha256=digest, base_sha256=list(BASE_SHA.get(rel, ())),
                         payload_sha256=sha256(payload) if payload.is_file() else None))
    return rows


def apply_plan(live_root, rows):
    live = Path(live_root)
    copied = []
    if any(row["state"] == "BLOCKED" or row["state"].startswith("PAYLOAD") or row["state"] == "MISSING_PAYLOAD" for row in rows):
        return False
    for row in rows:
        if row["state"] not in ("INSTALL", "REPLACE"):
            continue
        src = payload_file(row["path"])
        dest = live / row["path"]
        dest.parent.mkdir(parents=True, exist_ok=True)
        backup_rel = None
        if dest.exists():
            backup_rel = "state/thf/increment_20261106_backups/" + row["path"]
            backup = live / backup_rel
            backup.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(dest, backup)
        shutil.copy2(src, dest)
        if sha256(dest) != sha256(src):
            return False
        copied.append(dict(path=row["path"], backup=backup_rel, payload_sha256=sha256(dest),
                           replaced_sha256=row.get("sha256")))
    receipt = live / "state/thf/increment_20261106_receipt.json"
    receipt.parent.mkdir(parents=True, exist_ok=True)
    receipt.write_text(json.dumps(dict(copied=copied, book_sha256=BOOK_SHA), indent=2))
    return True


def rollback(live_root):
    live = Path(live_root)
    receipt_path = live / "state/thf/increment_20261106_receipt.json"
    if not receipt_path.is_file():
        print("ROLLBACK_NO_RECEIPT")
        return 2
    receipt = json.loads(receipt_path.read_text())
    for row in receipt.get("copied") or []:
        dest = live / row["path"]
        if dest.is_file() and sha256(dest) != row["payload_sha256"]:
            print("ROLLBACK_BLOCKED %s" % row["path"])
            return 2
    for row in receipt.get("copied") or []:
        dest = live / row["path"]
        backup = live / row["backup"] if row.get("backup") else None
        if backup and backup.is_file():
            shutil.copy2(backup, dest)
        elif dest.is_file():
            dest.unlink()
    book = live / BOOK
    if book.is_file() and sha256(book) != BOOK_SHA:
        print("ROLLBACK_BOOK_HASH_UNEXPECTED")
        return 2
    print("ROLLBACK_RESTORED")
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description="Dry-run the guarded 2026-11-06 increment.")
    parser.add_argument("--root", required=True, help="Destination tree. Not used against production from this workspace.")
    parser.add_argument("--apply", action="store_true", help="Copy INSTALL and REPLACE rows. Refused when any row is BLOCKED.")
    parser.add_argument("--rollback", action="store_true", help="Restore backups from the receipt. Does not restart services.")
    args = parser.parse_args(argv)
    if args.rollback:
        return rollback(args.root)
    rows = plan(args.root)
    blocked = False
    for row in rows:
        print("%s %s" % (row["state"], row["path"]))
        if row["state"] in ("BLOCKED", "PAYLOAD_HASH_MISMATCH", "MISSING_PAYLOAD"):
            blocked = True
    if args.apply:
        if blocked or not apply_plan(args.root, rows):
            print("APPLY_REFUSED")
            return 2
        print("APPLY_COPIED_INSTALL_ROWS")
        return 0
    print("DRY_RUN")
    print("BOOK_RUNTIME %s" % next(row["state"] for row in rows if row["path"] == BOOK))
    ready = [row["path"] for row in rows if row["state"] in ("INSTALL", "REPLACE")]
    print("INSTALL_READY %d" % len(ready))
    print("SERVICE_IMPACT shadow timer only; do not restart tfo-thf-tick")
    for row in rows:
        if row["base_sha256"]:
            print("BASE %s %s" % (row["path"], ",".join(row["base_sha256"])))
    return 3 if blocked else 0


if __name__ == "__main__":
    sys.exit(main())
