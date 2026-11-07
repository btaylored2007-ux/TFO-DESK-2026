"""The Transparent Trading School progress and assessment engine.

The 21 module slots are a coverage map. Lesson text is not in this package.
A content package is integrated only when it validates. Until then the school
lists the slots and does not offer an assessment.
"""

import os
import random
from decimal import Decimal, InvalidOperation

from customer_platform.errors import Denied, ResetBlocked
from customer_platform.files import append_jsonl, read_json, write_json
from customer_platform.paperbook import new_id

SCHOOL_NAME = "The Transparent Trading School"
DEFAULT_PASS = Decimal("0.80")
COVERAGE_KEYS = (
    "ai_judgment",
    "asset_markets",
    "beginner_to_advanced",
    "costs",
    "financial_metrics",
    "major_sectors",
    "risk",
    "strategy_testing",
    "trading_metrics",
)

# Stable ids for the content package to fill. These are not lessons.
SLOTS = (
    ("tts-01", "markets", "How markets match buyers and sellers"),
    ("tts-02", "markets", "Shares, bonds, cash, commodities, and currencies"),
    ("tts-03", "markets", "Prices, spreads, and the trading session"),
    ("tts-04", "sectors", "How industries are grouped"),
    ("tts-05", "sectors", "Industries that move with the economy"),
    ("tts-06", "sectors", "Industries that hold up in a slowdown"),
    ("tts-07", "sectors", "Growth industries and what the label leaves out"),
    ("tts-08", "metrics", "Reading a financial report"),
    ("tts-09", "metrics", "Earnings, cash, debt, and yields"),
    ("tts-10", "metrics", "Return, drawdown, and sample size"),
    ("tts-11", "costs", "Spreads, fees, and slippage"),
    ("tts-12", "risk", "How much can be lost"),
    ("tts-13", "risk", "Stops, exits, and what a stop does not do"),
    ("tts-14", "practice", "Practice capital and a new epoch"),
    ("tts-15", "testing", "A past result is not a promise"),
    ("tts-16", "testing", "Testing a rule on paper"),
    ("tts-17", "testing", "Reviewing a record without rewriting it"),
    ("tts-18", "markets", "Rates and inflation as context"),
    ("tts-19", "risk", "Holding more than one exposure"),
    ("tts-20", "judgment", "Using a model without moving the rails"),
    ("tts-21", "testing", "Bringing the record together"),
)


