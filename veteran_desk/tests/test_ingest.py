import csv, json, os, sqlite3, sys, tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from veteran_desk import ingest_staging as I
H = ["symbol", "date", "open", "high", "low", "close", "volume", "source", "available_at_utc", "delisted_flag"]
def mk(rows, name="a.csv"):
    d = tempfile.mkdtemp(); g = os.path.join(d, "grp"); os.makedirs(g); w = csv.writer(open(os.path.join(g, name), "w", newline="")); w.writerow(H); [w.writerow(r) for r in rows]
    json.dump(dict(group="grp", survivorship="UNIVERSE_BIASED"), open(os.path.join(g, "manifest.json"), "w")); return d
OK = ["AAA", "2024-01-02", 10, 11, 9, 10.5, 100, "t", "2024-01-02T21:00:00Z", "false"]
def test_good_merges_and_dupes_dropped():
    d = mk([OK, OK, ["AAA", "2024-01-03", 10, 11, 9, 10, 1, "t", "2024-01-03T21:00:00Z", "true"]]); db = os.path.join(d, "x.sqlite")
    r = I.run(stage=d, db=db); f = list(r["groups"]["grp"]["files"].values())[0]; assert f["status"] == "OK" and f["rows"] == 2 and f["n_soft"] == 1
    c = sqlite3.connect(db); assert c.execute("select status from universe where sym='AAA'").fetchone()[0] == "delisted"
def test_leak_quarantines():
    d = mk([OK, ["BBB", "2024-01-05", 1, 1, 1, 1, 1, "t", "2024-01-04T21:00:00Z", "false"]]); r = I.run(stage=d, db=os.path.join(d, "x.sqlite"))
    assert list(r["groups"]["grp"]["files"].values())[0]["status"] == "QUARANTINED"
def test_indonesia_quarantines():
    d = mk([["BBCA.JK", "2024-01-02", 1, 1, 1, 1, 1, "t", "2024-01-02T21:00:00Z", "false"]]); r = I.run(stage=d, db=os.path.join(d, "x.sqlite"))
    assert list(r["groups"]["grp"]["files"].values())[0]["status"] == "QUARANTINED"
if __name__ == "__main__":
    for k, v in dict(globals()).items():
        if k.startswith("test_"): v(); print("PASS", k)
