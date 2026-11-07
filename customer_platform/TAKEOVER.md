# Takeover — customer web app template

Another session can continue from this branch without the owner desk. Do not SSH, deploy, or edit production. The coordinator copies the source archive onto Singapore outside the production tree.

## Approval and course draft (2026-11-07)

Owner t2845u approved practical exercises, a private journal, mistake reviews, and Upload Skills. Those are in this package. The journal is stored and is not scored. Upload Skills saves the customer's words; AI interpretation stays unavailable until a provider is connected. Neither path is a promise of results.

Course file now loaded is v1.2.1, which supersedes v1.2.0. v1.0.0, v1.1.0, v1.1.1 and v1.2.0 stay byte-for-byte so an old attempt still grades on that course and that rubric snapshot. The draft has 21 modules, 126 lessons and 287 questions. Question ids and correct options are unchanged. Lesson prose is 22,275 words, and that count is not a completion rule. The Module 8 CFD close-out in v1.2.1 is 5% of the A$20,000 notional, not a 10% wipeout. A stored v1.2.0 rubric keeps the earlier wording. Expert, legal and compliance review are open. The conceptual review read all 287 questions and all 21 rubrics, fully read 23 lesson bodies, and claim-scanned the other 105. 112 numeric checks passed. That is not certification. APRA minima are unverified. Missing alternate pools are a limitation, not an acceptance rule. A nine-loss figure of 0.3053 is the Markov result for 120 independent trials with loss probability 0.58, not a market forecast.

The synthetic worked series ships under `school_content/v1.2.0/datasets/`. It is labelled SYNTHETIC, seed 20261107. The customer page can open that exercise and the two CSV files. It is not a market result and not an edge. The content archive hash is `7be8320c4297397dabb6dde7d79fc67e317b2b16d715b512b042abf04a9d7a06`.

## Customer questions (2026-11-07)

The customer app had no chat. It still does not call a model, and it does not reuse the owner THF threads. `/ask` saves a question for that customer and shows retry, cancel, provenance, and the raw record. Without that customer's own key, the job stays unconnected and no answer is invented. A test-only synthetic reply can be injected in process; the HTTP app does not do that. One lead reply is the answer. Independent reviews stay behind an expand. A critical event is kept. A late or stale reply is stored and is not the answer. The same delivery key is a retry. Reply text does not place an order. This shape is a proposed customer view, not a new trading rule. Owner-desk chat fixes stay on the owner review.

Human School progress and Teach my traders are separate pages. There is no customer health screen. The trial is not delivered.

Research claims from five vendors are archived under `customer_platform/research/`. They are vendor documents, not an independent benchmark. QuantConnect already documents bring-your-own-key on a paid Agent Node, with the tier boundary unclear, and documents stopping without liquidating. This app does not claim those ideas are unique, and it does not claim to outperform those products.

## What the next session should do

1. Do not publish the school. Expert, legal and compliance review are open, 11 modules have no pool B, and a human reread of inherited conceptual items is still open.
2. Keep external incident alerts off until a channel and credential exist.
3. Do not open a public port or restart the owner desk.

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
- Template commit: `e4f88dc3968717598e4b58cadda0593b27e8a649`
- This work does not edit `book.py`, `gate_v2.py`, release packages, model bridges, engine core, or the owner UI.

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

Config names, with empty values: `customer_platform/config.example`. The repo ignore rule skips `*.env`, so the example does not use that suffix.

Dependencies: Python 3.12 standard library, `pytest` for tests, and `veteran_desk/tenant_foundation.py` from this repo. A copy of that module is included under `dependency/` in the source archive so the tree can be read without the rest of the desk. The running import is still `veteran_desk.tenant_foundation`.

## Tests last run

- Combined command in `TEST_INVENTORY.md`: 41 passed (35 customer tests and the original 6 foundation tests). No network. That count is not acceptance and not a claim that the course or the app is finished.

Negative cases in the customer tests: wrong tenant, live venue, float/NaN/negative/extra decimals, open position, pending order, zero capital without acknowledgement, idempotency conflict, race, three crash points, rails tamper, cross-tenant idea read, prompt injection, secret redaction, unsigned webhook, live-mode webhook, sector opt-out still allowing an exit, non-loopback bind refused, `/leaderboard` returns 404.

## Next steps for a later session

1. Keep the web app isolated. Do not import it from `thf_dash.py`.
2. If a payment provider is ever approved, add it behind the existing webhook verifier and still refuse `livemode: true` until that project exists.
3. A model client, if ever approved, has to pass the spend cap and must not mark `model_running` while the provider is unconfigured.
4. Marketing site and leaderboard are a separate project. They need an explicit consent decision before any customer figure is shown outside that customer's login.

## Archive

The earlier foundation archive `customer-platform-foundation-source.tar.gz` stays in place. Its sha256 is `cad96a532314b6321c35dffa03d14a9620b8f1d4f4a779fbb80486ac087cf873`. Do not overwrite it.

The current self-contained export is `customer-platform-final-20261107.tar.gz`. It contains this package, the rendered screenshots under `customer_platform/design_evidence/`, school versions 1.0.0 through 1.2.1, the synthetic dataset files, `veteran_desk/tenant_foundation.py`, and `thf_dash.py` with `thf_http.py` so that module imports. It does not contain `book.py` or `gate_v2.py`. The sha256 is the sidecar file next to that archive. It has no demo password and no provider key. Neither archive is a trial deployment.