class School:
    def __init__(self, tenant_dir, clock, pass_mark=None, package=None, draft=None, published=False, allow_unpublished=False):
        self.dir = os.path.join(tenant_dir, "school")
        self.progress_path = os.path.join(self.dir, "progress.json")
        self.attempt_path = os.path.join(self.dir, "attempts.jsonl")
        self.clock = clock
        self.pass_mark = _mark(pass_mark if pass_mark is not None else DEFAULT_PASS)
        self.draft = draft
        self.published = bool(published)
        self.allow_unpublished = bool(allow_unpublished)
        self.versions = {}
        if draft is not None:
            self.versions[draft.get("version")] = draft
        prior = None
        base = os.path.join(os.path.dirname(__file__), "school_content")
        for version, folder in (("1.0.0", "v1.0.0"), ("1.1.0", "v1.1.0"), ("1.1.1", "v1.1.1"), ("1.2.0", "v1.2.0"), ("1.2.1", "v1.2.1")):
            path = os.path.join(base, folder, "course.json")
            if os.path.isfile(path):
                self.versions.setdefault(version, load_author_draft(path))
        self.package = None if draft is not None or package is None else validate_package(package)
        self._lock = __import__("threading").Lock()

    def catalog(self):
        passed = set(self._progress().get("passed") or [])
        if self.draft is not None:
            rows = []
            progress = self._progress()
            cleared = set(progress.get("cleared") or [])
            read = set(progress.get("lessons_read") or [])
            previous = True
            for module in self.draft["modules"]:
                study = previous
                unlocked = self.published and study
                lessons = []
                if study:
                    lessons = [
                        {
                            "lesson_id": lesson["id"],
                            "read": lesson["id"] in read,
                            "title": lesson["title"],
                        }
                        for lesson in module["lessons"]
                    ]
                rows.append({
                    "content": "published" if self.published else "unpublished_draft",
                    "label": module["title"],
                    "lessons": lessons,
                    "module_id": module["id"],
                    "order": module["number"],
                    "passed": module["id"] in cleared,
                    "study": study,
                    "unlocked": unlocked,
                })
                previous = module["id"] in cleared
            return {
                "certification": False,
                "content_finished": False,
                "coverage_note": "Draft coverage is representative, not every metric. Fact review is pending.",
                "credential": "A course completion record is not an accreditation.",
                "lessons_read": len(read),
                "modules": rows,
                "name": self.draft.get("brand") or SCHOOL_NAME,
                "pass_mark": format(self.pass_mark, "f"),
                "private": True,
                "published": False,
                "warning": "Unpublished draft. Not an accepted course. Fact review is unfinished.",
            }
        integrated = self.package is not None and self.published
        rows = []
        previous_passed = True
        for index, (module_id, area, label) in enumerate(SLOTS):
            unlocked = integrated and previous_passed
            rows.append({
                "area": area,
                "content": "integrated" if integrated else "not_integrated",
                "label": label,
                "module_id": module_id,
                "order": index + 1,
                "passed": module_id in passed,
                "unlocked": unlocked,
            })
            previous_passed = module_id in passed
        return {
            "certification": False,
            "content_finished": integrated,
            "coverage_note": "Coverage is checked against the package matrix, not every possible metric.",
            "credential": "This school does not award a credential.",
            "modules": rows,
            "name": SCHOOL_NAME,
            "pass_mark": format(self.pass_mark, "f"),
            "private": True,
            "published": self.published,
        }

    def progress(self):
        body = self._progress()
        public_attempts = []
        for row in body.get("attempts") or []:
            public_attempts.append({
                "attempt_id": row["attempt_id"],
                "correct": row["correct"],
                "module_id": row["module_id"],
                "package_version": row["package_version"],
                "pass_mark": row["pass_mark"],
                "passed": row["passed"],
                "questions": row["questions"],
                "rubric_version": row.get("rubric_version") or row["package_version"],
                "utc": row["utc"],
            })
        return {
            "attempts": public_attempts,
            "cleared": list(body.get("cleared") or []),
            "content_finished": False if self.draft is not None else self.package is not None,
            "lessons_read": list(body.get("lessons_read") or []),
            "passed": list(body.get("passed") or []),
            "pass_mark": format(self.pass_mark, "f"),
            "quiz_passed": list(body.get("quiz_passed") or []),
        }

    def begin_attempt(self, module_id, *, review_ack=False):
        with self._lock:
            if self.draft is not None and not self.published and not self.allow_unpublished:
                raise ResetBlocked("unpublished", "This draft is not published")
            source = self.draft if self.draft is not None else self.package
            if source is None:
                raise ResetBlocked("content", "Content not integrated")
            module = self._module(module_id)
            if self.draft is None or self.published:
                catalog = {row["module_id"]: row for row in self.catalog()["modules"]}
                if not catalog[module_id]["unlocked"]:
                    raise ResetBlocked("locked", "This module is still locked")
            prior = [row for row in self._progress().get("attempts") or [] if row["module_id"] == module_id]
            if prior and review_ack is not True:
                raise ResetBlocked("review", "A retake starts only after the lesson review is acknowledged")
            attempt_id = new_id("at_")
            pool = _select_pool(module, prior)
            chosen = [item for item in module["questions"] if pool == "mixed" or (item.get("pool") or "A") == pool]
            order = list(chosen or module["questions"])
            random.Random(attempt_id).shuffle(order)
            question_ids = [item.get("id") or item.get("question_id") for item in order]
            served = []
            for item in order:
                if "correct_option" in item:
                    options = list(item["options"])
                    random.Random(attempt_id + item["id"]).shuffle(options)
                    served.append({"options": [opt["id"] for opt in options], "question_id": item["id"]})
                    public = {
                        "options": [{"id": opt["id"], "text": opt["text"]} for opt in options],
                        "question_id": item["id"],
                        "stem": item["stem"],
                    }
                    item["_public"] = public
                else:
                    item["_public"] = _public_question(item)
            record = {
                "attempt_id": attempt_id,
                "module_id": module_id,
                "open": True,
                "option_order": served,
                "package_version": source.get("version"),
                "pool": pool,
                "question_ids": question_ids,
                "rubric_version": source.get("version"),
                "utc": self.clock(),
            }
            self._store_open(record)
            lessons = []
            if self.published:
                lessons = [{"lesson_id": lesson.get("id") or lesson.get("lesson_id"), "title": lesson["title"]} for lesson in module["lessons"]]
            return {
                "attempt_id": attempt_id,
                "lessons": lessons,
                "module_id": module_id,
                "questions": [item["_public"] for item in order],
            }

    def submit(self, attempt_id, answers):
        with self._lock:
            if not isinstance(answers, dict):
                raise ResetBlocked("answers", "Answers are required")
            if "score" in answers or "passed" in answers:
                raise ResetBlocked("forged", "The server grades the attempt")
            open_row = self._open(attempt_id)
            if open_row is None or not open_row.get("open"):
                raise ResetBlocked("attempt", "Attempt was not found")
            source = self.versions.get(open_row["package_version"]) or (self.draft if self.draft is not None else self.package)
            if source is None or open_row["package_version"] != source.get("version"):
                raise ResetBlocked("version", "The content package version changed")
            module = None
            for item in source["modules"]:
                if item.get("id") == open_row["module_id"] or item.get("module_id") == open_row["module_id"]:
                    module = item
            if module is None:
                raise ResetBlocked("module", "Module was not found")
            by_id = {item.get("id") or item.get("question_id"): item for item in module["questions"]}
            correct = 0
            for question_id in open_row["question_ids"]:
                selected = answers.get(question_id)
                question = by_id[question_id]
                expected = question.get("correct_option", question.get("correct"))
                if selected == expected:
                    correct += 1
            total = len(open_row["question_ids"])
            ratio = Decimal(correct) / Decimal(total)
            passed = ratio >= self.pass_mark
            graded = {
                "attempt_id": attempt_id,
                "correct": correct,
                "module_id": open_row["module_id"],
                "package_version": open_row["package_version"],
                "pass_mark": format(self.pass_mark, "f"),
                "passed": passed,
                "pool": open_row.get("pool"),
                "questions": total,
                "rubric_version": open_row.get("rubric_version") or source.get("version"),
                "utc": self.clock(),
            }
            body = self._progress()
            body.setdefault("attempts", []).append(graded)
            if passed and open_row["module_id"] not in body.setdefault("quiz_passed", []):
                body["quiz_passed"].append(open_row["module_id"])
            if passed and self._practical_met(module) and open_row["module_id"] not in body.setdefault("cleared", []):
                body["cleared"].append(open_row["module_id"])
            body.setdefault("passed", [])
            write_json(self.progress_path, body)
            open_row["open"] = False
            self._replace_open(open_row)
            append_jsonl(self.attempt_path, {"attempt_id": attempt_id, "passed": passed, "utc": graded["utc"]})
            return {
                "attempt_id": attempt_id,
                "correct": correct,
                "credential": "A course completion record is not an accreditation.",
                "module_id": open_row["module_id"],
                "passed": passed,
                "pool": open_row.get("pool"),
                "practical_required": bool(module.get("practical_required")),
                "practical_saved": self._practical_met(module),
                "questions": total,
            }

    def read_lesson(self, module_id, lesson_id):
        """Return one lesson body. The response has no questions and no answer key."""
        if not self._study_open(module_id):
            raise ResetBlocked("locked", "This module is still locked")
        module = self._module(module_id)
        lesson = None
        for item in module["lessons"]:
            if item["id"] == lesson_id:
                lesson = item
                break
        if lesson is None:
            raise ResetBlocked("lesson", "Lesson was not found")
        body = self._progress()
        read = body.setdefault("lessons_read", [])
        if lesson_id not in read:
            read.append(lesson_id)
            write_json(self.progress_path, body)
        return {
            "body": lesson["body"],
            "credential": "A course completion record is not an accreditation.",
            "layer": lesson.get("layer") or "",
            "lesson_id": lesson["id"],
            "module_id": module_id,
            "published": False,
            "title": lesson["title"],
            "warning": "Unpublished draft. Not an accepted course.",
        }

    def save_rubric(self, module_id, marks, *, profit_claimed=False):
        """Store manual rubric marks. Profit does not pass the module."""
        module = self._module(module_id)
        rubric = module.get("practical_rubric") or {}
        criteria = list(rubric.get("criteria") or [])
        if not criteria:
            raise ResetBlocked("rubric", "this module has no rubric")
        clean = {}
        snapshot = []
        for item in criteria:
            key = item["id"]
            if key not in marks:
                raise ResetBlocked("rubric", "every criterion needs a mark")
            mark = int(marks[key])
            if mark not in (0, 1, 2, 3):
                raise ResetBlocked("rubric", "marks are 0, 1, 2, or 3")
            clean[key] = mark
            snapshot.append({
                "description": item.get("description") or "",
                "id": key,
                "objective_evidence": item.get("objective_evidence") or "",
            })
        version = (self.draft or {}).get("version") or ""
        body = self._progress()
        record = {
            "criteria": snapshot,
            "label": "stored rubric, not a pass and not an accreditation",
            "marks": clean,
            "module_id": module_id,
            "package_version": version,
            "passed": False,
            "profit_claimed": bool(profit_claimed),
            "rubric_version": version,
            "scored": False,
            "utc": self.clock(),
        }
        body.setdefault("rubrics", []).append(record)
        write_json(self.progress_path, body)
        return {
            "label": record["label"],
            "module_id": module_id,
            "passed": False,
            "rubric_version": version,
            "scored": False,
        }

    def stored_rubric(self, module_id):
        """Return the saved rubric snapshot. Later course text does not replace it."""
        rows = [row for row in self._progress().get("rubrics") or [] if row.get("module_id") == module_id]
        if not rows:
            raise ResetBlocked("rubric", "no rubric is stored")
        row = rows[-1]
        return {
            "criteria": list(row.get("criteria") or []),
            "passed": False,
            "rubric_version": row.get("rubric_version") or "",
        }

    def save_practical(self, module_id, text, *, critical_mistake=False):
        """Store a paper exercise. It is not auto-graded."""
        if not str(text or "").strip():
            raise ResetBlocked("practical", "the exercise note is empty")
        body = self._progress()
        entry = {
            "critical_mistake": bool(critical_mistake),
            "module_id": module_id,
            "package_version": (self.draft or {}).get("version") or "",
            "scored": False,
            "text": str(text)[:2000],
            "utc": self.clock(),
        }
        body.setdefault("practicals", []).append(entry)
        if critical_mistake:
            body.setdefault("remediation", []).append(module_id)
        write_json(self.progress_path, body)
        return {"module_id": module_id, "scored": False, "label": "saved, not graded"}

    def save_capstone(self, scores):
        """Record a 0-3 rubric. The average is arithmetic, not an accreditation."""
        criteria = [item["id"] for item in (self.draft or {}).get("capstone", {}).get("criteria") or []]
        if len(criteria) != 10:
            raise ResetBlocked("capstone", "the capstone rubric is not loaded")
        clean = {}
        for key in criteria:
            if key not in scores:
                raise ResetBlocked("capstone", "every criterion needs a mark")
            mark = int(scores[key])
            if mark not in (0, 1, 2, 3):
                raise ResetBlocked("capstone", "marks are 0, 1, 2, or 3")
            clean[key] = mark
        average = sum(clean.values()) / 10
        body = self._progress()
        body["capstone"] = {
            "average": average,
            "label": "practice evidence, not validated skill",
            "scores": clean,
            "scored_by": "manual rubric entry",
        }
        write_json(self.progress_path, body)
        return body["capstone"]

    def _study_open(self, module_id):
        source = self.draft if self.draft is not None else self.package
        if source is None:
            return False
        ids = [module.get("id") or module.get("module_id") for module in source["modules"]]
        if module_id not in ids:
            raise ResetBlocked("module", "Module was not found")
        if not self.published and not self.draft:
            return False
        cleared = set(self._progress().get("cleared") or [])
        for previous, current in zip([None, *ids], ids):
            if current == module_id:
                return previous is None or previous in cleared
        return False

    def _practical_met(self, module):
        if not module.get("practical_required"):
            return True
        done = {row["module_id"] for row in self._progress().get("practicals") or []}
        return module.get("id") in done or module.get("module_id") in done

    def _module(self, module_id):
        source = self.draft if self.draft is not None else self.package
        for module in source["modules"]:
            if module.get("id") == module_id or module.get("module_id") == module_id:
                return module
        raise ResetBlocked("module", "Module was not found")

    def _progress(self):
        body = read_json(self.progress_path, None)
        if body is None:
            body = {"attempts": [], "passed": []}
            os.makedirs(self.dir, mode=0o700, exist_ok=True)
            write_json(self.progress_path, body)
        return body

    def _store_open(self, record):
        path = os.path.join(self.dir, "open.json")
        body = read_json(path, {"rows": []})
        body["rows"].append(record)
        write_json(path, body)

    def _open(self, attempt_id):
        body = read_json(os.path.join(self.dir, "open.json"), {"rows": []})
        for row in body["rows"]:
            if row["attempt_id"] == attempt_id:
                return row
        return None

    def _replace_open(self, record):
        path = os.path.join(self.dir, "open.json")
        body = read_json(path, {"rows": []})
        body["rows"] = [record if row["attempt_id"] == record["attempt_id"] else row for row in body["rows"]]
        write_json(path, body)


