# FAILED_COPY receipt

The packaged installer left a file half-copied. `apply_plan` restored only copies it had already marked finished. A replace whose payload copy raised after writing the destination stayed on the new bytes. A new file written just before the same kind of error was left in place. The book was not touched.

The fix restores that in-progress replace from its backup, deletes an in-progress new file, then restores every earlier finished replace. `main` prints `APPLY_REFUSED`, then `FAILED_COPY <path>: <error>`, then `NOT_DEPLOYED`. Production-root refusal is the same function as `deploy/lesson_caller_20261107/install.py`.

`veteran_desk/tests/test_lesson_failed_copy_20261107.py` loads `deploy/lesson_final_20261107/install.py`. One case raises on the second payload copy after that copy has written. The other raises on `veteran_desk/feed_status_facts.py` after that new file has been written and after the replaces have succeeded. Both leave every pre-existing file on its captured base, leave the new file absent, leave the book at `b4453b503f33e34110519bf4d7a9bf391ecc2802edbd87acca3e77dc96b7e891`, and write no receipt.

Payload bytes are the same files as archive `300f0559e1329f6244dfe46b516f63f63993f25996efa774787974c140afbdfc`. This archive changes the installer and this receipt. It was not applied. `tfo-thf-tick` was not restarted.
