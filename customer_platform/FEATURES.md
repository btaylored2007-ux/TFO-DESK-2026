# Feature matrix

Customer web template on `cursor/customer-platform-foundation-99ec`. A passing test is not product completion. There are no production customer conversations here, and owner chat logs were not imported. Live prices, news, charges, and outbound provider calls are not relabelled as unconnected when the HTTP control is missing: the table says unimplemented only where no code path exists.

| Feature | HTTP / worker | Evidence | Still blocked |
|---|---|---|---|
| Signup, login, tenant isolation, CSRF | Implemented | `test_isolation_web.py`, `test_http_product.py` | Public hostname. Trial hosting is hold. |
| Paper replay engine | Implemented. Start without a source stays stopped. Arming synthetic replay then Start runs a tenant worker that fills paper orders at a synthetic mark. The page does not say Running. | `test_http_product.py` | A licensed live price feed is absent. That is a connection blocker, not a missing engine. |
| Reset, ledger, school v1.2.1 | Implemented. Older course files and rubric snapshots stay put. | `test_reset_brain.py`, `test_school_draft.py` | Expert, legal, and compliance review. Course is unpublished. |
| Synthetic exercise | Implemented. In-sample-chosen lookback only. | school tests and HTTP CSV/results checks | Not a market edge. |
| Teach pipeline | Implemented on HTTP: draft, text-file upload, version list, paper-test only when this account has a fill, review, activation, rollback. | `test_http_product.py`, `test_teach_journal.py` | AI interpretation is unavailable. A form cannot mark a test that has no fill. Rails are unchanged. |
| Ask | Implemented on HTTP: save, retry, cancel, provenance, one lead answer, folded reviews, raw record. No outbound call. | `test_http_product.py`, `test_requests.py` | A real reply needs the customer's own key and a reviewed connection. No fixture is presented as a provider answer. |
| Own keys | Implemented. `/keys` shows masked last4 only after a real store. Missing cryptography or data key refuses the save. | `test_http_product.py` | `CUSTOMER_PLATFORM_DATA_KEY` is an operator server secret. No network test is sent. |
| Activity, B grades, trader entries | Implemented on the desk. | `test_http_product.py` | They do not bypass stops, freshness, or the price source. |
| Operator incidents | Implemented on `/admin` for an operator session: queue, ack, recheck, verify, propose, escalate. | `test_http_product.py` | External send is off. No engineer is running. Customers do not see the queue. |
| Charges, live orders, news, public leaderboard | Not built. Routes stay off or 404. | web tests | Separate approvals. Do not invent them. |
