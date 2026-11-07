"""Draft course grading stays on the server and is not a published course."""

import hashlib
import json
import shutil
import subprocess
import sys
import threading
from fractions import Fraction
from pathlib import Path

import pytest

from customer_platform.errors import ResetBlocked
from customer_platform.school import School, load_author_draft

ROOT = Path(__file__).resolve().parents[1]
DRAFT = ROOT / "school_content" / "draft" / "course.json"
ANSWER_HASH = "2ec74d6824a42a84d2199ef54bba2abe7dcbad25828c75abdcccd84a30071a75"


def _pairs():
    data = json.loads(DRAFT.read_text(encoding="utf-8"))
    pairs = [q["id"] + "=" + q["correct_option"] for m in data["modules"] for q in m["questions"]]
    lessons = sum(len(m["lessons"]) for m in data["modules"])
    return hashlib.sha256("\n".join(pairs).encode()).hexdigest(), len(pairs), len(data["modules"]), lessons


def test_answer_keys_were_not_rewritten():
    data = json.loads(DRAFT.read_text(encoding="utf-8"))
    assert data["version"] == "1.2.1" and data["supersedes"] == "1.2.0"
    questions = [q for m in data["modules"] for q in m["questions"]]
    lessons = [lesson for m in data["modules"] for lesson in m["lessons"]]
    assert len(data["modules"]) == 21 and len(lessons) == 126 and len(questions) == 287
    assert sum(1 for q in questions if q.get("pool") == "A") == 215
    assert sum(1 for q in questions if q.get("pool") == "B") == 72
    assert sum(len(lesson["body"].split()) for lesson in lessons) == 22275
    assert hashlib.sha256(DRAFT.read_bytes()).hexdigest() == "47710df87eba1bac393bb7bbc85d129add2424fd6b343a7c20e7498585a0f702"
    frozen = (ROOT / "school_content" / "v1.2.0" / "course.json").read_bytes()
    assert hashlib.sha256(frozen).hexdigest() == "cffb4fe0bd267b2a37c39666aa4d933e53303dc78a4f39574d6c08fd92e2930d"
    prior_course = json.loads(frozen)
    assert sum(len(lesson["body"].split()) for m in prior_course["modules"] for lesson in m["lessons"]) == 22186
    m08 = next(module for module in data["modules"] if module["id"] == "tts.m08")
    evidence = next(item["objective_evidence"] for item in m08["practical_rubric"]["criteria"] if item["id"] == "leverage_identity")
    assert "5% of notional" in evidence and "must not be substituted" in evidence
    old_m08 = next(module for module in prior_course["modules"] if module["id"] == "tts.m08")
    old_evidence = next(item["objective_evidence"] for item in old_m08["practical_rubric"]["criteria"] if item["id"] == "leverage_identity")
    assert "5% of notional" not in old_evidence
    assert data["content_review"]["expert_certified"] is False
    assert data["content_review"]["legal_review"] is False
    assert data["learning_tracks"]["separation"]
    assert all(m.get("practical_required") is True and m.get("practical_rubric") for m in data["modules"])
    prior = json.loads((ROOT / "school_content" / "v1.1.1" / "course.json").read_text(encoding="utf-8"))
    prior_ids = [lesson["id"] for m in prior["modules"] for lesson in m["lessons"]]
    assert prior_ids == [lesson["id"] for lesson in lessons]
    old = json.loads((ROOT / "school_content" / "v1.0.0" / "course.json").read_text(encoding="utf-8"))
    new_by_id = {q["id"]: q["correct_option"] for q in questions}
    prior_q = {q["id"]: q["correct_option"] for m in prior_course["modules"] for q in m["questions"]}
    assert prior_q == new_by_id
    old_ids = {q["id"]: q["correct_option"] for m in old["modules"] for q in m["questions"]}
    assert all(new_by_id[qid] == answer for qid, answer in old_ids.items())
    assert hashlib.sha256("\n".join(qid + "=" + answer for qid, answer in old_ids.items()).encode()).hexdigest() == ANSWER_HASH
    gate = __import__("customer_platform.school", fromlist=["quality_gate"]).quality_gate(data)
    assert gate["published"] is False and gate["word_count_is_completion"] is False


