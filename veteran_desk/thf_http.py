"""Shared dashboard transport and bounded read helpers. No trading side effects."""
import gzip
import hashlib
import json
import threading
import time
from urllib.parse import urlsplit


def accepts_gzip(value):
    """Respect explicit gzip exclusions even when a wildcard allows other encodings."""
    quality = {}
    for item in str(value or "").split(","):
        parts = [part.strip() for part in item.split(";")]
        encoding = parts[0].lower()
        if not encoding:
            continue
        q = 1.0
        for parameter in parts[1:]:
            if parameter.lower().startswith("q="):
                try:
                    q = float(parameter[2:])
                except ValueError:
                    q = 0.0
        quality[encoding] = q if 0 <= q <= 1 else 0.0
    return quality.get("gzip", quality.get("*", 0.0)) > 0


def wire_payload(body, content_type, accept_encoding="", static=False):
    """Compress large text responses, with representation-specific validators for assets."""
    raw = body.encode("utf-8") if isinstance(body, str) else body
    mime = content_type.split(";", 1)[0].lower()
    compressible = mime.startswith("text/") or mime in (
        "application/json", "application/javascript", "application/xml")
    headers = {
        "X-Content-Type-Options": "nosniff",
        "Referrer-Policy": "same-origin",
        "Cache-Control": "no-cache" if static else "no-store, no-cache, must-revalidate, max-age=0",
    }
    if compressible:
        headers["Vary"] = "Accept-Encoding"
    if compressible and len(raw) >= 1024 and accepts_gzip(accept_encoding):
        compressed = gzip.compress(raw, compresslevel=5, mtime=0)
        if len(compressed) < len(raw):
            raw = compressed
            headers["Content-Encoding"] = "gzip"
    if static:
        headers["ETag"] = '"' + hashlib.sha256(raw).hexdigest() + '"'
    return raw, headers


def etag_matches(value, etag):
    return any(token.strip().removeprefix("W/") in (etag, "*")
               for token in str(value or "").split(","))


def mutation_error(headers, max_bytes=12 * 1024 * 1024):
    """Validate before reading a body or forwarding a guardian mutation."""
    if str(headers.get("Sec-Fetch-Site") or "").lower() == "cross-site":
        return 403, "This request did not come from the desk."
    if headers.get("Transfer-Encoding"):
        return 400, "Chunked requests are not supported."
    lengths = headers.get_all("Content-Length") if hasattr(headers, "get_all") else None
    if lengths and len(lengths) != 1:
        return 400, "Use one Content-Length header."
    length = str(headers.get("Content-Length") or "")
    if not length.isascii() or not length.isdigit():
        return 400, "A valid Content-Length is required."
    if len(length) > len(str(max_bytes)):
        return 413, "Request is too large."
    size = int(length)
    if size <= 0 or size > max_bytes:
        return 413, "Request is empty or too large."
    origin = headers.get("Origin")
    if origin:
        try:
            parsed = urlsplit(origin)
            host = urlsplit("//" + str(headers.get("Host") or ""))
            if parsed.scheme not in ("http", "https") or not parsed.hostname or not host.hostname:
                raise ValueError("invalid origin")
            port = parsed.port or (443 if parsed.scheme == "https" else 80)
            host_port = host.port
            allowed_ports = {host_port} if host_port is not None else {80, 443, 18860, 18870, 18812}
            if parsed.username or parsed.password or parsed.path not in ("", "/") or parsed.query or parsed.fragment:
                raise ValueError("invalid origin")
            if parsed.hostname.lower() != host.hostname.lower() or port not in allowed_ports:
                return 403, "Open this desk in its own browser tab before sending a request."
        except ValueError:
            return 403, "This request did not come from the desk."
    return None


def jsonl_tail(path, count):
    """Read only the required end of an append-only UTF-8 log, ignoring malformed rows."""
    if count <= 0:
        return []
    try:
        with open(path, "rb") as stream:
            stream.seek(0, 2)
            position = stream.tell()
            chunks = []
            newlines = 0
            while position > 0 and newlines <= count:
                step = min(position, 65536)
                position -= step
                stream.seek(position)
                chunk = stream.read(step)
                chunks.append(chunk)
                newlines += chunk.count(b"\n")
        rows = b"".join(reversed(chunks)).splitlines()[-count:]
        result = []
        for row in rows:
            if not row.strip():
                continue
            try:
                result.append(json.loads(row))
            except (ValueError, UnicodeDecodeError):
                continue
        return result
    except OSError:
        return []


class SnapshotView:
    """Coalesce simultaneous read-only views; never cache a failed build."""
    def __init__(self, ttl=1.0):
        self.ttl = ttl
        self._lock = threading.Lock()
        self._source = None
        self._value = None
        self._at = float("-inf")

    def get(self, source, build):
        with self._lock:
            if self._source is source and time.monotonic() - self._at < self.ttl:
                return self._value
            value = build()
            self._source, self._value, self._at = source, value, time.monotonic()
            return value
