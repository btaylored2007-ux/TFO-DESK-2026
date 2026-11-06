# Commercial readiness gaps

Transparent Trading is the display name for a paper desk that may later be offered to retail subscribers. This note is not a launch checklist and not evidence that a subscription can be sold.

Not in this release:

- Billing, plans, tax, or payment receipts. Not started.
- A customer account product. `veteran_desk/tenant_foundation.py` is source only, off unless a caller passes `enabled=True`, and the dashboard does not import it. There is no revoke, no billing, and no public registration.
- Live-money orders. OANDA code that is present uses `https://api-fxpractice.oanda.com`. The host audit says the practice timers are enabled. That is not a live-money connection, and this checkout did not prove the running THF process imports `oanda_sleeve`.
- A public marketing site, domain registration, or trademark filing. Not started.
- A claim that tests in this repository prove the service is safe to charge for.

The private desk is tailnet-gated. Host UFW denies inbound 28870. That does not make it a multi-customer product. A PIN is not enforced.

Still required before anyone could treat the desk as a product: the Singapore runtime is tailnet-gated and was not verified in a browser from here; the matched AI sample has not started; several seats have stale lesson files; feed health is coded and unit-tested, not observed on the droplet; the package book now matches the live +0.7R file, and a destination with any other hash is refused by the guarded installer. This is not a launch.
