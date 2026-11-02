#!/bin/bash
# Install the THF guardian on tfo-sgp1. PAPER only. Do not run from a laptop.
# Backs up every file it replaces as .bak-<YYYYMMDD>-guardian and appends to the deploy log.
set -euo pipefail
DATE=$(TZ=Asia/Makassar date +%Y%m%d)
BAK=".bak-${DATE}-guardian"
ROOT=$(cd "$(dirname "$0")" && pwd)
LIVE=/opt/tfo/live/thf
DEST="$LIVE/thf_guardian"
DASH_UNIT=/etc/systemd/system/tfo-dash-thf.service
LOG=/opt/tfo/logs/DEPLOY_LOG.txt
if [ "$(id -u)" -eq 0 ]; then SUDO=""; else SUDO="sudo"; fi

backup() {
  local f="$1"
  if [ -e "$f" ]; then
    $SUDO cp -a "$f" "${f}${BAK}"
    echo "backed up $f -> ${f}${BAK}"
  fi
}

$SUDO mkdir -p "$LIVE/state/thf" "$LIVE/logs" /opt/tfo/logs /etc/systemd/system
backup "$DEST"
$SUDO rm -rf "$DEST"
$SUDO mkdir -p "$DEST"
$SUDO cp -a "$ROOT/." "$DEST/"
$SUDO find "$DEST" -name '.bak-*' -delete || true

for unit in tfo-thf-guardian.service tfo-thf-guardian-check.service tfo-thf-guardian-check.timer; do
  backup "/etc/systemd/system/$unit"
  $SUDO cp "$ROOT/systemd/$unit" "/etc/systemd/system/$unit"
done

patch_dash() {
  local f="$1"
  [ -f "$f" ] || return 0
  if grep -q tfo_guardian_20261102 "$f"; then
    echo "dash already patched: $f"
    return 0
  fi
  backup "$f"
  $SUDO tee -a "$f" >/dev/null <<'PY'

# --- tfo_guardian_20261102 begin ---
try:
    from thf_guardian.dash_mount import install as _gd_install
    _gd_install(H, sys.modules.get(__name__))
except Exception as _gde:
    import sys as _gsys
    _gsys.stderr.write("thf_guardian mount failed: %s\n" % _gde)
# --- tfo_guardian_20261102 end ---
PY
}

patch_dash "$LIVE/veteran_desk/thf_dash.py"
patch_dash /opt/tfo/dash/base/veteran_desk/thf_dash.py

if [ -f "$DASH_UNIT" ] && ! grep -q 'PYTHONPATH=/opt/tfo/live/thf' "$DASH_UNIT"; then
  backup "$DASH_UNIT"
  $SUDO sed -i 's|^Environment=\(.*\)$|Environment=\1 PYTHONPATH=/opt/tfo/live/thf|' "$DASH_UNIT" || true
fi

DESK="$LIVE/veteran_desk/desk.py"
if [ -f "$DESK" ] && ! grep -q tfo_guardian_armguard_20261102 "$DESK"; then
  backup "$DESK"
  $SUDO tee -a "$DESK" >/dev/null <<'PY'

# --- tfo_guardian_armguard_20261102 begin ---
try:
    import thf_guardian.arm_guard as _AG
    _AG.install(sys.modules[__name__])
except Exception as _age:
    pass
# --- tfo_guardian_armguard_20261102 end ---
PY
fi

KEY="$LIVE/state/thf/guardian_action.key"
if [ ! -s "$KEY" ]; then
  $SUDO mkdir -p "$LIVE/state/thf"
  $SUDO bash -c "umask 077; head -c 32 /dev/urandom | od -An -tx1 | tr -d ' \n' > '$KEY'"
fi

MAN="$LIVE/state/thf/approved_code.json"
if [ ! -f "$MAN" ]; then
  $SUDO /opt/tfo/venv/bin/python - <<PY
import hashlib, json, os
live = "$LIVE"
files = ["veteran_desk/book.py","veteran_desk/desk.py","veteran_desk/gate_v2.py","veteran_desk/vd_engine.py","veteran_desk/thf_dash.py","run_thf_tick_sgp.sh"]
out = {}
for rel in files:
    p = os.path.join(live, rel)
    if os.path.isfile(p):
        h = hashlib.sha256()
        h.update(open(p,"rb").read())
        out[rel] = h.hexdigest()
json.dump(dict(files=out, note="baseline written by thf_guardian install; drift is alerted, not reverted"), open("$MAN","w"), indent=1)
PY
fi

$SUDO mkdir -p /opt/tfo/logs
echo "$(TZ=Asia/Makassar date '+%F %T WITA') thf_guardian install PAPER $DATE" | $SUDO tee -a "$LOG" >/dev/null
$SUDO systemctl daemon-reload
$SUDO systemctl enable tfo-thf-guardian.service tfo-thf-guardian-check.timer
echo "installed. Review, then: systemctl restart tfo-thf-guardian.service tfo-dash-thf.service && systemctl start tfo-thf-guardian-check.timer"
echo "rollback: restore *.bak-${DATE}-guardian and systemctl daemon-reload"
