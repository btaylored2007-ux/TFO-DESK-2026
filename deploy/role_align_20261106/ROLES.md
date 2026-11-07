# Do not apply this archive

`thf-role-align-20261106.tar.gz` was built before the live correction. Leave it unapplied.

The droplet registry file is still `44455cc12c2d81df2b1aa176b8094ecfe1538744662b2057d64b71947bf83de9`. The sanitized DeepSeek config `allowed_seats` is already the same 18 ids as `lesson_trace.SEATS`, including `software_engineer` and `cfo`. The chip reported 18 authorised seats. This checkout has no staff persona files, so a local import shows 16. That is not the production roster.

The DeepSeek failure after runtime `b078846` is the transport classification in `deploy/deepseek_transport_20261106/`. Seat identities are unchanged.
