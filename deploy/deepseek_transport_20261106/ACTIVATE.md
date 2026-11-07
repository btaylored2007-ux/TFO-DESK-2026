# DeepSeek transport classification

Apply after runtime `b078846`. Do not apply `deploy/role_align_20261106/thf-role-align-20261106.tar.gz`. The live seat registry hash is still `44455cc12c2d81df2b1aa176b8094ecfe1538744662b2057d64b71947bf83de9`. The sanitized config `allowed_seats` is already the same 18 ids as `lesson_trace.SEATS`.

Reviewed base for the only replaced file:

- `veteran_desk/deepseek_bridge.py` `b632ddeeea08bdd2d9c036767871b99f57a156a32c7890a9e6eb707eb38d6175`

The previous worker stored `transport` plus the exception class and dropped the cause. A stored `transport ValueError` then made the next seat raise that same text without a new request. Account quota is unchanged: HTTP 402 and 429 still cool or block. A connection failure is `transport_ECONNREFUSED` or another errno name. A non-JSON body is `response_not_json`. Exception text is not stored. The next seat is allowed to try. The chip still shows the latest transient failure until a success.

`book.py` `b4453b503f33e34110519bf4d7a9bf391ecc2802edbd87acca3e77dc96b7e891` is not copied. Do not restart `tfo-thf-tick` or `tfo-chatgpt-web`. The shadow oneshot picks this file up on its next timer. `seven_day` stays not started until one task has real answers from all four providers.

The supplied archive did not include the shadow row or the DeepSeek receipt. If the next failure is still not one of these safe codes, collect only these fields, with no key file and no prompt text:

- `state/thf/shadow_samples.jsonl` rows whose `provider_requested` is `deepseek_api`: `error`, `availability`, `seat`, `utc`
- `state/thf/deepseek_api/requests.jsonl`: `event`, `reason`, `ok`, `seat`, `utc`
- `state/thf/deepseek_api/status.json`: `failure`, `http_status`, `blocked`, `last_success_at`
