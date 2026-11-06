"""ChatGPT ask lifecycle: one browser job survives disconnect, timeout, and retry."""
import io
import json
import threading
import time
import urllib.error

import pytest

from tools import chatgpt_web as W
from veteran_desk import chatgpt_bridge as CG


@pytest.fixture(autouse=True)
def isolated(monkeypatch):
    import queue
    monkeypatch.setattr(W, "Q", queue.Queue())
    monkeypatch.setattr(W, "BUSY", [False])
    monkeypatch.setattr(W, "RESULTS", {})
    monkeypatch.setattr(W, "ADMISSION", type(W.ADMISSION)(capacity=2))
    monkeypatch.setattr(W, "_receipt", lambda *a, **k: None)
    monkeypatch.setattr(W, "_request_finished", lambda *a, **k: None)


def handler(body):
    raw = json.dumps(body).encode()
    answers = []
    fake = type("H", (), {})()
    fake.headers = {"Content-Length": str(len(raw))}
    fake.rfile = io.BytesIO(raw)
    fake._j = lambda status, value: answers.append((status, value)) or True
    return fake, answers


def posted(**extra):
    body = dict(prompt="no order", timeout=120, deadline=time.time() + 180,
                request_id="stall-1", skip_if_busy=False, workload="other")
    body.update(extra)
    return handler(body)


def finish_queued(text="finished", ok=True):
    def run():
        for _ in range(100):
            if not W.Q.empty():
                job = W.Q.get()
                W.ADMISSION.finish(job, dict(ok=ok, text=text, error=None if ok else text))
                W.retain(job)
                return
            time.sleep(0.01)
    threading.Thread(target=run).start()


def test_busy_skip_does_not_queue():
    assert W.ADMISSION.reserve(dict(request_id="held", seat="a", deadline=time.time() + 180, workload="other"), False)[0]
    fake, answers = posted(skip_if_busy=True, request_id="other")
    W.H.do_POST(fake)
    assert answers[0][0] == 409 and W.Q.empty()


def test_normal_completion_returns_the_answer_once():
    finish_queued("done")
    fake, answers = posted()
    W.H.do_POST(fake)
    assert answers[0][0] == 200 and answers[0][1]["text"] == "done"
    before = W.Q.qsize()
    again, second = posted()
    W.H.do_POST(again)
    assert second[0][1]["text"] == "done" and W.Q.qsize() == before


def test_ack_then_poll_does_not_create_a_second_job():
    finish_queued("kept")
    fake, answers = posted(ack=True)
    W.H.do_POST(fake)
    assert answers[0][0] == 202 and answers[0][1]["state"] == "pending"
    for _ in range(100):
        if W.lookup("stall-1") and W.lookup("stall-1")["state"] == "done":
            break
        time.sleep(0.01)
    polled, got = handler({})
    polled.path = "/result?request_id=stall-1"
    W.H.do_GET(polled)
    assert got[0][0] == 200 and got[0][1]["text"] == "kept"
    assert W.Q.empty()


def test_disconnect_keeps_the_result_for_retry():
    finish_queued("saved")
    fake, answers = posted()
    def broken(status, value):
        answers.append((status, value))
        if status == 200:
            raise BrokenPipeError("client gone")
        return True
    fake._j = broken
    W.H.do_POST(fake)
    assert W.STATUS["last_delivery"] == "client_disconnected"
    retry, second = posted()
    W.H.do_POST(retry)
    assert second[0][1]["text"] == "saved" and W.Q.empty()


def test_waiting_client_timeout_is_explicit_and_does_not_start_another_job():
    job = dict(request_id="slow", deadline=time.time() + 0.05, done=__import__("threading").Event(),
               cancelled=__import__("threading").Event(), result=None, state="running")
    with W.ADMISSION.lock:
        W.ADMISSION.active["slow"] = job
    fake, answers = handler({})
    W.deliver(fake, job, True)
    assert answers[0][0] == 504 and answers[0][1]["state"] == "timeout"
    assert job["cancelled"].is_set() and W.Q.empty()


def test_error_result_is_returned_without_a_retry_job():
    finish_queued("bridge exploded", ok=False)
    fake, answers = posted()
    W.H.do_POST(fake)
    assert answers[0][0] == 200 and answers[0][1]["ok"] is False
    assert W.Q.empty()


def test_bridge_client_acks_once_and_polls_the_same_id(monkeypatch):
    monkeypatch.setattr(CG, "enabled", lambda: True)
    monkeypatch.setattr(CG, "busy", lambda *a, **k: False)
    monkeypatch.setattr(CG, "health", lambda: {"delivery": "ack_poll_v1", "state": "logged_in"})
    monkeypatch.setattr(CG, "_T", [])
    calls = []
    def urlopen(request, timeout=None):
        calls.append((request.full_url, getattr(request, "data", None)))
        if request.full_url.endswith("/ask"):
            return io.BytesIO(b'{"ok":true,"accepted":true,"state":"pending"}')
        return io.BytesIO(b'{"ok":true,"state":"done","text":"polled"}')
    monkeypatch.setattr(CG.urllib.request, "urlopen", urlopen)
    assert CG.ask("no order", timeout=90, request_id="same-id") == "polled"
    posts = [body for url, body in calls if url.endswith("/ask")]
    assert len(posts) == 1 and json.loads(posts[0])["ack"] is True
    assert json.loads(posts[0])["request_id"] == "same-id"
