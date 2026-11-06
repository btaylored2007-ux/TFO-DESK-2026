# Security and reliability audit — 6 Nov 2026

This is a single-owner paper desk on a private tailnet. It is not a multi-customer product. Tailnet reachability is the access gate in the nginx excerpt. A browser 403 from outside that network is that gate working. The cause of any particular 403 was not re-checked from this checkout, and the gate is not changed here.

## What the code shows

| Area | Evidence | Boundary |
|---|---|---|
| Network gate | `nginx_safe/tfo-dash.conf.excerpt` listens only on `100.120.8.11` for ports 18860, 18870, and 18812. No `auth_basic`. | One private desk. Not an account system. |
| Mutations | `thf_http.mutation_error` rejects `Sec-Fetch-Site: cross-site` and a foreign `Origin`. A request with no `Origin` is not rejected. | Enough for the owner's own browser and curl on the tailnet. Not a login. |
| Sessions | `thf_dash.py` sets no cookie and has no session id. `thf_funder.check_pin` always returns true; the PIN was removed 2 Nov. The nginx comment that says PIN-checked is stale. | Anyone who can open the tailnet can post as the funder. |
| Uploads | `POST /api/chat_img` is store-only, magic-byte checked, 8MB, id pattern `ci-…`. `img_read` refuses a path that leaves the directory. GET of an id is a normal GET under `location /`. | Images are not separated by customer. The 21:19 design upload matched its hash. The later missing screenshot was not that file. Phone browser use is still unchecked. |
| Broker | `paid_feeds.py`, `pit_fx_sgp.py`, `oanda_sleeve.py`, and `desk_doh.py` name `api-fxpractice.oanda.com` and do not assign `api-fxtrade`. `oanda_sleeve.py` can POST `/orders` to that practice host. The THF book path logs `paper_order` with `paper_only`. | Practice-host orders are not a retail live account. This checkout does not prove the live process never calls the sleeve. |
| Learning | Trace separates included, cited, and validated. A cited close is an outcome link, not an edge. Six admins have no traced turn. | Not a finished learning result. |
| Seven-day | The comparison clock can be started by two matched tasks. The review time is later. | Starting the clock is not a seven-day result. |
| Book | Installers refuse a destination whose `book.py` hash is not `b4453b503f33e34110519bf4d7a9bf391ecc2802edbd87acca3e77dc96b7e891`. | The protected paper book is not edited by these increments. |

## P0 before any customer subscription

Not a small patch. Do not describe the current desk as tenant-isolated.

- No customer account, password, or session separate from the owner tailnet.
- No per-customer book, upload store, or audit identity.
- No billing, plan, or payment record.
- No public authentication in front of the dashboard.

## P1, still one owner

- Posts without `Origin` are accepted. That matches tailnet curl. It is not a CSRF token.
- Chat images are readable by anyone on the tailnet who has the id.
- `oanda_sleeve.py` order POST is practice-host only. Whether the running THF process calls it was not observed here.
- Python listens on `0.0.0.0` inside `thf_dash.py`. The nginx excerpt binds the public ports to the tailnet address. Firewall rules in front of `28870` were not in this checkout.
- Restart durability of the comparison clock is in the clock bundle now being deployed. This audit does not redeploy it.

## Not a code defect

- Full seven-day coverage, citations, and real outcomes are still waiting on later matched rows and real admin use.
- A 403 from a non-tailnet browser is not fixed by opening the gate.

## Paths not in this checkout

Do not guess these. Read-only, if a later pass needs them:

- The installed nginx file, to confirm it still matches `nginx_safe/tfo-dash.conf.excerpt`.
- The host firewall for `127.0.0.1:28870` and `0.0.0.0:18870`.
- Whether the live THF process imports `oanda_sleeve`.

## Scope of this upgrade

In scope: paper book protection, feed honesty, shadow comparison, learning trace honesty, provider labels, chat upload limits, and the Transparent Trading paper shell.

Out of scope until a separate design: subscriptions, billing, customer tenants, a public login, and a live retail broker.
