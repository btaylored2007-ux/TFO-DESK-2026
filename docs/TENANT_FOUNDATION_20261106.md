# Customer account foundation — staged, off

This is not a subscription launch and not a certification. The running desk remains one owner's paper engine. The tailnet gate is unchanged.

`veteran_desk/tenant_foundation.py` is not imported by `thf_dash.py`. `Store` raises unless the caller passes `enabled=True`. It refuses `/opt/tfo/live` and any root that already contains `veteran_desk/book.py`. There is no listen port, no public registration function, no default password, and no copy of the owner book.

A session is created only after a password check. The tenant id is read from that session. A tenant id in the call is rejected when it differs. Passwords are stored as scrypt hashes. Session tokens are stored only as SHA-256. Uploads, paper notes, and the audit log stay in that tenant's directory. Roles are viewer, member, and tenant admin. A tenant admin cannot create another admin. A viewer cannot upload. Nothing in this module reads provider keys, browser profiles, or customer prompts from the owner tree, and it does not place a broker order.

## Later migration, not this change

The owner book stays `veteran_desk/book.py` with hash `b4453b503f33e34110519bf4d7a9bf391ecc2802edbd87acca3e77dc96b7e891`. A future cutover would create a new tenant record and would not import that book automatically. Import, if ever approved, would be a separate reviewed copy into that tenant's own directory. Provider credentials would stay with the owner process, not be shared into customer stores.

## Not built

Billing, tax, public signup, a second HTTP port, and customer activation. Nginx and the host firewall are being read elsewhere; this checkout still does not contain the installed nginx file or the firewall rules.

No archive is produced. Copying this file onto the droplet is not authorized until the access review says the owner path still works and this module is still unmounted.
