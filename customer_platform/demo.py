"""Seed a local fixture desk. Writes the password only into the chosen data directory."""

import argparse
import os
import secrets

from customer_platform.platform import Platform


def seed(root, password=None):
    os.makedirs(root, mode=0o700, exist_ok=True)
    password = password or (secrets.token_urlsafe(12) + "Aa1")
    platform = Platform(
        root, enabled=True, signup_enabled=True, allow_fixtures=True,
        webhook_secret=secrets.token_bytes(16),
    )
    if not os.path.exists(os.path.join(platform.store.root, "operators.json")) or _empty_operators(platform):
        platform.create_operator("operator@example.com", password, "Local operator")
    created = platform.provision_tenant(
        "ada@example.com", password, "Ada", starting_capital="10000.00", currency="USD", fixture=True,
    )
    token = platform.login("ada@example.com", password)
    buy = platform.place_order(
        token, symbol="AAPL", side="buy", qty="10", limit_price="100.00",
        idempotency_key="demo-buy-aapl-10",
    )
    platform.fill_order(token, buy["order_id"], price="100.00", fixture=True)
    sell = platform.place_order(
        token, symbol="AAPL", side="sell", qty="10", limit_price="90.00",
        idempotency_key="demo-sell-aapl-10",
    )
    platform.fill_order(token, sell["order_id"], price="90.00", fixture=True)
    platform.run_brain(token, trigger="manual", idempotency_key="demo-brain-once")
    path = os.path.join(platform.store.root, "local_demo_password.txt")
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(password + "\n")
    os.chmod(path, 0o600)
    return {"password_file": path, "tenant_id": created["tenant_id"], "root": platform.store.root}


def _empty_operators(platform):
    from customer_platform.files import read_json
    return not read_json(os.path.join(platform.store.root, "operators.json"), {})


def main(argv=None):
    parser = argparse.ArgumentParser(description="Seed a local customer web fixture. Does not reset the owner desk.")
    parser.add_argument("--root", required=True)
    args = parser.parse_args(argv)
    seeded = seed(args.root)
    print("Local fixture seeded. Password file:", seeded["password_file"])
    print("Start with CUSTOMER_PLATFORM_ENABLE=1 CUSTOMER_PLATFORM_SIGNUP=1 CUSTOMER_PLATFORM_ROOT=" + seeded["root"])
    print("python -m customer_platform --host 127.0.0.1 --port 8765")
    print("Fixture fills are caller-supplied test prices. No charge was made. The owner desk was not reset.")


if __name__ == "__main__":
    main()
