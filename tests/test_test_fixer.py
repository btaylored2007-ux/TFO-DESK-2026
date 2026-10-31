"""The nightly test fixer. Offline. It must not merge, must not deploy, and must not edit a risk rail."""
import json, os, re, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import tools.test_fixer as F

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PLIST = os.path.join(ROOT, "launchd", "com.tfo.test-fixer.plist")


def test_plist_is_a_nightly_job_and_names_the_fixer():
    text = open(PLIST).read()
    assert "com.tfo.test-fixer" in text
    assert "tools/test_fixer.py" in text
    assert "<key>Hour</key>" in text and "<key>Minute</key>" in text
    assert "<key>Weekday</key>" not in text   # every night, not one weekday
    assert "TFO_TEST_OFFLINE" in text


def test_classify_stale_bug_and_environment():
    assert F.classify("tests/t.py::test_a", "AssertionError: 'RETIRED' != 'PENDING'", "tests/t.py:9") == "stale_test"
    assert F.classify("tests/t.py::test_b", "KeyError: 'main'", "tests/t.py:12 in test_b") == "stale_test"
    assert F.classify("tests/t.py::test_c", "TypeError: 'set' object is not callable", "tests/t.py:119") == "stale_test"
    assert F.classify("tests/t.py::test_d", "KeyError: 'pnl_today_aud'", "hunt48_short_paper.py:890 in write_status") == "real_bug"
    assert F.classify("tests/t.py::test_e", "FileNotFoundError: state/trend18.json", "scorecard_weekly.py:16") == "environment"
    assert F.classify("tests/t.py::test_f", "network blocked in CI (TFO_TEST_OFFLINE)", "urllib") == "environment"
    assert F.classify("veteran_desk/tests/t.py::test_g", "PermissionError: TEST GUARD: os.makedirs on live path", "conftest.py") == "environment"


def test_quarantine_is_three_retries_and_a_pass_is_flaky():
    assert F.judge_retries(["failed", "failed", "failed"]) is None
    assert F.judge_retries(["failed", "passed", "failed"]) == "flaky"
    assert F.judge_retries(["passed", "passed", "passed"]) == "flaky"
    assert F.judge_retries([]) is None


def test_parse_pytest_nodeids():
    text = "F\n=================================== FAILURES ===================================\nFAILED tests/a.py::T::test_one - AssertionError: 'RETIRED' != 'PENDING'\nERROR veteran_desk/tests/b.py::test_two - TimeoutError: x\n1 failed, 1 error\n"
    rows, summary = F.parse_pytest(text)
    assert [r["nodeid"] for r in rows] == ["tests/a.py::T::test_one", "veteran_desk/tests/b.py::test_two"]
    assert "RETIRED" in rows[0]["message"]
    assert "failed" in summary


def test_rail_file_and_rail_assignment_are_refused():
    assert F.is_rail("veteran_desk/book.py")
    assert F.is_rail("./tfo_buy.py")
    assert not F.is_rail("tests/test_book.py")
    assert F.rail_blocked("veteran_desk/book.py", "x", "y")
    assert F.rail_blocked("tests/t.py", "US_BETA_CAP_PCT = 0.60", "US_BETA_CAP_PCT = 0.40")
    assert not F.rail_blocked("tests/t.py", "US_BETA_CAP_PCT = 0.60", "US_BETA_CAP_PCT = 0.60")
    assert F.propose_fix({"nodeid": "veteran_desk/book.py::test_x", "class": "real_bug", "message": "AssertionError: 1 != 2", "traceback": ""}, "def test_x(self):\n    pass\n") is None


def test_stale_literal_patch_stays_inside_the_failing_test():
    src = (
        "class C:\n"
        "    def test_hang_times_out_fast(self):\n"
        "        self.assertEqual(status, \"PENDING\")\n"
        "    def test_other(self):\n"
        "        self.assertEqual(x, \"PENDING\")\n"
    )
    fail = {"nodeid": "tests/test_claude.py::C::test_hang_times_out_fast", "class": "stale_test",
            "message": "AssertionError: 'RETIRED' != 'PENDING'", "traceback": "tests/test_claude.py:9"}
    patch = F.propose_fix(fail, src)
    assert patch and patch["path"].startswith("tests/")
    assert "\"RETIRED\"" in patch["new"].split("def test_other")[0]
    assert "def test_other" in patch["new"] and "\"PENDING\"" in patch["new"].split("def test_other")[1]
    assert not F.rail_blocked(patch["path"], patch["old"], patch["new"])


