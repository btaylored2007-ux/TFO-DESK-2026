"""The phone hub shell is Transparent Trading paper chrome, and it does not invent P&L."""
from veteran_desk import thf_home as H

def test_hub_page_uses_the_paper_brand_and_keeps_both_clocks():
    html = H.page_html({}, "/")
    assert "Transparent Trading" in html
    assert "Taylor Hedge Fund" not in html
    assert "PAPER" in html
    assert "Not a live broker" in html
    assert "Since 06:00 WITA" in html
    assert "Since midnight WITA" in html
    assert "A$7,276" not in html
    assert "JPMorgan" not in html and "J.P. Morgan" not in html
    assert 'data-mode="hub"' in html
    assert "<!--HOME0-->" not in html
    assert len(html) > 2000

def test_classic_title_is_the_same_paper_name():
    text = open("veteran_desk/thf_dash.html", encoding="utf-8").read()
    assert "<title>Transparent Trading · Paper</title>" in text
    assert "Taylor Hedge Fund" not in text
