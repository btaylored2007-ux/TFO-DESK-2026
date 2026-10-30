#!/usr/bin/env python3
"""autonomous_deploy.py — the deterministic rail for Option 2 autonomous deployment.

PAPER ONLY. Given a mechanism <name> with a lab receipt reports/<name>_lab.json and a
probe module <name>_probe.py, this script DECIDES (it does not trust the caller) whether
the mechanism qualifies under AUTONOMOUS_DEPLOYMENT_POLICY.md, and only then seals a
pre-registration, registers a $10k paper probe, and logs+flags a notification. It refuses
everything else. It never edits frozen registrations, never touches the engine code,
never places a real order.

Usage:  python3 autonomous_deploy.py <name>
Exit 0 = deployed; exit 2 = refused (reason printed); exit 3 = disarmed/halted.
"""
import hashlib
import importlib
import json
import os
import re
import sys
import time

import capital_admission
import desk_lock

BASE = os.path.dirname(os.path.abspath(__file__))
TDIR = os.path.join(BASE, "reports", "tournament")
REGISTRY = os.path.join(TDIR, "auto_probes.json")
LOG = os.path.join(BASE, "reports", "autonomous_deployments.log")
ARMED_FLAG = os.path.join(BASE, "AUTONOMY_ARMED")
HALT_FLAG = os.path.join(BASE, "AUTONOMY_HALT")

CAPITAL = 10000.0
MAX_CONCURRENT = 5
MAX_TOTAL = 50000.0
GATE = {"t_stat": 2.0, "n_effective": 20, "beta_abs": 0.30}
DIAL_FILE = os.path.join(BASE, "reports", "exposure_dial.json")
DIAL_FLOOR = 0.80          # tightening amendment 2026-08-15 (funder-directed dial)
DIAL_MAX_AGE = 48 * 3600   # stale dial -> fail closed


def _armed():
    return os.path.exists(ARMED_FLAG) and not os.path.exists(HALT_FLAG)


def _dial_ok():
    """Exposure-dial check (registration 21e05244c53d4e77): FAIL-CLOSED tightening.
    Refuse new deployments when the firm dial is throttled, stale, or unreadable.
    This check can only ever BLOCK a deployment, never approve one."""
    try:
        age = time.time() - os.path.getmtime(DIAL_FILE)
        if age > DIAL_MAX_AGE or age < -60:
            return False, "exposure dial STALE (%.0fh > 48h) — fail closed" % (age / 3600)
        with open(DIAL_FILE) as f:
            d = json.load(f)
        if d.get("degraded"):
            return False, "exposure dial DEGRADED (partial data) — fail closed"
        s = float(d["scale"])
        # registered range is (0, 1]; NaN/inf/out-of-range all fail this comparison
        if not (0.0 < s <= 1.0):
            return False, "exposure dial scale %r outside registered range — fail closed" % s
        if s < DIAL_FLOOR:
            return False, "exposure dial THROTTLED (scale %.2f < %.2f)" % (s, DIAL_FLOOR)
        return True, ""
    except Exception as e:
        return False, "exposure dial unreadable (%s) — fail closed" % str(e)[:60]


def _find_eval(o):
    """Only the exact root evaluation admitted by capital_admission is usable."""
    return o.get("evaluation") if isinstance(o, dict) else None


def _find_beta(o):
    """Do not substitute a favourable nested beta from a diagnostic plot."""
    return o.get("beta_to_SPY") if isinstance(o, dict) else None


def qualifies(receipt):
    """Returns (ok, reasons[]). ALL gate conditions must hold."""
    admitted, admission_reasons = capital_admission.evaluate_admission(receipt)
    if not admitted:
        return False, admission_reasons
    verified, verification_reasons = capital_admission.verify_artifacts(receipt, BASE)
    if not verified:
        return False, verification_reasons
    reasons = []
    ev = _find_eval(receipt)
    if not ev:
        return False, ["no evaluate() block in receipt"]
    if not ev.get("survives_deflation"):
        reasons.append("survives_deflation False")
    t = ev.get("t_stat")
    if not capital_admission.finite_number(t) or t < GATE["t_stat"]:
        reasons.append("t_stat %.2f < %.1f" % (t or 0, GATE["t_stat"]))
    ne = ev.get("n_effective")
    if ne is None or ne < GATE["n_effective"]:
        reasons.append("n_effective %.1f < %d" % (ne or 0, GATE["n_effective"]))
    ptb = ev.get("per_trade_bps")
    if ptb is None or ptb <= 0:
        reasons.append("net per-trade %.2f bps <= 0" % (ptb or 0))
    beta = _find_beta(receipt)
    if beta is None:
        reasons.append("no beta_to_SPY in receipt (cannot verify low-beta)")
    elif abs(beta) > GATE["beta_abs"]:
        reasons.append("|beta| %.2f > %.2f" % (beta, GATE["beta_abs"]))
    return (len(reasons) == 0), reasons