def validate_package(package):
    if not isinstance(package, dict) or package.get("certification") not in (False, None):
        raise ResetBlocked("package", "Content package is not valid")
    if package.get("fixture") is True:
        raise ResetBlocked("package", "A fixture package is not the school")
    modules = package.get("modules") or []
    ids = [row[0] for row in SLOTS]
    if [module.get("module_id") for module in modules] != ids:
        raise ResetBlocked("package", "Content package does not match the 21 module ids")
    coverage = package.get("coverage_qa") or {}
    if any(coverage.get(key) is not True for key in COVERAGE_KEYS):
        raise ResetBlocked("package", "Coverage matrix is incomplete")
    for module in modules:
        lessons = module.get("lessons") or []
        if not lessons:
            raise ResetBlocked("package", "A module is missing a lesson")
        for lesson in lessons:
            if len(str(lesson.get("body") or "")) < 800:
                raise ResetBlocked("package", "A lesson is not substantial")
            if len(str(lesson.get("worked_example") or "")) < 200:
                raise ResetBlocked("package", "A worked example is missing")
            if len(str(lesson.get("paper_exercise") or "")) < 100:
                raise ResetBlocked("package", "A paper exercise is missing")
        questions = module.get("questions") or []
        if not 10 <= len(questions) <= 12:
            raise ResetBlocked("package", "Each module needs 10 to 12 questions")
        sources = {item.get("source_id") for item in module.get("sources") or []}
        if not sources or None in sources:
            raise ResetBlocked("package", "Cited sources are missing")
        seen = set()
        for question in questions:
            if question.get("question_id") in seen:
                raise ResetBlocked("package", "Question ids must be stable and unique")
            seen.add(question["question_id"])
            choices = question.get("choices") or []
            if len(choices) != 4 or not isinstance(question.get("correct"), int):
                raise ResetBlocked("package", "A question is not a four-choice item")
            if not 0 <= question["correct"] <= 3:
                raise ResetBlocked("package", "A question is not a four-choice item")
            if question.get("source_id") not in sources:
                raise ResetBlocked("package", "A question is missing its citation")
    if "version" not in package:
        raise ResetBlocked("package", "Content package is not valid")
    package["certification"] = False
    return package


