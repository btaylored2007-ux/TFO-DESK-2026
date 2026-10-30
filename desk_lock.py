#!/usr/bin/env python3
"""desk_lock.py — one advisory lock for every ledger writer. PAPER ONLY.

WHY THIS EXISTS
---------------
On 2026-08-28 a $7,894 position (CCP.AX) was OPENED, CONFIRMED, and then SILENTLY
DESTROYED about three minutes later.

The mechanism: `fundamental_book.mark()` is a READ-MODIFY-WRITE. It loads the
ledger, spends ~2 minutes fetching prices for 60 positions, then writes the whole
structure back. A 5-minute refresh job was marking on a timer. Any position opened
inside that ~2-minute window was present in neither the loaded copy nor the
written one — so the write reverted it, with no error, no conflict, and no trace
in either log. The position count simply went back down.

**That is the worst class of failure this operation has: a confirmed action that
quietly un-happens.** It was found only because a routine status read showed 59
positions where 60 had just been reported.

THE FIX
-------
Every process that writes a ledger takes this exclusive lock first. Readers do not
need it. The lock is held across the ENTIRE read-modify-write, not just the write,
because the window is the whole cycle.

Timeout is generous (default 900s) because a 60-position mark legitimately takes
minutes; a writer that cannot get the lock must FAIL LOUDLY rather than proceed
unlocked, since proceeding is exactly what caused the loss.
"""
import contextlib
import errno
import fcntl
import os
import time

BASE = os.path.dirname(os.path.abspath(__file__))
LOCKDIR = os.path.join(BASE, "reports")


@contextlib.contextmanager
def ledger_lock(name="fundamental", timeout=900, poll=0.5):
    """Exclusive advisory lock for one ledger family. Raises on timeout."""
    os.makedirs(LOCKDIR, exist_ok=True)
    path = os.path.join(LOCKDIR, ".%s.lock" % name)
    fh = open(path, "a+")
    start = time.time()
    while True:
        try:
            fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            break
        except OSError as e:
            if e.errno not in (errno.EAGAIN, errno.EACCES):
                fh.close()
                raise
            if time.time() - start > timeout:
                fh.close()
                raise RuntimeError(
                    "REFUSED: could not acquire the %s ledger lock within %ds. "
                    "Another writer (a mark, an entry, or a scheduled refresh) is "
                    "mid read-modify-write. Proceeding WITHOUT the lock is what "
                    "silently destroyed a confirmed CCP.AX entry on 2026-08-28, so "
                    "this fails instead." % (name, timeout))
            time.sleep(poll)
    try:
        fh.seek(0); fh.truncate()
        fh.write("pid=%d acquired=%s\n" % (os.getpid(),
                 time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())))
        fh.flush()
        yield
    finally:
        try:
            fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
        finally:
            fh.close()
