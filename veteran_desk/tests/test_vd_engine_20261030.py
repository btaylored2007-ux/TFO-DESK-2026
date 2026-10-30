import ast, json, os, tempfile, unittest
from unittest import mock
from veteran_desk import vd_engine as E, desk as D
HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
class T(unittest.TestCase):
    def test_grade_math(self):
        self.assertEqual(E.grade(93, 3, 0.95, []), "A+"); self.assertEqual(E.grade(93, 2, 0.95, []), "A")
        self.assertEqual(E.grade(86, 2, 0.84, []), "B"); self.assertEqual(E.grade(99, 4, 1, ["floor"]), "B"); self.assertEqual(E.grade(60, 0, 1, []), "C")
    def test_caps_constants(self):
        self.assertEqual((E.CAP_SEAT, E.CAP_TOTAL, E.SEATS_PER_SLOT), (6, 40, 11))   # tfo_thfpar_20261031: all eligible seats per round
    def test_cap_never_lifts(self):
        with tempfile.TemporaryDirectory() as td:
            os.makedirs(os.path.join(td, "state", "vd_engine", "grades"))
            json.dump({"grades": {"AAA": "A+", "BBB": "B", "CCC": "C"}}, open(os.path.join(td, "state", "vd_engine", "grades", "s.json"), "w"))
            with mock.patch.object(D, "BASE", td), mock.patch.object(D, "ENGINE_WAKE", "w1"):
                j = dict(ticker="AAA", grade="A"); self.assertIsNone(D._engine_cap("s", j)); self.assertEqual(j["grade"], "A")
                j = dict(ticker="BBB", grade="A+"); self.assertTrue(D._engine_cap("s", j)); self.assertEqual((j["grade"], j["grade_seat"]), ("B", "A+"))
                j = dict(ticker="CCC", grade="A"); D._engine_cap("s", j); self.assertEqual(j["grade"], "B")
            with mock.patch.object(D, "ENGINE_WAKE", None):
                j = dict(ticker="BBB", grade="A+"); self.assertIsNone(D._engine_cap("s", j)); self.assertEqual(j["grade"], "A+")
    def test_engine_never_writes_book(self):
        import re
        src = open(os.path.join(HERE, "vd_engine.py")).read().split(chr(34)*3, 2)[2]
        self.assertEqual(set(re.findall(r"BK\.(\w+)", src)), {"aud"})          # book module used only for FX conversion
        self.assertNotIn("funder_queue", src); self.assertNotIn("FG.update", src); self.assertNotIn("risk_plan(", src)
        for line in src.splitlines():
            if "veteran_desk_book.json" in line: self.assertTrue(line.strip().startswith("Bk = rj("), line)   # read-only
    def test_engine_does_not_take_pipeline_lock(self):
        src = open(os.path.join(HERE, "vd_engine.py")).read(); self.assertNotIn("os.mkdir(PIPE_LOCK", src)
    def test_deadman_independent(self):
        src = open(os.path.join(HERE, "vd_deadman.py")).read(); self.assertNotIn("veteran_desk", src.split('"""')[2])
if __name__ == "__main__": unittest.main()