def _select_pool(module, prior):
    pools = {(item.get("pool") or "A") for item in module.get("questions") or []}
    if "B" not in pools:
        return "A"
    last = prior[-1].get("pool") if prior else ""
    if last == "A":
        return "B"
    if last == "B":
        return "mixed"
    return "A"


def _public_question(question):
    return {
        "choices": list(question["choices"]),
        "prompt": question["prompt"],
        "question_id": question["question_id"],
    }


def _mark(value):
    try:
        mark = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ResetBlocked("pass_mark", "Pass mark is not valid") from exc
    if mark < Decimal("0.50") or mark > Decimal("1"):
        raise ResetBlocked("pass_mark", "Pass mark is not valid")
    return mark


def load_author_draft(path=None):
    """Load the current unpublished draft. Does not rewrite answer keys."""
    import json
    path = path or os.path.join(os.path.dirname(__file__), "school_content", "draft", "course.json")
    if not os.path.isfile(path):
        return None
    with open(path, encoding="utf-8") as handle:
        data = json.load(handle)
    if data.get("schema") != "tts-course":
        raise ResetBlocked("package", "draft package is not a school course")
    if data.get("version") not in {"1.0.0", "1.1.0", "1.1.1", "1.2.0", "1.2.1"}:
        raise ResetBlocked("package", "draft package version is not recognised")
    return data


