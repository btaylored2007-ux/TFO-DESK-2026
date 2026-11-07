# Loopback dashboard bind

Nginx on `100.120.8.11:18870` already proxies to `127.0.0.1:28870`. In-repo readers (`dash_context`, `thf_home`, `thf_guardian`) use that same loopback URL. UFW on the host denies public inbound 28870. This change makes the app bind `127.0.0.1` unless `THF_DASH_BIND` is set. It does not change `THF_DASH_PORT`, nginx, or UFW.

Do not restart `tfo-thf-tick`. Restarting `tfo-dash-thf` is what picks up the bind. The owner URL stays `http://100.120.8.11:18870/`.

The unit file `/etc/systemd/system/tfo-dash-thf.service` was not in the handoff. The running process is on 28870, so that unit sets the port. DigitalOcean's cloud firewall was not visible from the host.

Bases for `veteran_desk/thf_dash.py`:

- `e77d367e97f6a149ef0890f49262d0599753e6fac85efceaeb944158d133e745` (last captured live file)
- `310f395d2c8b9440d67be67a38549956a33ba971b4998836511a25ed0a51247e` (repo file before this bind)

`check_pin` stays a no-op. The book is not copied. The customer account module stays unmounted.
