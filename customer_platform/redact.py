"""Redact secrets before they reach a log, status payload, or brain statement."""

import re

_SK = re.compile(r"sk-[A-Za-z0-9_\-]{12,}")
_BEARER = re.compile(r"(?i)bearer\s+[A-Za-z0-9._\-]{8,}")
_KEY_ASSIGN = re.compile(r"(?i)(api[_ ]?key|secret|token)\s*[:=]\s*\S+")


def redact(text, extra=()):
    value = str(text if text is not None else "")
    for secret in extra:
        if secret:
            value = value.replace(str(secret), "[redacted]")
    value = _SK.sub("[redacted]", value)
    value = _BEARER.sub("[redacted]", value)
    value = _KEY_ASSIGN.sub("[redacted]", value)
    return value
