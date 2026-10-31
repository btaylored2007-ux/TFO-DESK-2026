"""Strip secrets out of any text we might store. Never log a vendor key."""
import re

_PAIR = re.compile(
    r"(?i)(api[_-]?key|apikey|api_token|access_token|authorization|bearer|secret|token)"
    r"(\s*[=:]\s*|\s+)[^\s&\"']+"
)


def scrub(text, limit=180):
    s = text if isinstance(text, str) else str(text)
    s = _PAIR.sub(lambda m: m.group(1) + "=***", s)
    s = re.sub(r"(?i)(apikey|api_key|api_token|token)=[^&\s]+", r"\1=***", s)
    return s[:limit]
