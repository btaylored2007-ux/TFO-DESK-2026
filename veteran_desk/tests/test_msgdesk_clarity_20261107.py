"""Call-path checks for reply, report, and colleague routing. Synthetic text only."""
from veteran_desk import seat_mail as SM
from veteran_desk import thf_funder as TF
from veteran_desk import thf_msgdesk as MD


def test_identical_report_stays_in_the_mailbox(monkeypatch):
    cards = []
    delivered = []
    monkeypatch.setattr(MD, "log", lambda *args, **kwargs: None)
    monkeypatch.setattr(MD, "tadd", lambda **kwargs: cards.append(kwargs))
    monkeypatch.setattr(SM, "deliver", lambda *args, **kwargs: delivered.append(args))
    monkeypatch.setattr(SM, "report_target", lambda seat: "head_trader")
    monkeypatch.setattr(SM, "targets", lambda seat, body: {})
    MD._seat_mail("crypto", "owner question", {"reply": "alpha answer", "report_to_marcus": "alpha answer"}, "alpha answer", root="req-1")
    assert delivered and not cards


def test_different_report_remains_visible(monkeypatch):
    cards = []
    monkeypatch.setattr(MD, "log", lambda *args, **kwargs: None)
    monkeypatch.setattr(MD, "tadd", lambda **kwargs: cards.append(kwargs))
    monkeypatch.setattr(SM, "deliver", lambda *args, **kwargs: None)
    monkeypatch.setattr(SM, "report_target", lambda seat: "head_trader")
    monkeypatch.setattr(SM, "targets", lambda seat, body: {})
    MD._seat_mail("crypto", "owner question", {"reply": "alpha answer", "report_to_marcus": "different formal note"}, "alpha answer", root="req-1")
    assert cards and cards[0]["kind"] == "report" and cards[0]["text"] == "different formal note"


def test_same_text_forward_does_not_open_another_request(monkeypatch):
    posts = []
    monkeypatch.setattr(MD, "log", lambda *args, **kwargs: None)
    monkeypatch.setattr(MD, "tadd", lambda **kwargs: None)
    monkeypatch.setattr(SM, "deliver", lambda *args, **kwargs: None)
    monkeypatch.setattr(SM, "report_target", lambda seat: "head_trader")
    monkeypatch.setattr(SM, "targets", lambda seat, body: {"macro": "alpha answer"})
    monkeypatch.setattr(SM, "send", lambda *args, **kwargs: (True, "ok"))
    monkeypatch.setattr(TF, "post", lambda *args, **kwargs: posts.append(args))
    MD._seat_mail("crypto", "owner question", {"reply": "alpha answer"}, "alpha answer", root="req-1")
    assert posts == []


def test_different_forward_still_reaches_the_other_seat(monkeypatch):
    posts = []
    monkeypatch.setattr(MD, "log", lambda *args, **kwargs: None)
    monkeypatch.setattr(MD, "tadd", lambda **kwargs: None)
    monkeypatch.setattr(SM, "deliver", lambda *args, **kwargs: None)
    monkeypatch.setattr(SM, "report_target", lambda seat: "head_trader")
    monkeypatch.setattr(SM, "targets", lambda seat, body: {"macro": "please look at a different question"})
    monkeypatch.setattr(SM, "send", lambda *args, **kwargs: (True, "ok"))
    monkeypatch.setattr(TF, "post", lambda *args, **kwargs: posts.append(args))
    MD._seat_mail("crypto", "owner question", {"reply": "alpha answer"}, "alpha answer", root="req-1")
    assert posts and posts[0][2] == "macro" and "FROM SEAT crypto to macro" in posts[0][1]


def test_hop_limit_stops_another_forward(monkeypatch):
    posts = []
    monkeypatch.setattr(MD, "log", lambda *args, **kwargs: None)
    monkeypatch.setattr(SM, "deliver", lambda *args, **kwargs: None)
    monkeypatch.setattr(SM, "targets", lambda seat, body: {"macro": "another question"})
    monkeypatch.setattr(SM, "send", lambda *args, **kwargs: (True, "ok"))
    monkeypatch.setattr(TF, "post", lambda *args, **kwargs: posts.append(args))
    MD._seat_mail("banks", "FROM SEAT crypto to banks [hop 2]: prior", {"reply": "stop"}, "stop", root="req-2")
    assert posts == []


def test_production_candidate_fixture():
    import subprocess
    import sys
    from pathlib import Path
    script = Path(__file__).resolve().parents[2] / "deploy/supergrok_prod_20261107/tests/fixture_matrix.py"
    run = subprocess.run([sys.executable, str(script)], text=True, capture_output=True, check=False)
    assert run.returncode == 0, run.stdout + run.stderr
    assert run.stdout.strip() == "FIXTURE_OK"


def test_colleague_prompt_is_not_labelled_as_brett():
    assert MD._speaker_line("FROM SEAT crypto to banks [hop 1]: hello").startswith("A desk colleague")
    assert MD._speaker_line("owner question").startswith("Brett")
    note = MD._independent_note(["claude"], {"claude": {"reply": "one view"}})
    assert "not a peer review" in note and "All reviewed" not in note
