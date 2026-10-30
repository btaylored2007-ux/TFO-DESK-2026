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
    assert open(os.path.join(C.ROOT, "reports", "veteran_desk_book.json")).read(1)   # reads allowed