def test_simple_set_shadow_is_renamed_and_does_not_touch_fail_calls():
    src = "class T:\n    def setUp(self):\n        self.fail = set()\n    def test_x(self):\n        self.fail = {'A'}\n"
    fail = {"nodeid": "tests/test_three.py::T::test_x", "class": "stale_test",
            "message": "TypeError: 'set' object is not callable", "traceback": "tests/test_three.py:119"}
    patch = F.propose_fix(fail, src)
    assert patch["new"].count("self.blocked") == 2 and "self.fail" not in patch["new"]
    src_call = src + "    def test_y(self):\n        self.fail('no')\n"
    assert F.propose_fix(fail, src_call) is None


def test_never_merges_and_never_invokes_safe_deploy():
    try:
        F.assert_allowed(["gh", "pr", "merge", "1"])
        assert False, "merge must be refused"
    except RuntimeError as e:
        assert "never merges" in str(e)
    try:
        F.assert_allowed(["bash", "tools/safe_deploy.sh", "book.py"])
        assert False, "deploy must be refused"
    except RuntimeError as e:
        assert "safe_deploy" in str(e)
    # naming the gate in a PR body is required; that is not running it
    body = "Deploy only through `tools/safe_deploy.sh` and the test gate. Do not merge."
    cmds = F.gh_pr_commands("cursor/test-fixer-x", "main", "Test fixer: x", body, ["tests/t.py"])
    F.assert_allowed(cmds[-1])
    flat = [a for c in cmds for a in c]
    assert "merge" not in flat
    assert not any(os.path.basename(a) == "safe_deploy.sh" for a in flat)
    assert cmds[-1][:3] == ["gh", "pr", "create"]


def test_green_suite_writes_an_ok_card_and_skips_the_baseline():
    files = {}
    def write_text(path, text, append=False):
        files[path] = (files.get(path, "") + text) if append else text
    def run_suite():
        return "FAILED tests/keep.py::test_known - AssertionError: old\n2 passed\n"
    calls = []
    def run_node(node):
        calls.append(node); return 1, "E   AssertionError: old\n"
    root = os.path.join(ROOT, "tests")  # load_known reads tools/test_baseline.json from this root's parent only if we pass a root that has one
    # isolated root so the real baseline is not what this case is proving
    import tempfile
    root = tempfile.mkdtemp()
    os.makedirs(os.path.join(root, "tools"))
    json.dump({"known_failures": ["tests/keep.py::test_known"]}, open(os.path.join(root, "tools", "test_baseline.json"), "w"))
    report = F.nightly(root=root, run_suite=run_suite, run_node=run_node, read_text=lambda p: "", write_text=write_text,
                        env={}, run_cmd=lambda a: 0, http_post=lambda *a, **k: (0, {}), dry_run=True, ref="main")
    assert report["status"] == "green" and report["card"]["tone"] == "ok"
    assert calls == []
    card = json.loads(files[F.HEALTH_REL])
    assert card["card"]["title"] == "Test health" and card["failures"] == []
    assert "safe_deploy.sh" in card["deploy"] and "never merges" in card["deploy"]


def test_flaky_node_is_retried_three_times_and_not_patched():
    files = {}
    cmds = []
    def write_text(path, text, append=False):
        files[path] = files.get(path, "") + text if append else text
    outs = [(1, "E   AssertionError: 1 != 2\ntests/flaky.py:4"), (0, "1 passed"), (1, "E   AssertionError: 1 != 2\n")]
    def run_node(node):
        return outs.pop(0)
    report = F.nightly(root=os.path.dirname(ROOT), run_suite=lambda: "FAILED tests/flaky.py::test_sometimes - AssertionError: 1 != 2\n",
                        run_node=run_node, read_text=lambda p: "def test_sometimes(self):\n    assert 1 == 2\n",
                        write_text=write_text, env={"CURSOR_API_KEY": "should-not-be-used"}, run_cmd=lambda a: cmds.append(a),
                        http_post=lambda *a, **k: (_ for _ in ()).throw(AssertionError("no agent for a flake")),
                        dry_run=False, ref="main")
    row = report["failures"][0]
    assert row["class"] == "flaky" and row["action"] == "quarantined"
    assert [r["outcome"] for r in row["retries"]] == ["failed", "passed", "failed"]
    assert len(row["retries"]) == 3
    assert cmds == []
    log = files[F.QUARANTINE_REL]
    assert "tests/flaky.py::test_sometimes" in log and "flaky" in log
    assert report["card"]["tone"] == "watch"


