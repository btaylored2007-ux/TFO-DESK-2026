# Test inventory

Run from the repository root, or from a self-contained export that contains this package plus `veteran_desk/tenant_foundation.py` and `veteran_desk/thf_dash.py`:

```bash
pip install -r customer_platform/requirements-customer-platform.txt
PYTHONPATH=. python -m pytest customer_platform/tests veteran_desk/tests/test_tenant_foundation_20261106.py -q
```

No network is required. These tests do not call a market, a payment provider, or a model.

| Set | File | Role |
|---|---|---|
| Original 6 | `veteran_desk/tests/test_tenant_foundation_20261106.py` | Owner-tree isolation of the dormant account store. Needs the real `thf_dash.py` beside it. |
| Original 14 | `customer_platform/tests/test_reset_brain.py` and `test_isolation_web.py` | Reset, brain, payments, web login. |
| Added | `test_school_draft.py`, `test_teach_journal.py`, `test_requests.py`, `test_http_product.py`, `test_provider_risk.py`, `test_http_runtime.py`, `test_workflow_contract.py` | Course draft, Teach, questions, HTTP product, provider jobs, stop management, and the labelled workflow. |

Intentionally not in this export: owner `book.py` runtime, live broker, payment credentials, news licences, and the rest of the 71-file course workspace. The course files here are the JSON, QA, review, changes, and evidence register.
