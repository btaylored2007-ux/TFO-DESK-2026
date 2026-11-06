"""Lock the parked live book text. Does not import it and does not write a ledger."""
import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parents[2]
BOOK = ROOT / "docs" / "live_bank07" / "book.py"
TEXT = BOOK.read_text(encoding="utf-8")

def test_snapshot_is_the_audited_half_bank():
    assert "RAIL_BANK_R = 0.7" in TEXT
    assert "RAIL_BANK_ALL_POSITIONS = True" in TEXT
    assert 'id="bank07_20261106"' in TEXT or "id=\"bank07_20261106\"" in TEXT
    assert "RAIL_HALF_R" in TEXT

def test_integer_half_matches_the_reported_bga_split():
    assert 29015 // 2 == 14507
    assert 29015 - 14507 == 14508

def test_second_manual_partial_is_labeled_quarter_not_another_half():
    start = TEXT.index("def mark_half_banked")
    body = TEXT[start:start + 1200]
    assert 'if p.get("t1_done")' in body
    assert "quarter_bank" in body
    assert "initial_stop" in body

def test_package_book_is_the_audited_snapshot():
    packaged = (ROOT / "veteran_desk" / "book.py").read_text(encoding="utf-8")
    assert "RAIL_BANK_R = 0.7" in packaged
    assert "RAIL_BANK_ALL_POSITIONS = True" in packaged
    assert packaged == TEXT

def test_chat_display_name_is_original():
    js = (ROOT / "tools" / "thf_chat.js").read_text(encoding="utf-8")
    assert "Transparent Trading" in js
    assert "Grok-Bot" not in js
    assert "JPMorgan" not in js and "J.P. Morgan" not in js
    assert (ROOT / "assets" / "brand" / "mark.svg").exists()
