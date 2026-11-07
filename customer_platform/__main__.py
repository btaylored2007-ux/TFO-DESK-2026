"""Start the loopback customer web app. Refuses to listen unless explicitly enabled."""

import argparse
import os
import threading

from veteran_desk.tenant_foundation import FoundationOff

from customer_platform.http_app import serve, worker_loop
from customer_platform.platform import Platform


def main(argv=None):
    if os.environ.get("CUSTOMER_PLATFORM_ENABLE") != "1":
        raise SystemExit("customer web app is off; set CUSTOMER_PLATFORM_ENABLE=1 to listen on loopback")
    parser = argparse.ArgumentParser(description="Customer web app template, loopback only")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args(argv)
    if args.host not in {"127.0.0.1", "::1"}:
        raise SystemExit("refusing non-loopback host")
    root = os.environ.get("CUSTOMER_PLATFORM_ROOT")
    if not root:
        raise SystemExit("CUSTOMER_PLATFORM_ROOT is required")
    secret = os.environ.get("CUSTOMER_PLATFORM_WEBHOOK_SECRET") or ""
    try:
        platform = Platform(
            root,
            enabled=True,
            webhook_secret=secret.encode("utf-8") if secret else None,
            signup_enabled=os.environ.get("CUSTOMER_PLATFORM_SIGNUP") == "1",
        )
    except FoundationOff as exc:
        raise SystemExit(str(exc))
    stop = threading.Event()
    if os.environ.get("CUSTOMER_PLATFORM_BRAIN_WORKER") == "1":
        threading.Thread(
            target=worker_loop, args=(platform, stop, 30), name="customer-brain", daemon=True,
        ).start()
    serve(platform, args.host, args.port)


if __name__ == "__main__":
    main()
