"""Display-only P&L mark. Does not open, write, or rewrite the book, the ledger, or capital.

The saved mark stores the book's pnl_since_inception_aud (equity minus seed) at the moment
an owner asks for a display reset. Later readings subtract that saved number. At the mark,
the difference is 0 because the current field equals the saved field. It is not a hardcoded
zero and it is not the lifetime figure.

There is no HTTP route. The owner command is local and requires the confirm token.
"""
import argparse
import hashlib
import json
import os
import sys
import tempfile
from pathlib import Path

CONFIRM = "OWNER-DISPLAY-RESET"
FILENAME = "display_epoch.json"
AUDIT = "display_epoch_audit.jsonl"
REQUIRED = ("id", "set_utc", "pnl_since_inception_aud", "equity_aud", "seed_aud")


def epoch_file(state_dir):
    return Path(state_dir) / FILENAME


def audit_file(state_dir):
    return Path(state_dir) / AUDIT


def read_epoch(path):
    file_path = Path(path) if path else None
    if file_path is None or not file_path.is_file():
        return None
    data = json.loads(file_path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or data.get("pnl_since_inception_aud") is None:
        return None
    return data


def since_reset_aud(current_pnl, epoch):
    """Current book equity-minus-seed, minus the saved mark. None if either side is missing."""
    if not epoch or current_pnl is None:
        return None
    base = epoch.get("pnl_since_inception_aud")
    if base is None:
        return None
    return round(float(current_pnl) - float(base), 2)


def _aud(value):
    from veteran_desk.thf_home import aud
    return aud(value, sign=True)


def attach(payload, today=None, path=None):
    """Add display-reset fields to a home dict. Removes them when no mark is saved."""
    if not isinstance(payload, dict):
        return payload
    epoch = read_epoch(path)
    keys = ("since_reset_aud", "since_reset_txt", "since_reset_note", "since_reset_mark_utc", "since_reset_book_updated")
    if not epoch:
        for key in keys:
            payload.pop(key, None)
        return payload
    current = payload.get("since_aud")
    if current is None and isinstance(today, dict):
        current = today.get("pnl_since_inception_aud")
    delta = since_reset_aud(current, epoch)
    payload["since_reset_aud"] = delta
    payload["since_reset_txt"] = _aud(delta) if delta is not None else "—"
    payload["since_reset_mark_utc"] = epoch.get("set_utc")
    note = "Display only: book equity minus capital, measured from the saved mark. Not since inception."
    seed_now = None if not isinstance(today, dict) else today.get("seed_aud")
    if seed_now is not None and epoch.get("seed_aud") is not None and abs(float(seed_now) - float(epoch["seed_aud"])) > 0.5:
        note += " Capital (seed) has changed since that mark."
    if payload.get("book_updated"):
        note += " Book time " + str(payload["book_updated"]) + "."
        payload["since_reset_book_updated"] = payload.get("book_updated")
    payload["since_reset_note"] = note
    return payload


def stamp_page(html, today=None, path=None):
    """Patch the inlined home JSON. Leave the page unchanged when no mark is saved or the block is unreadable."""
    if not isinstance(html, str) or not read_epoch(path):
        return html
    marker = '<script id="home0"'
    start = html.find(marker)
    if start < 0:
        return html
    open_end = html.find(">", start)
    close = html.find("</script>", open_end)
    if open_end < 0 or close < 0:
        return html
    raw = html[open_end + 1:close].replace("<\\/", "</")
    try:
        data = json.loads(raw)
    except ValueError:
        return html
    if not isinstance(data, dict):
        return html
    attach(data, today, path)
    blob = json.dumps(data, default=str).replace("</", "<\\/")
    return html[:open_end + 1] + blob + html[close:]


def _canonical(baseline):
    missing = [key for key in REQUIRED if baseline.get(key) is None]
    if missing:
        raise ValueError("baseline missing " + ",".join(missing))
    body = {key: baseline[key] for key in (
        "id", "set_utc", "set_wita", "pnl_since_inception_aud", "equity_aud", "seed_aud",
        "cash_aud", "invested_aud", "commission_expense_aud", "today_06_00_aud",
        "book_midnight_today_aud", "open", "armed", "capital_note", "basis", "source",
    ) if key in baseline}
    body["basis"] = "pnl_since_inception_aud"
    body["display_only"] = True
    body["ledger_rewritten"] = False
    encoded = json.dumps(body, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return body, hashlib.sha256(encoded).hexdigest()


def _write_atomic(dest, text):
    dest.parent.mkdir(parents=True, exist_ok=True)
    handle = tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=str(dest.parent), prefix=".epoch-", delete=False)
    tmp = Path(handle.name)
    try:
        handle.write(text)
        handle.flush()
        os.fsync(handle.fileno())
        handle.close()
        os.replace(tmp, dest)
    except Exception:
        handle.close()
        if tmp.exists():
            tmp.unlink()
        raise


def _audit(state_dir, row):
    path = audit_file(state_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(row, sort_keys=True) + "\n")
        stream.flush()
        os.fsync(stream.fileno())


def establish(state_dir, baseline, confirm, replace=False):
    if confirm != CONFIRM:
        raise ValueError("confirm token refused")
    body, digest = _canonical(baseline)
    dest = epoch_file(state_dir)
    if dest.is_file():
        current = json.loads(dest.read_text(encoding="utf-8"))
        same = current.get("id") == body["id"] and current.get("sha256") == digest
        if same:
            return dict(status="ALREADY_SET", id=body["id"], sha256=digest, path=str(dest))
        if not replace:
            raise ValueError("a display mark is already saved; refusing to replace it")
    body["sha256"] = digest
    _write_atomic(dest, json.dumps(body, indent=2) + "\n")
    if json.loads(dest.read_text(encoding="utf-8")).get("sha256") != digest:
        raise RuntimeError("saved mark does not match the request")
    _audit(state_dir, dict(action="set", id=body["id"], sha256=digest, set_utc=body.get("set_utc")))
    return dict(status="SET", id=body["id"], sha256=digest, path=str(dest))


def main(argv=None):
    parser = argparse.ArgumentParser(description="Save a display-only P&L mark. Does not write the book.")
    parser.add_argument("command", choices=("establish",))
    parser.add_argument("--state-dir", required=True)
    parser.add_argument("--baseline", required=True)
    parser.add_argument("--confirm", required=True)
    parser.add_argument("--replace", action="store_true")
    args = parser.parse_args(argv)
    baseline = json.loads(Path(args.baseline).read_text(encoding="utf-8"))
    try:
        result = establish(args.state_dir, baseline, args.confirm, replace=args.replace)
    except ValueError as error:
        print(str(error))
        print("NOT_APPLIED")
        return 2
    print(result["status"])
    print(result["sha256"])
    print("LEDGER_NOT_WRITTEN")
    return 0


if __name__ == "__main__":
    sys.exit(main())