def test_unpublished_attempt_hides_the_key_and_grades_on_the_server(tmp_path):
    draft = load_author_draft(DRAFT)
    school = School(tmp_path / "t", lambda: "2026-11-07T00:00:00Z", draft=draft, allow_unpublished=True)
    served = school.begin_attempt("tts.m01")
    blob = json.dumps(served)
    assert "correct_option" not in blob and "explanation" not in blob
    module = next(item for item in draft["modules"] if item["id"] == "tts.m01")
    answers = {question["id"]: question["correct_option"] for question in module["questions"]}
    graded = school.submit(served["attempt_id"], answers)
    assert graded["passed"] is True and graded["correct"] == graded["questions"]
    assert "correct_option" not in json.dumps(school.progress())


def test_two_submits_cannot_both_grade(tmp_path):
    draft = load_author_draft(DRAFT)
    school = School(tmp_path / "t", lambda: "2026-11-07T00:00:00Z", draft=draft, allow_unpublished=True)
    served = school.begin_attempt("tts.m01")
    module = next(item for item in draft["modules"] if item["id"] == "tts.m01")
    answers = {question["id"]: question["correct_option"] for question in module["questions"]}
    errors = []
    barrier = threading.Barrier(2)

    def go():
        try:
            barrier.wait()
            school.submit(served["attempt_id"], dict(answers))
        except ResetBlocked as exc:
            errors.append(exc.code)

    threads = [threading.Thread(target=go) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert len(school.progress()["attempts"]) == 1
    assert len(errors) == 1


def test_second_pool_is_served_on_the_retake_and_v1_attempts_still_grade(tmp_path):
    draft = load_author_draft(DRAFT)
    school = School(tmp_path / "t", lambda: "2026-11-07T00:00:00Z", draft=draft, allow_unpublished=True)
    first = school.begin_attempt("tts.m03")
    assert first.get("questions")
    assert school._open(first["attempt_id"])["pool"] == "A"
    module = next(item for item in draft["modules"] if item["id"] == "tts.m03")
    school.submit(first["attempt_id"], {q["id"]: "a" for q in module["questions"] if q.get("pool", "A") == "A"})
    second = school.begin_attempt("tts.m03", review_ack=True)
    assert school._open(second["attempt_id"])["pool"] == "B"
    old = load_author_draft(ROOT / "school_content" / "v1.0.0" / "course.json")
    legacy = School(tmp_path / "legacy", lambda: "2026-11-07T00:00:00Z", draft=old, allow_unpublished=True)
    opened = legacy.begin_attempt("tts.m01")
    assert opened["questions"][0]["question_id"].startswith("tts.m01")
    legacy.draft = draft
    answers = {q["id"]: q["correct_option"] for q in old["modules"][0]["questions"]}
    graded = legacy.submit(opened["attempt_id"], answers)
    assert graded["passed"] is True
    practical = school.save_practical("tts.m01", "I recorded a paper spread for five days.")
    assert practical["scored"] is False
    capstone = school.save_capstone({item["id"]: 2 for item in draft["capstone"]["criteria"]})
    assert capstone["average"] == 2
    assert capstone["label"] == "practice evidence, not validated skill"


def test_lesson_navigation_rubric_and_synthetic_exercise(tmp_path):
    from fractions import Fraction
    import shutil
    import subprocess
    import sys

    from customer_platform.errors import Denied
    from customer_platform.platform import Platform
    from customer_platform.school import synthetic_exercise, synthetic_exercise_file

    draft = load_author_draft(DRAFT)
    school = School(tmp_path / "t", lambda: "2026-11-07T00:00:00Z", draft=draft, allow_unpublished=False)
    opened = school.read_lesson("tts.m01", "tts.m01.l1")
    source = next(lesson["body"] for lesson in draft["modules"][0]["lessons"] if lesson["id"] == "tts.m01.l1")
    assert opened["body"] == source
    assert "correct_option" not in json.dumps(opened)
    assert school.progress()["lessons_read"] == ["tts.m01.l1"]
    with pytest.raises(ResetBlocked):
        school.read_lesson("tts.m02", "tts.m02.l1")
    rubric = school.save_rubric(
        "tts.m01",
        {item["id"]: 3 for item in draft["modules"][0]["practical_rubric"]["criteria"]},
        profit_claimed=True,
    )
    assert rubric["passed"] is False and rubric["scored"] is False
    assert school.progress()["cleared"] == []

    desk = Platform(tmp_path / "customers", enabled=True, webhook_secret=b"test-webhook-secret")
    desk.provision_tenant("ada@example.com", "correct-horse-1", "Ada", starting_capital="1000.00")
    ada = desk.login("ada@example.com", "correct-horse-1")
    desk.create_operator("op@example.com", "correct-horse-9", "Operator")
    operator = desk.operator_login("op@example.com", "correct-horse-9")
    lesson = desk.school_lesson(ada, "tts.m01", "tts.m01.l1")
    assert lesson["title"] and "correct_option" not in json.dumps(lesson)
    with pytest.raises(Denied):
        desk.school_lesson(operator, "tts.m01", "tts.m01.l1")
    with pytest.raises(ResetBlocked):
        desk.school_begin(ada, "tts.m01")
    exercise = synthetic_exercise()
    assert exercise["label"] == "SYNTHETIC" and exercise["edge_claim"] is False
    assert "not evidence of a tradable edge" in exercise["disclaimer"].lower() or "not evidence" in exercise["disclaimer"].lower()
    blob = synthetic_exercise_file("synthetic_mini_backtest.csv").decode()
    assert "SYNTHETIC" in blob and "next_return" not in blob
    with pytest.raises(Denied):
        synthetic_exercise_file("results.json")
    states = [Fraction(0) for _ in range(10)]
    states[0] = Fraction(1)
    loss, win = Fraction(58, 100), Fraction(42, 100)
    for _ in range(120):
        nxt = [Fraction(0) for _ in range(10)]
        for streak, prob in enumerate(states):
            if streak == 9:
                nxt[9] += prob
            else:
                nxt[streak + 1] += prob * loss
                nxt[0] += prob * win
        states = nxt
    assert round(float(states[9]), 4) == 0.3053
    folder = tmp_path / "datasets"
    shutil.copytree(ROOT / "school_content" / "v1.2.0" / "datasets", folder)
    subprocess.check_call([sys.executable, str(folder / "compute_backtest.py")], cwd=folder)
    fresh = json.loads((folder / "results.json").read_text(encoding="utf-8"))
    stored = json.loads((ROOT / "school_content" / "v1.2.0" / "datasets" / "results.json").read_text(encoding="utf-8"))
    assert fresh["split"] == stored["split"]
    assert fresh["chosen_lookback_by_in_sample_honest_net"] == stored["chosen_lookback_by_in_sample_honest_net"]
    assert fresh["out_of_sample_for_chosen_lookback"]["honest_with_cost"]["total_return"] == stored["out_of_sample_for_chosen_lookback"]["honest_with_cost"]["total_return"]
    assert "evidence about real markets" in fresh["interpretation"]["no_edge_claim"]
    reported = exercise["out_of_sample_net"]
    others = [row["honest_with_cost"]["total_return"] for row in stored["out_of_sample_all_lookbacks"]]
    assert reported == stored["out_of_sample_for_chosen_lookback"]["honest_with_cost"]["total_return"]
    assert reported != max(others)
    assert exercise["series"] == "synthetic csv"


def test_an_old_attempt_keeps_its_course_and_rubric(tmp_path):
    older = load_author_draft(ROOT / "school_content" / "v1.2.0" / "course.json")
    newer = load_author_draft(DRAFT)
    school = School(tmp_path / "t", lambda: "2026-11-07T00:00:00Z", draft=older, allow_unpublished=True)
    opened = school.begin_attempt("tts.m08")
    assert school._open(opened["attempt_id"])["package_version"] == "1.2.0"
    assert school._open(opened["attempt_id"])["rubric_version"] == "1.2.0"
    module = next(item for item in older["modules"] if item["id"] == "tts.m08")
    marks = {item["id"]: 2 for item in module["practical_rubric"]["criteria"]}
    school.save_rubric("tts.m08", marks)
    school.draft = newer
    stored = school.stored_rubric("tts.m08")
    assert stored["rubric_version"] == "1.2.0" and stored["passed"] is False
    evidence = next(item["objective_evidence"] for item in stored["criteria"] if item["id"] == "leverage_identity")
    assert "no-buffer wipeout" in evidence and "5% of notional" not in evidence
    school.save_rubric("tts.m08", marks)
    current = school.stored_rubric("tts.m08")
    assert current["rubric_version"] == "1.2.1"
    assert "5% of notional" in next(item["objective_evidence"] for item in current["criteria"] if item["id"] == "leverage_identity")
    history = school._progress()["rubrics"]
    assert [row["rubric_version"] for row in history] == ["1.2.0", "1.2.1"]
    answers = {q["id"]: q["correct_option"] for q in module["questions"]}
    graded = school.submit(opened["attempt_id"], answers)
    assert graded["passed"] is True
    attempt = school.progress()["attempts"][-1]
    assert attempt["package_version"] == "1.2.0" and attempt["rubric_version"] == "1.2.0"
