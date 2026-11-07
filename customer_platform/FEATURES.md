# Feature matrix

Customer web template on `cursor/customer-platform-foundation-99ec`. A passing test is not product completion. There are no production customer conversations here, and owner chat logs were not imported. Live prices, news, charges, and outbound provider calls are not relabelled as unconnected when the HTTP control is missing: the table says unimplemented only where no code path exists.

| Feature | HTTP / worker | Evidence | Still blocked |
|---|---|---|---|
| Signup, login, tenant isolation, CSRF | Implemented | `test_isolation_web.py`, `test_http_product.py` | Public hostname. Trial hosting is hold. |
| Paper replay engine | Implemented. Start stays disabled until a synthetic replay is armed. The worker fills paper orders that carry a stop. Headline does not say positions are protected while prices are unconnected. | `test_http_product.py`, `test_provider_risk.py` | A licensed live price feed is absent. |
| Reset, ledger, school v1.2.1 | Implemented. Older course files and rubric snapshots stay put. | `test_reset_brain.py`, `test_school_draft.py` | Expert, legal, and compliance review. Course is unpublished. |
| Synthetic exercise | Implemented. In-sample-chosen lookback only. | school tests and HTTP CSV/results checks | Not a market edge. |
| Teach pipeline | Implemented. A synthetic paper test is stored against that skill version. An unrelated account fill cannot advance it. A profit is not a pass. Human attestation is labelled as attestation. | `test_teach_journal.py`, `test_http_product.py` | AI interpretation is unavailable. The result is not a live-competence claim. |
| Ask | Implemented. Per-tenant provider jobs use fixed official endpoints, a host check, bounded retries, cancel, timeout, and secret redaction. A mock transport can return a labelled synthetic fixture in tests. The customer answer is not filled from that fixture. | `test_provider_risk.py`, `test_http_product.py` | External connection proof is unconnected until an authorised customer credential and an enabled send exist. No operator key is used. |
| Own keys | Implemented. `/keys` shows masked last4 only after a real store. Missing cryptography or data key refuses the save. | `test_http_product.py` | `CUSTOMER_PLATFORM_DATA_KEY` is an operator server secret. No network test is sent. |
| Activity, B grades, trader entries | Implemented on the desk. | `test_http_product.py` | They do not bypass stops, freshness, or the price source. |
| Operator incidents | Implemented on `/admin` for an operator session: queue, ack, recheck, verify, propose, escalate. | `test_http_product.py` | External send is off. No engineer is running. Customers do not see the queue. |
| Charges, live orders, news, public leaderboard | Not built. Routes stay off or 404. | web tests | Separate approvals. Do not invent them. |
