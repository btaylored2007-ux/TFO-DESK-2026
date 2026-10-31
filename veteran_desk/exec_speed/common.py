"""File helpers for the exec-speed services. PAPER ONLY. No secrets."""
import datetime as dt
import json
import os
import tempfile

UTC = dt.timezone.utc


def utc_now(now=None):
    t = now or dt.datetime.now(UTC)
    if t.tzinfo is None:
        t = t.replace(tzinfo=UTC)
    return t.astimezone(UTC)


def iso(now=None):
    return utc_now(now).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_utc(text):
    t = dt.datetime.fromisoformat(str(text).replace("Z", "+00:00"))
    if t.tzinfo is None:
        t = t.replace(tzinfo=UTC)
    return t.astimezone(UTC)


def atomic_json(path, payload):
    """Readers see one complete generation. Never writes NaN."""
    directory = os.path.dirname(path) or "."
    os.makedirs(directory, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".exec-speed-", dir=directory)
    try:
        with os.fdopen(fd, "w") as fh:
            json.dump(payload, fh, indent=2, allow_nan=False, default=str)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            try:
                os.unlink(tmp)
            except OSError:
                pass


def read_json(path, default=None):
    try:
        with open(path) as fh:
            return json.load(fh)
    except Exception:
        return default


def append_jsonl(path, row):
    directory = os.path.dirname(path) or "."
    os.makedirs(directory, exist_ok=True)
    with open(path, "a") as fh:
        fh.write(json.dumps(row, default=str, allow_nan=False) + "\n")


def redact(text, secrets):
    """Strip secret values from a log line. Short tokens are left alone."""
    out = "" if text is None else str(text)
    for secret in secrets or ():
        token = "" if secret is None else str(secret).strip()
        if len(token) >= 8:
            out = out.replace(token, "[redacted]")
    return out
