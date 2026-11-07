"""Synthetic disk-backed brain tests; no models, live brains, books or services."""
import importlib.util
import json
from pathlib import Path
import sys
import types

import pytest


@pytest.fixture
def brain(tmp_path, monkeypatch):
    path = Path(__file__).parents[1] / "seat_brain.py"
    spec = importlib.util.spec_from_file_location("isolated_readback_brain", path)
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    monkeypatch.setattr(module, "DIR", str(tmp_path/"brains"))
    monkeypatch.setattr(module, "BOOK", str(tmp_path/"book.json"))
    monkeypatch.setattr(module, "STATUS", str(tmp_path/"nightly.json"))
    (tmp_path/"book.json").write_text('{"seats":{}}')
    return module


@pytest.mark.parametrize("cap", [900, 1100])
def test_saturated_brain_keeps_newest_full_lesson_and_provenance(brain, cap):
    b=brain;seat="essentials_utilities"
    b.write_facts(seat,["Long standing fact "+str(i)+" "+"x"*210 for i in range(20)])
    for i in range(8):b.journal(seat,"chat","Oversized journal "+str(i)+" "+"y"*340)
    b.lesson(seat,"miss","OLD-LESSON "+"z"*580,ref="older-unique")
    text="NEWEST-START "+"e"*500+" Next time: UNIQUE-CORRECTION-AT-THE-END"
    b.lesson(seat,"miss",text,ref="latest-unique")
    prompt=b.prompt_block(seat,cap)
    assert len(prompt)<=cap
    assert "THF seat="+seat in prompt
    assert "ref=latest-unique" in prompt and text in prompt
    assert "UNIQUE-CORRECTION-AT-THE-END" in prompt


@pytest.mark.parametrize("cap", [900,1100])
def test_review_is_qualified_and_structured_check_survives(brain,cap):
    seat="healthcare"
    brain.write_facts(seat,["Old fact "+"x"*300]*8)
    brain.lesson(seat,"review","A recorded lesson " + "a"*280,
                 ref="learning:fm-20261104133001-c57d:"+seat,status="hypothesis",
                 hypothesis="This is a testable idea, not a verified rule.",
                 next_check="Check ONLY the next five own-paper outcomes; keep existing risk rules.")
    text=brain.prompt_block(seat,cap)
    assert len(text)<=cap and "UNVERIFIED HYPOTHESIS" in text
    assert "This is a testable idea, not a verified rule." in text
    assert "Check ONLY the next five own-paper outcomes; keep existing risk rules." in text


def test_eighteen_separate_disk_brains_roundtrip(brain):
    seats=["banks","crypto","energy","essentials_utilities","fx_commodities","healthcare",
           "macro","news_filings","price_action","tech_ai_infra","trend","head_of_desk",
           "head_trader","software_engineer","cfo","blind_challenger","devils_advocate","fixer"]
    for seat in seats:
        assert brain.lesson(seat,"review","unique-own-note-"+seat,ref="own-"+seat,status="hypothesis")
    for seat in seats:
        fresh=brain.read_lessons(seat,10)
        assert len(fresh)==1 and fresh[0]["seat"]==seat
        prompt=brain.prompt_block(seat,900)
        assert "unique-own-note-"+seat in prompt and "ref=own-"+seat in prompt
        assert all(("ref=own-"+other+"]") not in prompt for other in seats if other!=seat)


def test_reader_has_no_writes_and_strips_frozen_today(brain):
    brain.write_facts("banks",["Identity; today A$9999, snapshot."])
    brain.lesson("banks","miss","Do not duplicate another seat.",ref="r")
    before={str(p):p.read_bytes() for p in Path(brain.DIR).rglob("*") if p.is_file()}
    text=brain.prompt_block("banks",1100)
    assert "today A$9999" not in text
    assert before=={str(p):p.read_bytes() for p in Path(brain.DIR).rglob("*") if p.is_file()}


@pytest.mark.parametrize("cap", [0,1,50,150,300,900,1100])
def test_budget_is_hard_and_never_raises(brain,cap):
    brain.lesson("banks","miss","x"*590+" LASTCHECK",ref="r"*100)
    assert len(brain.prompt_block("banks",cap))<=cap


def test_closure_ingestion_remains_idempotent_and_book_readonly(brain):
    b=brain;book={"seats":{"banks":{"closed":[dict(id="miss-1",ticker="ABC",status="cancelled",why="unfilled")]}}}
    raw=json.dumps(book);Path(b.BOOK).write_text(raw)
    assert len(b.lessons_from_book())==1
    assert b.lessons_from_book()==[]
    assert len(b.read_lessons("banks",20))==1
    assert Path(b.BOOK).read_text()==raw


@pytest.mark.parametrize("ai", [False, True])
def test_failed_or_disabled_nightly_preserves_prior_learned_facts(brain, monkeypatch, tmp_path, ai):
    b=brain;seat="banks";monkeypatch.setattr(b,"BASE",str(tmp_path))
    b.write_facts(seat,["Learned fact one","Learned fact two","Learned fact three"],"verified-review")
    original=Path(b._d(seat),"facts.md").read_bytes()
    monkeypatch.setattr(b,"ensure",lambda *a,**k:dict(name="Bank",role="proposer"))
    monkeypatch.setattr(b,"lessons_from_book",lambda **k:[])
    monkeypatch.setattr(b,"code_facts",lambda *a,**k:["fresh deterministic observation"])
    monkeypatch.setattr(b,"review_prompt",lambda *a:"synthetic review")
    def unavailable(*a,**k):raise RuntimeError("provider cooling")
    package=types.ModuleType("veteran_desk")
    package.seat_registry=types.SimpleNamespace(mail_ids=lambda S:[seat])
    package.claude_brain=types.SimpleNamespace(ask=unavailable)
    monkeypatch.setitem(sys.modules,"veteran_desk",package)
    monkeypatch.delenv("PYTEST_CURRENT_TEST",raising=False)
    result=b.nightly([seat],ai=ai,S={seat:{}})
    assert Path(b._d(seat),"facts.md").read_bytes()==original
    snapshot=json.loads(Path(b._d(seat),"code_snapshot.json").read_text())
    assert snapshot["facts"]==["fresh deterministic observation"]
    assert snapshot["ai_review_completed"] is False and snapshot["standing_facts_preserved"] is True
    assert result["seats"][seat]["ai_review_completed"] is False
    assert "nightly_deferred" in Path(b._d(seat),"journal.md").read_text()


@pytest.mark.parametrize("cap", [900,1100])
def test_max_legacy_review_never_loses_main_lesson(brain,cap):
    text="A"*280+" | Hypothesis: "+"H"*100+" | Next: "+"N"*150
    brain.lesson("essentials_utilities","review",text,ref="r"*100,status="hypothesis",hypothesis="H"*150,next_check="N"*160)
    result=brain.prompt_block("essentials_utilities",cap)
    assert text in result and result.count("Hypothesis:")==1 and len(result)<=cap


@pytest.mark.parametrize("cap", [900,1100])
def test_max_new_review_fields_fit_with_provenance(brain,cap):
    seat="s"*41
    brain.lesson(seat,"review","L"*300,ref="r"*100,status="hypothesis",hypothesis="H"*140,next_check="N"*150)
    result=brain.prompt_block(seat,cap)
    assert all(x in result for x in ("L"*300,"H"*140,"N"*150,"UNVERIFIED HYPOTHESIS","ref="+"r"*100))
    assert len(result)<=cap
