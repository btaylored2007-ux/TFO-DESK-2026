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
    from veteran_desk import pit as P
    live = os.path.join(C.ROOT, "data", "pit", "pit.sqlite")
    old = P.DB; P.DB = live
    try:
        with pytest.raises(PermissionError): P.con()
    finally:
        P.DB = old
    assert any("pit" in str(v) for v in C.VIOLATIONS[n:]), C.VIOLATIONS[n:]
    del C.VIOLATIONS[n:]
    tmp = tempfile.mkdtemp(); P.DB = os.path.join(tmp, "pit.sqlite")
    try:
        c = P.con(); assert c.execute("select count(*) from bars").fetchone()[0] == 0; c.close()
    finally:
        P.DB = old
    assert not os.path.isdir(os.path.join(C.ROOT, "data", "pit"))
    live_book = os.path.join(C.ROOT, "reports", "veteran_desk_book.json")
    try:
        assert open(live_book).read(1)   # a present live book may be read
    except FileNotFoundError:
        pass   # this checkout has no owner ledger; a missing file is not a blocked read
    except PermissionError:
        raise AssertionError("the guard blocked a read of the live book path")
