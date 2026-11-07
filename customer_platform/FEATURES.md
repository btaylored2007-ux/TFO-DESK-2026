# Feature matrix

Customer web template on `cursor/customer-platform-foundation-99ec`. A tested row is still blocked where the dependency says so. The 36-test run does not make the course or the app finished. There are no production customer conversations in this package. Owner-desk chat logs were not imported.

| Feature | Working | Test | Blocked |
|---|---|---|---|
| Signup, login, isolated tenant | Loopback, flag-gated | `test_isolation_web.py`, foundation 6 | Public hostname. Trial hosting is hold. |
| Paper ledger, chart, reset | Chosen starting capital, epoch P&L separate, append-only ledger | `test_reset_brain.py` | Not run on the owner desk. Prices in the demo are fixtures. |
| Start and Stop | Server gate. Stop headline is "Stopped · positions protected." | teach/journal start test, web desk | Start stays off without a fresh authorised price feed. Test probe is not a live feed. Engine sends no orders. |
| Private brain | Extracts from this tenant's paper records | brain tests in the reset and isolation files | No model call. |
| Own API keys | Encrypt-at-rest module, fail closed without a data key | Not in the 36-test run | No outbound call. Not a licence resale. |
| School v1.2.1 | Unpublished draft. 21 modules, 126 lessons, 287 questions. v1.0.0 through v1.2.0 stay immutable. An attempt keeps its course version and its rubric snapshot. | `test_school_draft.py`, web lesson GET | Expert, legal and compliance review. 23 lessons were fully read and 105 were claim-scanned. 11 modules have no pool B. Not published. |
| Synthetic exercise | Invented series, seed 20261107, CSV download for the logged-in tenant. The page shows the in-sample-chosen lookback, not the best out-of-sample row, and not a lesson Sharpe example. | The stored results match a fresh run of `compute_backtest.py` | Not market data and not an edge. Evidence extracts are not a customer download. |
| Upload Skills and journal | Draft save, status machine, unscored notes | `test_teach_journal.py` | AI reading is unavailable. Practical scores are not computed. A skill does not complete the school. |
| Operator incidents | Operator file only | teach/journal incident test | External notification is off. No engineer is running. |
| Customer questions | One lead reply, independent reviews folded, critical events kept, retry keeps the raw record, late or cancelled text is not the answer, reply text does not act | `test_requests.py` | No provider is connected. This is not the owner THF chat. |
| Public leaderboard, charges, live orders, news | Not built | `/leaderboard` is 404 | Separate approvals. |
