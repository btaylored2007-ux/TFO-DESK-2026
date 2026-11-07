"""Draft course grading stays on the server and is not a published course."""

import hashlib
import json
import threading
from pathlib import Path

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
    assert data["version"] == "1.1.0" and data["supersedes"] == "1.0.0"
    questions = [q for m in data["modules"] for q in m["questions"]]
    lessons = sum(len(m["lessons"]) for m in data["modules"])
    assert len(data["modules"]) == 21 and lessons == 126 and len(questions) == 251
    assert sum(1 for q in questions if q.get("pool") == "A") == 215
    assert sum(1 for q in questions if q.get("pool") == "B") == 36
    assert all("sources" not in m and "evidence_ids" in m for m in data["modules"])
    assert all(m.get("practical_required") is True for m in data["modules"])
    old = json.loads((ROOT / "school_content" / "v1.0.0" / "course.json").read_text(encoding="utf-8"))
    new_by_id = {q["id"]: q["correct_option"] for q in questions}
    old_ids = {q["id"]: q["correct_option"] for m in old["modules"] for q in m["questions"]}
    assert all(new_by_id[qid] == answer for qid, answer in old_ids.items())
    assert hashlib.sha256("\n".join(qid + "=" + answer for qid, answer in old_ids.items()).encode()).hexdigest() == ANSWER_HASH


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
