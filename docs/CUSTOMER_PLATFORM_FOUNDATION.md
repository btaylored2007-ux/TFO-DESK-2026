# Customer web app foundation

This is a local template for the first customer product: a hosted web app with signup, login, and an isolated practice desk. It is not a desktop app, not an installer, and not a self-host distribution. A marketing site and a public leaderboard are later work. They are not built here, and private performance is not published.

The package is off unless a caller passes `enabled=True`. The process listens only on loopback, and only when `CUSTOMER_PLATFORM_ENABLE=1`. It is not imported by the owner desk. It does not edit `book.py`, `gate_v2.py`, release packages, model bridges, or the owner UI.

## What works offline

- Signup and login for a subscription, with the existing scrypt passwords and tenant-scoped sessions. Signup stays closed unless the process sets `CUSTOMER_PLATFORM_SIGNUP=1`. That flag is the web-app flow. It is not a public website.
- Isolated paper ledger, chart drawn from that ledger, sector opt-out that still shows open holdings and still allows exits.
- Reset of practice capital to a customer-chosen starting amount. The default is the account's configured starting capital. The new epoch's performance P&L starts at 0. Old fills, currency, FX, and cost basis stay on the append-only ledger. Open positions and pending orders block the reset. Zero capital is allowed only with an explicit warning, and it blocks new entries.
- A per-tenant learning brain. It appends lessons from that tenant's realized paper outcomes, research, and explicitly approved teachings. Runs are event-driven or scheduled, deduped, and idempotent. A run with no new evidence stays idle and does not claim an update. Resetting capital does not change the brain files.
- Central operator view of subscription status without customer secrets, research text, or provider values.
- Signed test webhooks for sandbox payment state. `commercial_paid` stays false.

## Interfaces only

- Payment provider hosted checkout. The page is a test contract. It cannot charge a card. A sandbox state changes only after a signed `livemode: false` event.
- Model providers. A secret ref can be declared. The value stays in the process environment. Calls stay disabled and unconfigured providers are not described as running. Spend is capped and the outbound client is not wired.
- News. Default blocked. A license note can be stored and still does not deliver or redistribute a feed.
- Broker modes `manual`, `approval`, and `auto` can be named after an explicit grant. `submit_live_order` stays disabled. Venue stays paper unless a test tampers the file, and a live venue cannot be reset or used to trade.

## Reset semantics

Practice capital and epoch P&L are different numbers. After a reset the open epoch's capital is the chosen starting figure. Its P&L is 0 because that epoch has no fills yet. Historical realized P&L is the sum of fill rows and is not rewritten. A balance adjustment is marked `not_pnl`.

The reset refuses a non-finite, negative, or over-precise amount, and it refuses floats. The template bound is 1,000,000.00 in USD, EUR, GBP, and AUD, and 100,000,000 in JPY. Currency does not change.

## Learning brain

Lessons are candidate records until a person accepts one. Acceptance records a paper hypothesis. It does not place an order, enable a short, or edit the sealed rails. Submitted text is stored as data. Injection text is quarantined and is not copied into the lesson statement. Unit tests do not prove that a model learned anything. `model_running` is false in this template.

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

The demo writes a password only into that data directory. It does not reset the owner desk and it does not charge a card. Copy `customer_platform/config.example` for the variable names. Leave the values empty in the repo.

## Tests recorded with this template

`customer_platform/tests`: 14 passed. Coverage includes wrong tenant, live venue, idempotent retry, a two-thread race, crash after intent, crash between the two file replaces, crash before commit, byte-stable brain and knowledge across reset, poison-line error and explicit skip, prompt injection, cross-tenant reads, secret redaction, unsigned and live-mode webhooks, sector opt-out, and the loopback signup page. `test_tenant_foundation_20261106.py`: 6 passed.

## Still open

No production mount, no public hostname, no email, no tax invoice, no real checkout provider, no news delivery, no outbound model, no live broker, no marketing site, and no public leaderboard. Customer consent to publish performance does not exist because nothing is published.
