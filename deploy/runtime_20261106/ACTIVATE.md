# Runtime fix activation

This bundle does not include the earlier main archive. It copies four files when their live hash is the reviewed base. `book.py` `b4453b503f33e34110519bf4d7a9bf391ecc2802edbd87acca3e77dc96b7e891` is not copied. The installer does not restart a service, clear a queue, or clear a cooldown.

Reviewed bases:

- `veteran_desk/thf_shadow_sampler.py` `27d49d68c98b9d1691bc0f0881e77f1e7796e2af6fdc5c658943859f5e8668a4`
- `veteran_desk/thf_grok.py` `382b1f23764ad09cff4581d9ac1e17cf74670a05b5c05b857f7ec7428ae64646`
- `veteran_desk/deepseek_bridge.py` `ac5e6024b7e4bb935237a911bc1707b58f7518cffba37c8be3075890cd232989`
- `deploy/incremental_20261106/tfo-thf-shadow.service` `0d56c6dce437149a2c1429f1c42f9db881a3a568483ff8bb6d887d4b747c8aff`

## Order

1. Dry-run `python3 install.py --root /opt/tfo/live/thf`. Apply only when the book line is `ALREADY_PROTECTED` and no line is `BLOCKED`.
2. As root, dry-run `python3 migrate_status_owner.py --root /opt/tfo/live/thf`, then `--apply` if it lists `CHOWN`. It changes owner to `tfo:tfo` and leaves the mode alone. It does not read file contents.
3. Install the unit over `/etc/systemd/system/tfo-thf-shadow.service` and run `systemctl daemon-reload`. Do not restart `tfo-thf-tick`. Do not restart `tfo-chatgpt-web`. Do not start the shadow service by hand if a run is already active; the next timer uses the new user.
4. Roll back the four files with `python3 install.py --root /opt/tfo/live/thf --rollback`. That does not undo the chown and does not restore the systemd unit. Put the previous unit back by hand if the new user must be removed.

The new unit sets `User=tfo`, `Group=tfo`, and `HOME=/home/tfo`. The sampler also exits before writing when it is started as root.

ChatGPT shadow calls use the existing 280 second bridge budget, capped at 300, so an Extra High answer near 169 seconds can finish and the ack poll can collect it. A bridge that is down, restarting, or already busy is not given a second POST. The same prompt keeps one request id.

## Not claimed

No provider is marked up by this package. The seven-day clock stays `not_started` until a stored row has a real answer from all four providers, and even that row is not a seven-day result. Sixteen of the eighteen seats remain pending when a run takes the configured two jobs. The picture upload fault was not reproduced. Pages that returned 200 are `/`, `/hub`, `/classic`, `/desk/`, `/desk/chat`, and `/desk/phone`. `/api/chat_img` is the upload route, not a page.
