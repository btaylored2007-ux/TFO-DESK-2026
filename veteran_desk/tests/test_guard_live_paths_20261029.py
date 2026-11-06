import os, pytest, tempfile
import builtins, sys
C = sys.modules[builtins.open.__module__]   # the conftest instance that installed the guard
def test_guard_blocks_live_and_allows_tmp():
    n = len(C.VIOLATIONS)
    with pytest.raises(PermissionError): open(os.path.join(C.ROOT, "state", "veteran_desk_payouts.json"), "a")
    with pytest.raises(PermissionError): os.replace(os.path.join(tempfile.mkdtemp(), "x"), os.path.join(C.ROOT, "reports", "zz.json"))
    assert len(C.VIOLATIONS) == n + 2
    del C.VIOLATIONS[n:]   # expected probes, not real violations
    p = os.path.join(tempfile.mkdtemp(), "ok.json"); open(p, "w").write("{}"); assert open(p).read() == "{}"
    live_book = os.path.join(C.ROOT, "reports", "veteran_desk_book.json")
    try:
        assert open(live_book).read(1)   # a present live book may be read
    except FileNotFoundError:
        pass   # this checkout has no owner ledger; a missing file is not a blocked read
    except PermissionError:
        raise AssertionError("the guard blocked a read of the live book path")