def test_stale_fix_opens_a_pr_and_does_not_run_when_dry():
    written = {}
    cmds = []
    src = "class C:\n    def test_hang(self):\n        self.assertEqual(status, \"PENDING\")\n"
    def write_text(path, text, append=False):
        written[path] = text
    def run_node(node):
        return 1, "E   AssertionError: 'RETIRED' != 'PENDING'\ntests/test_claude.py:4 in test_hang\n"
    report = F.nightly(root=ROOT, run_suite=lambda: "FAILED tests/test_claude.py::C::test_hang - AssertionError: 'RETIRED' != 'PENDING'\n",
                        run_node=run_node, read_text=lambda p: src, write_text=write_text, env={},
                        run_cmd=lambda a: cmds.append(a), http_post=lambda *a, **k: (0, {}), dry_run=True, ref="main")
    row = report["failures"][0]
    assert row["class"] == "stale_test" and row["action"] == "pr_opened"
    assert "RETIRED" in row["why"]
    assert cmds == [] and "tests/test_claude.py" not in written
    assert not any(re.search(r"(^|\s)pr merge(\s|$)", c) for c in row["commands"])
    report2_cmds = []
    written.clear()
    F.nightly(root=ROOT, run_suite=lambda: "FAILED tests/test_claude.py::C::test_hang - AssertionError: 'RETIRED' != 'PENDING'\n",
              run_node=run_node, read_text=lambda p: src, write_text=write_text, env={},
              run_cmd=lambda a: report2_cmds.append(list(a)), http_post=lambda *a, **k: (0, {}), dry_run=False, ref="main")
    assert "RETIRED" in written["tests/test_claude.py"]
    assert report2_cmds[-1][:3] == ["gh", "pr", "create"]
    assert not any("merge" in c for c in report2_cmds)


def test_cloud_agent_when_there_is_no_safe_patch_and_the_key_is_set():
    posts = []
    def http_post(url, body, headers):
        posts.append((url, body, headers))
        return 200, {"agent": {"id": "bc-test", "url": "https://cursor.com/agents/bc-test"}}
    def run_node(node):
        return 1, "E   KeyError: 'pnl'\nhunt48_short_paper.py:890 in write_status\n"
    src = "def test_h4(self):\n    assert s['pnl_today_aud'] == 0\n"
    report = F.nightly(root=ROOT, run_suite=lambda: "FAILED tests/test_h4.py::test_h4 - KeyError: pnl\n",
                        run_node=run_node, read_text=lambda p: src, write_text=lambda *a, **k: None,
                        env={"CURSOR_API_KEY": "test-key", "TFO_GIT_REPO": "https://github.com/btaylored2007-ux/TFO-DESK-2026"},
                        run_cmd=lambda a: (_ for _ in ()).throw(AssertionError("gh must not run")),
                        http_post=http_post, dry_run=False, ref="main")
    row = report["failures"][0]
    assert row["class"] == "real_bug" and row["action"] == "cloud_agent"
    assert row["agent_url"] == "https://cursor.com/agents/bc-test"
    url, body, headers = posts[0]
    assert url.endswith("/v1/agents")
    assert body["autoCreatePR"] is True and body["workOnCurrentBranch"] is False
    assert body["repos"][0]["startingRef"] == "main"
    assert headers["Authorization"].startswith("Basic ")
    prompt = body["prompt"]["text"]
    assert "veteran_desk/book.py" in prompt and "Do not merge" in prompt
    assert "2%" in prompt and "CoinGlass" in prompt
    assert "safe_deploy.sh" in prompt


def test_no_key_and_no_patch_needs_a_person():
    def run_node(node):
        return 1, "E   KeyError: 'pnl'\nhunt48_short_paper.py:890 in write_status\n"
    report = F.nightly(root=ROOT, run_suite=lambda: "FAILED tests/test_h4.py::test_h4 - KeyError: pnl\n",
                        run_node=run_node, read_text=lambda p: "def test_h4(self):\n    pass\n",
                        write_text=lambda *a, **k: None, env={}, run_cmd=lambda a: None,
                        http_post=lambda *a, **k: (_ for _ in ()).throw(AssertionError("no http")),
                        dry_run=False, ref="main")
    assert report["failures"][0]["action"] == "needs_human"
    assert report["status"] == "alarm"
