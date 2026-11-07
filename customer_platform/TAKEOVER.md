# Takeover — customer web app template

Another session can continue from this branch without the owner desk. Do not SSH, deploy, or edit production. The coordinator copies the source archive onto Singapore outside the production tree.

## Product sequencing

The first product is a hosted customer web app: secure signup, login, customer sessions, an isolated dashboard, private durable learning, and practice-capital reset.

Not part of this template:

- a native desktop app
- an installable package
- a self-host distribution

Later, roadmap only, not built:

- marketing website integration
- public leaderboard statistics

Do not publish private performance and do not treat silence as consent to publish it. Real billing and live broker activation stay unwired.

## Git

- Branch: `cursor/customer-platform-foundation-99ec`
- Base: `145af9cf0389fc0f21769e1f2e614e24d0ccd80e` (`145af9c`, PR 18 head). That commit already contains the off, unmounted tenant foundation `05c4644`.
- This work does not edit `book.py`, `gate_v2.py`, release packages, model bridges, engine core, or the owner UI.

The commit hash for this template is filled in after the commit is created. Search the latest commit on this branch whose subject mentions the customer web app.

## Progress

Working in the local template, with tests:

- Web signup and login, loopback only, sessions reused from `veteran_desk/tenant_foundation.py`.
- Isolated paper ledger and a chart drawn from that ledger.
- Reset to a customer-chosen starting capital. Default is the configured starting amount. New epoch P&L starts at 0. Old ledger is append-only. Open positions and pending orders block the reset. Zero capital warns and blocks new entries. Crash recovery, idempotency, and a race are tested. Reset does not touch the brain.
- Per-tenant brain with event and schedule updates, dedup, idempotency, error status, skip of one poison line, and a background worker loop. Lessons come from paper outcomes, research, and approved teachings. Candidates are not trading behavior. Rails are sealed. Model calls are not wired. Unconfigured providers are not described as running.
- Operator status view without customer secrets.
- Signed sandbox webhook contract. `commercial_paid` cannot become true.
- Education lesson authored for this template, plus outline-only topics. Starter pack is separate from the brain and makes no performance claim.
- Sector opt-out that still shows holdings and still allows exits.
- Broker mode names with an explicit grant. Live submit stays disabled.

## Not done

- Mounting this on the owner process or a public hostname.
- A real payment provider, charges, refunds of real money, invoices, or email.
- News delivery or redistribution.
- An outbound model call. The spend cap and secret ref are gates, not a client.
- Live brokerage.
- Marketing pages and a public leaderboard.
- Resetting the owner's current desk. The feature runs only on a customer store the caller opens.

## Commands

```bash
pip install -r customer_platform/requirements-customer-platform.txt
PYTHONPATH=. python -m pytest customer_platform/tests -q
PYTHONPATH=. python -m pytest veteran_desk/tests/test_tenant_foundation_20261106.py -q
PYTHONPATH=. python -m customer_platform.demo --root /tmp/cp-demo
CUSTOMER_PLATFORM_ENABLE=1 CUSTOMER_PLATFORM_SIGNUP=1 \
  CUSTOMER_PLATFORM_ROOT=/tmp/cp-demo \
  python -m customer_platform --host 127.0.0.1 --port 8765
```

Config names, with empty values: `customer_platform/config.example.env`.

Dependencies: Python 3.12 standard library, `pytest` for tests, and `veteran_desk/tenant_foundation.py` from this repo. A copy of that module is included under `dependency/` in the source archive so the tree can be read without the rest of the desk. The running import is still `veteran_desk.tenant_foundation`.

## Tests last run

- `customer_platform/tests`: 14 passed.
- `veteran_desk/tests/test_tenant_foundation_20261106.py`: 6 passed.

Negative cases in the customer tests: wrong tenant, live venue, float/NaN/negative/extra decimals, open position, pending order, zero capital without acknowledgement, idempotency conflict, race, three crash points, rails tamper, cross-tenant idea read, prompt injection, secret redaction, unsigned webhook, live-mode webhook, sector opt-out still allowing an exit, non-loopback bind refused, `/leaderboard` returns 404.

## Next steps for a later session

1. Keep the web app isolated. Do not import it from `thf_dash.py`.
2. If a payment provider is ever approved, add it behind the existing webhook verifier and still refuse `livemode: true` until that project exists.
3. A model client, if ever approved, has to pass the spend cap and must not mark `model_running` while the provider is unconfigured.
4. Marketing site and leaderboard are a separate project. They need an explicit consent decision before any customer figure is shown outside that customer's login.

## Archive

The runnable source archive and its SHA256 file are written under `/opt/cursor/artifacts/` when this session packs them. The archive contains customer source, tests, the example config, docs, and the tenant-foundation dependency. It does not contain runtime customer state, demo passwords, or provider keys.
