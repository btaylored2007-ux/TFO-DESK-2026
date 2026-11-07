"""Retail phone hub: provider status stays off the default screens, and brand files have a route."""
import io
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CHAT = (ROOT / "tools" / "thf_chat.js").read_text(encoding="utf-8")
ELI = (ROOT / "tools" / "thf_eli10.js").read_text(encoding="utf-8")
PAGE = (ROOT / "tools" / "thf_eli10.html").read_text(encoding="utf-8")


class Headers(dict):
    def get_all(self, key):
        value = self.get(key)
        return [value] if value is not None else []


def test_retail_templates_do_not_paint_provider_status():
    from veteran_desk import thf_home as H
    html = H.page_html({}, "/")
    assert "The AI brains" not in ELI
    assert "Every seat in plain words" not in ELI
    assert "h.ais" not in ELI and "t.brains" not in ELI
    assert "Page checked " not in ELI
    assert "Figures built " in ELI
    assert 'g!=="info"&&g!=="ai"' in ELI
    assert "/api/ai_status" not in ELI and "/api/ai_status" not in CHAT
    assert "admin=1" not in ELI and "admin=1" not in CHAT
    assert "debug=1" not in ELI and "debug=1" not in CHAT
    assert "[?&]admin" not in ELI and "[?&]debug" not in CHAT
    assert "dc-aisw" not in CHAT and "code reply" not in CHAT and "AI was down" not in CHAT
    assert "min-height:0" in CHAT and 'id=dc-more hidden' in CHAT and "imp_more" in CHAT
    assert "id=dc-t" in CHAT and "id=dc-send" in CHAT and "id=dc-to" in CHAT
    seats = ELI[ELI.index("R.seats=function"):ELI.index("/* ---- Health")]
    assert seats.index("Traders") < seats.index("t.push")
    assert 'href="/assets/brand/apple-touch-icon.png"' in PAGE
    assert "fetch(\"/api/ai_status\"" not in CHAT
    assert "Transparent Trading" in html and "PAPER" in html
    assert "Not a live broker" in html
    assert "Since 06:00 WITA" in html and "Since midnight WITA" in html
    assert 'href="/assets/brand/favicon.svg"' in html
    assert 'href="/assets/brand/tokens.css"' in html
    assert "JPMorgan" not in html and "J.P. Morgan" not in html
    assert 'data-mode="hub"' in html
    header = PAGE.split("<main", 1)[0]
    assert 'href="/desk/chat"' not in header and "Previous page" not in header
    assert "::-webkit-details-marker{display:none}" in PAGE
    assert "prefers-reduced-motion:reduce" in PAGE
    assert 'class="chev" aria-hidden="true"' in ELI and "›" not in ELI
    assert "replies ▾" not in CHAT and "show more ▾" not in CHAT
    assert "dc-clip" in CHAT and "::-webkit-details-marker" in CHAT


def _get(path):
    from veteran_desk import thf_dash
    sent = {}

    class Get(thf_dash.H):
        def __init__(self):
            self.path = path
            self.headers = Headers({"Host": "127.0.0.1:18870", "Accept-Encoding": ""})
            self.command = "GET"
            self.wfile = io.BytesIO()
            self.close_connection = True

        def send_response(self, code, message=None):
            sent["code"] = code

        def send_header(self, *args):
            pass

        def end_headers(self):
            pass

        def log_message(self, *args):
            pass

    getter = Get()
    getter.do_GET()
    return sent["code"], getter.wfile.getvalue()


def test_brand_map_serves_the_four_files_and_refuses_traversal():
    from veteran_desk import thf_dash
    assert set(thf_dash.BRAND_TYPES) == {"favicon.svg", "mark.svg", "wordmark.svg", "tokens.css", "apple-touch-icon.png"}
    for name, media in thf_dash.BRAND_TYPES.items():
        body, ctype = thf_dash.brand_asset("/assets/brand/" + name)
        raw = (ROOT / "assets" / "brand" / name).read_bytes()
        assert body == raw and ctype == media
        code, wired = _get("/assets/brand/" + name + "?admin=1")
        assert code == 200 and raw in wired
    assert thf_dash.brand_asset("/assets/brand/../book.py") is None
    assert thf_dash.brand_asset("/assets/brand/favicon.svg/../../book.py") is None
    assert thf_dash.brand_asset("/assets/brand/not-a-file.svg") is None
    code, body = _get("/assets/brand/../book.py")
    assert code == 404 and b"not found" in body
    assert thf_dash.brand_asset("/assets/brand/favicon.svg?debug=1")[0].startswith(b"<svg")
    assert thf_dash.brand_asset("/assets/brand/apple-touch-icon.png")[0].startswith(b"\x89PNG")


def test_unread_badge_says_plus_only_when_older_rows_were_dropped():
    from veteran_desk import thf_chat_home as CH
    reg = {"macro": dict(id="macro", name="Theo", desk="Macro", label="Theo", admin=False, star=False, pinned=False, color="#000", av="T", can_send=True)}

    def row(i):
        return dict(k=i, id="m%d" % i, **{"from": "macro"}, to="brett", text="fault %d" % i, ts_wita="2026-11-07 04:00:00", mode="brain")

    exact = CH.summarize("macro", [row(i) for i in range(1, 41)], reg)
    assert exact["imp_more"] is False and len(exact["imp"]) == 40
    dropped = CH.summarize("macro", [row(i) for i in range(1, 50)], reg)
    assert dropped["imp_more"] is True and len(dropped["imp"]) == CH.HOME_IMP_N and dropped["imp"][0] == 10