def _registry():
    try:
        with open(REGISTRY) as f:
            value = json.load(f)
        if not isinstance(value, dict):
            raise ValueError("invalid auto-probe registry")
        return value
    except FileNotFoundError:
        return {}


def _caps_ok(reg):
    live = {k: v for k, v in reg.items() if not v.get("removed")}
    if len(live) >= MAX_CONCURRENT:
        return False, "concurrent cap: %d live auto-probes" % len(live)
    if (len(live) + 1) * CAPITAL > MAX_TOTAL:
        return False, "capital cap: would exceed $%.0f" % MAX_TOTAL
    return True, ""


def _validate_probe(name):
    """Import <name>_probe and confirm current_targets() is a sane dollar-neutral book."""
    try:
        mod = importlib.import_module("%s_probe" % name)
        w, px = mod.current_targets()
    except Exception as e:
        return False, "probe module error: %s" % str(e)[:80], None
    if not w:
        return False, "current_targets() returned no positions", None
    if not isinstance(w, dict) or not isinstance(px, dict):
        return False, "invalid target or price mapping", None
    if any(not capital_admission.finite_number(v) for v in w.values()):
        return False, "non-finite target weight", None
    if any(not isinstance(t, str) or not t for t in w):
        return False, "invalid instrument identity", None
    net = abs(sum(w.values()))
    gross = sum(abs(v) for v in w.values())
    if net > 0.05:
        return False, "not dollar-neutral (|Σw|=%.3f)" % net, None
    if gross > 2.0:
        return False, "gross %.2f > 2.0" % gross, None
    if any(t not in px for t in w):
        return False, "unpriced legs", None
    if any(not capital_admission.finite_number(px[t]) or px[t] <= 0 for t in w):
        return False, "non-finite or non-positive prices", None
    return True, "", (w, px)


def _seal_prereg(name, receipt, spec_hash):
    ev = _find_eval(receipt) or {}
    beta = _find_beta(receipt)
    path = os.path.join(BASE, "%s_PROBE_PREREGISTRATION.md" % name.upper())
    with open(path, "x") as f:
        f.write("# %s — Autonomous Paper Probe Pre-Registration\n\n" % name)
        f.write("**Spec hash: `%s`** — auto-sealed %s under AUTONOMOUS_DEPLOYMENT_POLICY.md.\n"
                "NEVER edit to fit a result.\n\n" % (spec_hash, time.strftime("%Y-%m-%d")))
        f.write("Deployed autonomously because it cleared the full gate:\n")
        f.write("- survives_deflation %s\n- t_stat %s\n- n_effective %s\n"
                "- net per-trade %s bps\n- beta_to_SPY %s\n\n"
                % (ev.get("survives_deflation"), ev.get("t_stat"),
                   ev.get("n_effective"), ev.get("per_trade_bps"), beta))
        f.write("$%.0f paper, monthly rebalance, 25 bps/side modelled, -30%% DD kill, "
                "182-day clock. Receipt: reports/%s_lab.json.\n" % (CAPITAL, name))
    return path


def deploy(name):
    if not isinstance(name, str) or re.fullmatch(r"[a-z][a-z0-9_]{0,63}", name) is None:
        print("REFUSED: invalid mechanism name")
        return 2
    with desk_lock.ledger_lock("autonomous_deploy", timeout=55):
        return _deploy_locked(name)


