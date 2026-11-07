# Source flow

This is the customer app path. A passing test is not production acceptance. A scratch HTTP server is not a reply from OpenAI, Anthropic, xAI, or DeepSeek.

## Provider client

Implemented adapters, each with one fixed HTTPS URL:

| Provider | Host | URL |
|---|---|---|
| openai | api.openai.com | https://api.openai.com/v1/chat/completions |
| anthropic | api.anthropic.com | https://api.anthropic.com/v1/messages |
| grok | api.x.ai | https://api.x.ai/v1/chat/completions |
| deepseek | api.deepseek.com | https://api.deepseek.com/v1/chat/completions |

No other host is called. The customer key is encrypted with the server data key and sent only as a request header. It is not stored in the job body. There is no owner-key fallback.

Public egress stays off unless the operator sets `CUSTOMER_PLATFORM_PROVIDER_EGRESS=allow`. That variable is not set by this app. A test may install a scratch opener. The scratch opener is not public egress and its reply is not provider acceptance.

Send stays off until all of these are true:

1. The server data key is present, so a customer key can be stored.
2. This login has stored its own key for that adapter.
3. The customer confirms `Send to my provider`.
4. Paper replay has been started.
5. Either the scratch opener is installed for a test, or public egress is allowed.

The recorded reason distinguishes those states. A stored key with send off is `provider client is configured; outbound send is off`, not `no customer credential`.

The model may return only `symbol`, `side`, `stop_percent`, and `market`. A price, a feed, an approval, a non-finite stop, an oversize body, or a hostile teaching is refused. The engine sets the limit from the synthetic print and the stop from the reviewed percent. The model cannot activate the skill.

Redirects are refused. `product_urlopen` does not follow 301, 302, 303, 307, or 308, so the customer key is not sent to a second host, an HTTP URL, or another port. A scratch reply that is only a redirect is not treated as a decision.

The provider call is outside the tenant lock. Before the call, the worker records the account epoch and the print. Stop, a trader change, a new print, a skill change, funds, and reset each take the same lock briefly and advance the epoch. When the reply returns, the worker locks again and places an order only if that epoch, the entry switch, the trader, the print, the active skill version, and the cash check are unchanged. A late reply after Stop is recorded as cancelled and does not open a position. Stop management of an open position still uses the current print.

A written `buy SYMBOL` plus `stop N percent below entry` rule can still place one paper order. That path is `strategy_rule`. It is a limited fallback, not the provider path.

## Lock

Orders, fills, teaching, uploads, reset, funds, stop distance, start, stop, trader entries, activity, arming, the replay print, and the fast part of a paper tick take `tenants/<id>/writer.lock`. The provider network call does not hold that lock. A second process gets `writer busy` and does not write. The same thread may re-enter. The kernel releases the lock if the process dies. Pending buy notional is reserved. Reset still refuses while a position or order is open. A cancelled provider attempt stays in `decision_audit.jsonl`.

## What remains unproved

A real customer key and a real provider reply. Licensed prices, news, charges, live orders, external alerts, and a public host. Publishing the 21-module course. The brain's own model slot stays unwired and cannot place an order.
