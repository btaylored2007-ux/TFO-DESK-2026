"""Private-trial hosting review. This does not start a process or open a port.

8765 and 8766 are already in use by the owner desk. No trial UNIX user and no
approved data directory have been captured. The controller stays fail closed.
"""

OCCUPIED = (8765, 8766)
MISSING = (
    "a free loopback port other than 8765 and 8766",
    "a new UNIX user that is not the owner desk user",
    "an empty data directory outside /opt/tfo/live and not readable by the owner desk",
)


def review(host="127.0.0.1", port=None, data_root=None, unix_user=None):
    missing = list(MISSING)
    if host not in {"127.0.0.1", "::1"}:
        missing.append("bind is not loopback")
    if port in OCCUPIED or port is None:
        missing.append("port is missing or is an owner port")
    if not data_root or str(data_root).startswith("/opt/tfo/live"):
        missing.append("data directory is missing or is inside the owner live tree")
    if not unix_user or unix_user in {"tfo", "root"}:
        missing.append("trial UNIX user is missing")
    return {
        "apply": False,
        "listen": False,
        "missing": missing,
        "nginx": "unchanged",
        "firewall": "unchanged",
        "owner_services": "not restarted",
        "reason": "metadata required before a private trial can be hosted",
    }