def load_course_version(version):
    folder = {"1.0.0": "v1.0.0", "1.1.0": "v1.1.0", "1.1.1": "v1.1.1", "1.2.0": "v1.2.0", "1.2.1": "v1.2.1"}.get(version)
    if folder is None:
        raise ResetBlocked("version", "course version is not stored")
    path = os.path.join(os.path.dirname(__file__), "school_content", folder, "course.json")
    return load_author_draft(path)


def quality_gate(draft):
    review = (draft or {}).get("content_review") or {}
    return {
        "compliance_review": bool(review.get("compliance_review")),
        "expert_certified": bool(review.get("expert_certified")),
        "fact_review": "pending",
        "financial_expert_approval": False,
        "legal_review": bool(review.get("legal_review")),
        "published": False,
        "word_count_is_completion": False,
        "reasons": [
            "external expert, legal, and compliance review is open",
            "a human reread of inherited conceptual items is open",
            "some explanations are still terse",
            "11 modules have no alternate question pool",
            "APRA minima and ISM methodology are not primary-verified here",
            "no auction-imbalance dataset and no deflated Sharpe",
            "lesson word count is not a completion criterion",
        ],
    }


DATASET_DIR = os.path.join(os.path.dirname(__file__), "school_content", "v1.2.0", "datasets")
EXERCISE_FILES = ("synthetic_mini_backtest.csv", "synthetic_corporate_actions.csv")


