"""Per-tenant provider jobs. No network call unless a test transport is injected.

The product transport is off. A missing customer key and a missing network
send are different states. Neither one invents a model reply.
"""

from urllib.parse import urlparse

from customer_platform.ai_vault import ALLOW
from customer_platform.errors import Denied, ResetBlocked
from customer_platform.files import read_json, write_json
from customer_platform.paperbook import new_id

ENDPOINTS = {
    "openai": "https://api.openai.com/v1/chat/completions",
    "anthropic": "https://api.anthropic.com/v1/messages",
    "grok": "https://api.x.ai/v1/chat/completions",
    "deepseek": "https://api.deepseek.com/v1/chat/completions",
}
MAX_ATTEMPTS = 3
TIMEOUT_SECONDS = 5


class ProviderJobs:
    def __init__(self, path, clock, transport=None):
        self.path = path
        self.clock = clock
        self.transport = transport
        self.calls = 0

    def open_job(self, *, request_id, provider, has_credential):
        if provider not in ENDPOINTS:
            raise Denied("provider is not configurable")
        body = self._load()
        for job in body["jobs"]:
            if job["request_id"] == request_id and job["status"] in {"queued", "unconnected"}:
                return _public(job)
        job = {
            "attempts": 0,
            "cancelled": False,
            "endpoint": ENDPOINTS[provider],
            "error": "",
            "has_credential": bool(has_credential),
            "id": new_id("job_"),
            "prompt_stored": False,
            "provider": provider,
            "request_id": request_id,
            "status": "unconnected",
            "synthetic": False,
            "timeout_seconds": TIMEOUT_SECONDS,
            "utc": self.clock(),
        }
        body["jobs"].append(job)
        self._save(body)
        return _public(job)

    def run(self, job_id, secret):
        body = self._load()
        job = self._job(body, job_id)
        if job["cancelled"] or job["status"] == "cancelled":
            return _public(job)
        if not job["has_credential"] or not secret:
            job["status"] = "unconnected"
            job["error"] = "no customer credential"
            self._save(body)
            return _public(job)
        if self.transport is None:
            job["status"] = "unconnected"
            job["error"] = "outbound send is not enabled"
            self._save(body)
            return _public(job)
        url = job["endpoint"]
        _guard(url, job["provider"])
        last_error = ""
        while job["attempts"] < MAX_ATTEMPTS and not job["cancelled"]:
            job["attempts"] += 1
            self.calls += 1
            try:
                result = self.transport(url, secret, TIMEOUT_SECONDS)
            except Exception:
                last_error = "transport failed"
                continue
            text = str((result or {}).get("text") or "")
            if secret and secret in text:
                job["status"] = "failed"
                job["error"] = "response withheld"
                self._save(body)
                return _public(job)
            if not (result or {}).get("synthetic"):
                job["status"] = "failed"
                job["error"] = "reply was not a labelled synthetic fixture"
                self._save(body)
                return _public(job)
            job["status"] = "complete"
            job["synthetic"] = True
            job["error"] = ""
            job["body"] = text[:500]
            self._save(body)
            return _public(job)
        job["status"] = "failed"
        job["error"] = last_error or "attempt bound reached"
        self._save(body)
        return _public(job)

    def note(self, job_id, status, error):
        body = self._load()
        job = self._job(body, job_id)
        job["status"] = status
        job["error"] = error
        self._save(body)
        return _public(job)

    def cancel(self, job_id):
        body = self._load()
        job = self._job(body, job_id)
        if job["status"] in {"queued", "unconnected"}:
            job["cancelled"] = True
            job["status"] = "cancelled"
            self._save(body)
        return _public(job)

    def _load(self):
        body = read_json(self.path, None)
        if body is None:
            body = {"jobs": []}
            self._save(body)
        return body

    def _save(self, body):
        write_json(self.path, body)

    def _job(self, body, job_id):
        for job in body["jobs"]:
            if job["id"] == job_id:
                return job
        raise ResetBlocked("job", "provider job was not found")


def _guard(url, provider):
    parsed = urlparse(url)
    if parsed.scheme != "https" or parsed.username or parsed.port:
        raise Denied("provider endpoint was refused")
    if parsed.hostname != ALLOW[provider] or url != ENDPOINTS[provider]:
        raise Denied("provider endpoint was refused")


def _public(job):
    return {
        "attempts": job["attempts"],
        "body": job.get("body") or "",
        "calls_are_network": False,
        "endpoint_host": urlparse(job["endpoint"]).hostname,
        "error": job.get("error") or "",
        "id": job["id"],
        "prompt_stored": False,
        "provider": job["provider"],
        "status": job["status"],
        "synthetic": bool(job.get("synthetic")),
    }