def _deploy_locked(name):
    if not _armed():
        print("REFUSED: autonomy is DISARMED (need AUTONOMY_ARMED present, AUTONOMY_HALT absent).")
        return 3
    try:
        import book_kill
        allowed, reason = book_kill.firm_entries_allowed("new autonomous capital", verbose=False)
    except Exception as exc:
        allowed, reason = False, "firm risk unavailable: " + type(exc).__name__
    if not allowed:
        print("REFUSED: " + reason)
        return 2
    dok, dwhy = _dial_ok()
    if not dok:
        print("REFUSED: " + dwhy)
        return 2
    rpath = os.path.join(BASE, "reports", "%s_lab.json" % name)
    try:
        with open(rpath) as f:
            receipt = json.load(f)
    except Exception as e:
        print("REFUSED: cannot read %s (%s)" % (rpath, e))
        return 2
    ok, reasons = qualifies(receipt)
    if not ok:
        print("REFUSED by gate: " + "; ".join(reasons))
        return 2
    try:
        reg = _registry()
    except (ValueError, OSError) as exc:
        print("REFUSED: registry unreadable — " + str(exc))
        return 2
    if name in reg and not reg[name].get("removed"):
        print("REFUSED: %s already deployed" % name)
        return 2
    if (os.path.exists(os.path.join(BASE, name.upper() + "_PROBE_PREREGISTRATION.md"))
            or os.path.exists(os.path.join(TDIR, name + ".json"))):
        print("REFUSED: existing registration or ledger; no overwrite or loss reset")
        return 2
    cok, why = _caps_ok(reg)
    if not cok:
        print("REFUSED: " + why + " — reverting to PROPOSE (notify funder).")
        return 2
    try:
        with open(os.path.join(BASE, name + "_probe.py"), "rb") as source:
            probe_hash = hashlib.sha256(source.read()).hexdigest()
        if receipt.get("probe_source_sha256") != probe_hash:
            raise ValueError("probe source is not bound to the validated receipt")
    except (OSError, ValueError) as exc:
        print("REFUSED: " + str(exc))
        return 2
    vok, vwhy, tgt = _validate_probe(name)
    if not vok:
        print("REFUSED: " + vwhy)
        return 2

    spec = {"name": name, "capital": CAPITAL, "kill_dd": -30, "clock_days": 182,
            "cost_bps_side": 25.0, "gate": GATE,
            "eval": _find_eval(receipt), "beta": _find_beta(receipt)}
    spec_hash = hashlib.sha256(
        json.dumps(spec, sort_keys=True, separators=(",", ":"), default=str).encode()
    ).hexdigest()[:16]
    prereg = _seal_prereg(name, receipt, spec_hash)

    # seed the sleeve ledger (dollar-neutral, marked flat at entry)
    os.makedirs(TDIR, exist_ok=True)
    sleeve = {"name": name, "cash": CAPITAL, "holdings": {}, "entry_px": {},
              "last_rebalance": None, "costs": 0.0, "peak": CAPITAL, "start": CAPITAL,
              "frozen": False, "started": time.strftime("%Y-%m-%d"),
              "auto": True, "spec_hash": spec_hash}
    with open(os.path.join(TDIR, name + ".json"), "x") as f:
        json.dump(sleeve, f, indent=2)
        f.flush()
        os.fsync(f.fileno())

    reg[name] = {"probe_module": "%s_probe" % name, "cost_bps": 25.0,
                 "deployed": time.strftime("%Y-%m-%dT%H:%M:%S"),
                 "spec_hash": spec_hash, "removed": False}
    registry_tmp = REGISTRY + ".%d.tmp" % os.getpid()
    with open(registry_tmp, "w") as f:
        json.dump(reg, f, indent=2)
        f.flush()
        os.fsync(f.fileno())
    os.replace(registry_tmp, REGISTRY)

    ev = _find_eval(receipt)
    line = ("DEPLOYED %s  hash=%s  t=%s n_eff=%s beta=%s  seal=%s"
            % (name, spec_hash, ev.get("t_stat"), ev.get("n_effective"),
               _find_beta(receipt), os.path.basename(prereg)))
    with open(LOG, "a") as f:
        f.write("%s  %s\n" % (time.strftime("%Y-%m-%dT%H:%M:%S"), line))
    print("NOTIFY_FUNDER: " + line)
    print("DEPLOYED. Registered auto-probe %s ($%.0f paper). Pre-reg sealed %s."
          % (name, CAPITAL, spec_hash))
    return 0


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("usage: autonomous_deploy.py <name>")
        sys.exit(1)
    sys.exit(deploy(sys.argv[1]))