def synthetic_exercise():
    """Stored teaching totals for the invented series. This does not rerun a model."""
    import json
    with open(os.path.join(DATASET_DIR, "results.json"), encoding="utf-8") as handle:
        results = json.load(handle)
    chosen = results["chosen_lookback_by_in_sample_honest_net"]
    in_sample = next(row for row in results["in_sample_by_lookback"] if row["lookback"] == chosen)
    return {
        "adjusted_split_return": results["split"]["split_adjusted_return_on_split_day"],
        "disclaimer": results["disclaimer"],
        "edge_claim": False,
        "files": list(EXERCISE_FILES),
        "in_sample_net": in_sample["honest_with_cost"]["total_return"],
        "label": "SYNTHETIC",
        "look_ahead_in_sample": in_sample["lookahead_with_cost"]["total_return"],
        "lookback": chosen,
        "out_of_sample_net": results["out_of_sample_for_chosen_lookback"]["honest_with_cost"]["total_return"],
        "published": False,
        "raw_split_return": results["split"]["raw_close_return_on_split_day"],
        "seed": results["seed"],
        "series": "synthetic csv",
    }


def synthetic_exercise_file(name):
    if name not in EXERCISE_FILES:
        raise Denied("that file is not the synthetic exercise")
    with open(os.path.join(DATASET_DIR, name), "rb") as handle:
        return handle.read()


def load_package(path):
    if not path:
        return None
    package = read_json(path, None)
    if package is None:
        return None
    return validate_package(package)
