"""Dashboard inbox. Facts first, then an optional bridge phrase. Actions are signed.

The inbox binds to localhost. The Tailscale dashboard proxies to it.
"""
import os

from thf_guardian.actions import parse, pause_engine, resume_engine, set_seat_pause
from thf_guardian.responder import answer
from thf_guardian.signing import issue, verify
from thf_guardian.takeover import act, is_on, takeover_off, takeover_on
from thf_guardian.util import append_jsonl, wita_str


def _thread(cfg, role, text, now, extra=None):
    row = dict(role=role, text=text, ts_utc=now.isoformat(), ts_wita=wita_str(now), paper_only=True)
    if extra:
        row.update(extra)
    append_jsonl(cfg.thread, row)
    return row


def handle_message(cfg, snap, text, now, secret, used, io, confirm=None, bridge=None):
    ans = answer(text, snap, bridge=bridge)
    _thread(cfg, "brett", text or "", now)
    action = ans["action"]
    if action["kind"] == "queue" or action["kind"] == "reject":
        _thread(cfg, "guardian", ans["answer"], now, dict(queued_for_head=True))
        io["head"](ans["answer"])
        return ans
    if confirm is None:
        token = issue(secret, action["kind"], action, now, snap.get("screen_rev") or 0)
        ans["token"] = token
        ans["answer"] = ans["answer"] + "\nConfirm to run %s." % action["kind"]
        _thread(cfg, "guardian", ans["answer"], now, dict(needs_confirm=True))
        return ans
    ok, why = verify(secret, confirm, now, snap.get("screen_rev") or 0, used)
    if not ok:
        ans.update(ok=False, error=why, executed=None, needs_confirm=False)
        _thread(cfg, "guardian", "rejected: " + why, now)
        return ans
    executed = _execute(cfg, snap, action, now, secret, used, io, confirm)
    if executed.get("ok"):
        used.add(confirm["body"]["nonce"])
    ans.update(needs_confirm=False, executed=executed, queued_for_head=not executed.get("ok", True) and executed.get("queued"))
    _thread(cfg, "guardian", executed.get("detail") or action["kind"], now, dict(executed=True, kind=action["kind"]))
    return ans


def _execute(cfg, snap, action, now, secret, used, io, token):
    kind = action["kind"]
    if kind == "restart":
        io["systemctl"](action["how"], action["unit"])
        return dict(ok=True, unit=action["unit"], how=action["how"], detail="restarted %s" % action["unit"])
    if kind == "health":
        status = io["health"]()
        return dict(ok=True, detail="health re-ran", status_ok=status.get("ok"))
    if kind == "pause_engine":
        pause_engine(cfg, now, "funder")
        return dict(ok=True, detail="ENGINE_KILL set; tick pauses. Stops do not run until the tick is resumed.")
    if kind == "resume_engine":
        resume_engine(cfg)
        return dict(ok=True, detail="ENGINE_KILL removed")
    if kind == "fill":
        try:
            io["fill"](action.get("id"))
        except Exception as exc:
            return dict(ok=False, detail=str(exc))
        return dict(ok=True, detail="filled %s at armed entry" % action.get("id"))
    if kind == "push":
        plan = io["push"](action.get("seats") or [])
        return dict(ok=True, detail="push planned", plan=plan)
    if kind == "takeover_on":
        takeover_on(cfg, now)
        return dict(ok=True, detail="takeover on")
    if kind == "takeover_off":
        takeover_off(cfg)
        return dict(ok=True, detail="takeover off")
    if kind in ("pause_seat", "resume_seat"):
        if not is_on(cfg):
            return dict(ok=False, queued=True, detail="seat pause needs takeover")
        for seat in action.get("seats") or []:
            set_seat_pause(cfg, seat, kind == "pause_seat")
        return dict(ok=True, detail=kind, seats=action.get("seats"))
    if kind in ("flatten", "takeover_trade"):
        if not is_on(cfg):
            return dict(ok=False, queued=True, detail="kill, flatten, arm, close and stop moves need takeover")
        result = act(cfg, snap.get("book") or {}, snap.get("brains") or {}, token, now, snap.get("screen_rev") or 0,
                     secret, used, marks=snap.get("marks"), notional_fn=io.get("notional"), cost_fn=io.get("cost"))
        if result.get("book") is not None:
            io["write_book"](result["book"])
        return dict(ok=result.get("ok", False), detail=result.get("error") or kind, result={k: v for k, v in result.items() if k != "book"})
    return dict(ok=False, queued=True, detail="not executed")
