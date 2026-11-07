# ChatGPT stall increment — separate from the d601fe4 bundle

Paper only. Do not restart `tfo-thf-tick`. Do not delete `state/llm_queue` or the ChatGPT profile. This bundle does not include `book.py`.

Apply it only after the preflight of `deploy/incremental_20261106/thf-increment-20261106.tar.gz` (commit `d601fe4`) is finished or explicitly skipped. This archive does not replace that file.

## What the code does

`POST /ask` without `ack` still waits and returns the answer, as older clients expect. A broken pipe while writing that answer does not cancel the browser job. The result is kept under its `request_id`.

A second `POST` with the same `request_id` returns the stored result and does not queue another browser job. A poll of `GET /result?request_id=` returns `pending` or `done`.

`veteran_desk.chatgpt_bridge.ask` sends `ack: true` only when `/health` reports `delivery=ack_poll_v1`. It then polls. A dropped ack connection polls the same id and does not submit a second ask. An old bridge without that field keeps the previous blocking call.

A deadline that expires on the original waiting client still returns HTTP 504 and marks that request cancelled, which is the existing deadline contract. A retry that only attached to an in-flight id does not cancel it.

Chips: a fresh signed-in bridge with a good ask stays up. Busy stays busy. A failed or timed-out ask stays unknown. A delivered ask whose client disconnected stays up and says the result was not delivered. A Grok check older than five minutes stays unknown, including an old success.

## Commands

```bash
python3 deploy/chatgpt_stall_20261106/install.py --root /opt/tfo/live/thf
python3 deploy/chatgpt_stall_20261106/install.py --root /opt/tfo/live/thf --apply
python3 deploy/chatgpt_stall_20261106/install.py --root /opt/tfo/live/thf --rollback
```

`BLOCKED` refuses the whole apply. Recorded bases:

- `tools/chatgpt_web.py` `d1554bac584951c3829f04d852f6d6f0fb8ca909e4cf4ba440c5d1dd13769399`
- `tools/chatgpt_admission.py` `dceb94c59f426fb2a68f5fdd1f98e99df31de1c57dd860b1e481fef5d32addd8`
- `veteran_desk/chatgpt_bridge.py` `e094a13c119e311be0e79fcecf715ed0d7cc28656b7d0b923740c2cc868e268a`
- `veteran_desk/thf_chat_home.py` `b1184435dc3ccad8771133e59b26852d3ab184171ae35e383c7601f0119de7ff`

If `veteran_desk/book.py` is present and is not `b4453b503f33e34110519bf4d7a9bf391ecc2802edbd87acca3e77dc96b7e891`, the apply stops.

## Activation

Restart only the ChatGPT bridge process that serves `127.0.0.1:18891`, after the dry-run is clean. Do not clear its queue and do not delete the login profile. The desk client picks up `chatgpt_bridge.py` when that process starts again. No image-failure cause is claimed; no new user image was stored from 5 Nov through the 22:07 audit.

## Not in this fix

`tools/chatgpt_web.py` still drives one logged-in browser. This does not shorten the model’s own think time. A 169-second answer can still occur. The waiting desk client no longer has to hold that socket, and a disconnect does not create a second job for the same id.
