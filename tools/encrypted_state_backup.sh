#!/bin/bash
# 2026-10-30 encbackup (Brett: cloud instead of thumb drive). ENCRYPTED, ledgers+state ONLY, daily.
# Destination, identity and passphrase live in an untracked env file (mode 600), never in this script.
# Encryption: openssl AES-256-CBC + PBKDF2 (200k iter, salted). Plaintext never leaves this Mac.
# Archive is refused if its listing contains any secret-like path. Root SSH is refused.
set -uo pipefail
DESK="/Users/test/Applications/MultiVenueDesk"; APPS="/Users/test/Applications"
CFG="${OFFSITE_BACKUP_ENV:-$HOME/.tfo_secrets/offsite_backup.env}"
LOG="$DESK/reports/offsite_backup.log"; ST="$DESK/reports/offsite_backup_status.json"; MARK="$DESK/reports/.offsite_backup_state"
STAMP=$(date -u +%Y%m%d_%H%M%SZ); KEEP=14; MINGAP=${ENC_BACKUP_MINGAP:-72000}
say(){ echo "$(date -u '+%F %T')Z enc: $*" >> "$LOG"; }
status(){ printf '{"ok":%s,"at_utc":"%s","detail":"%s","encryption":"openssl aes-256-cbc pbkdf2 200k"}\n' "$1" "$(date -u +%FT%TZ)" "$2" > "$ST"; }
fail(){ say "ERROR: $*"; status false "$*"; exit 1; }
[ -f "$CFG" ] || fail "offsite config missing"
set -a; . "$CFG"; set +a
HOST="${OFFSITE_SSH_HOST:?}"; PORT="${OFFSITE_SSH_PORT:?}"; KEY="${OFFSITE_SSH_KEY:?}"
REMOTE="${OFFSITE_REMOTE_DIR:?}"; PASS="${OFFSITE_PASSFILE:?}"
if [ "${HOST%%@*}" = "root" ]; then fail "root SSH is refused"; fi
LAST=$(grep '^enc ' "$MARK" 2>/dev/null | tail -1 | awk '{print $2}'); NOW=$(date +%s)
if [ -n "${LAST:-}" ] && [ $((NOW-LAST)) -lt "$MINGAP" ] && [ "${1:-}" != "--force" ]; then say "skip: last ok $(( (NOW-LAST)/3600 ))h ago (daily)"; exit 0; fi
[ -s "$PASS" ] || fail "passphrase file missing"
P=$(mktemp -t encbk); E="$P.tar.gz.enc"; trap 'rm -f "$P" "$P.list" "$E"' EXIT
EX=(--exclude='.env' --exclude='*.env' --exclude='.env.*' --exclude='*.key' --exclude='*.pem' --exclude='id_*' --exclude='*secret*'
    --exclude='*credential*' --exclude='*token*' --exclude='*.p12' --exclude='*passphrase*' --exclude='*.pass' --exclude='__pycache__'
    --exclude='*.bak*' --exclude='*.png' --exclude='*.jpg' --exclude='*.log' --exclude='local_ledger_backup' --exclude='backups')
cd "$APPS" || fail "no apps dir"
SRC=(MultiVenueDesk/state); for f in MultiVenueDesk/reports/*.json MultiVenueDesk/reports/*.jsonl KrakenQuantDesk/kraken_bot/*.json; do [ -f "$f" ] && SRC+=("$f"); done
H=(); for d in "$HOME"/.tfo_swing "$HOME"/.tfo_swing_us "$HOME"/.tfo_honest10 "$HOME"/.tfo_edge50 "$HOME"/.tfo_btc_pounce; do [ -d "$d" ] && H+=("$(basename "$d")"); done
tar czf "$P" "${EX[@]}" "${SRC[@]}" -C "$HOME" "${H[@]}" 2>/dev/null; [ -s "$P" ] || fail "archive empty"
tar tzf "$P" > "$P.list" || fail "archive unreadable"
BAD=$(grep -Ei '(^|/)\.env|\.env$|\.key$|\.pem$|(^|/)id_|secret|credential|token|\.p12$|\.pass$' "$P.list" | head -3)
[ -z "$BAD" ] || fail "secret-like path in archive, refused: $(echo $BAD | tr -d '\"')"
N=$(wc -l < "$P.list" | tr -d ' ')
openssl enc -aes-256-cbc -pbkdf2 -iter 200000 -salt -pass "file:$PASS" -in "$P" -out "$E" || fail "encrypt failed"
openssl enc -d -aes-256-cbc -pbkdf2 -iter 200000 -pass "file:$PASS" -in "$E" | tar tzf - >/dev/null 2>&1 || fail "decrypt self-test failed"
SSH="ssh -i $KEY -p $PORT -o StrictHostKeyChecking=no -o ConnectTimeout=20 -o BatchMode=yes"
B="state_$STAMP.tar.gz.enc"; SUM=$(shasum -a 256 "$E" | awk '{print $1}'); SZ=$(du -h "$E" | cut -f1)
$SSH "$HOST" "mkdir -p $REMOTE && rm -f $REMOTE/*.partial" || fail "droplet unreachable"
ok=0; for a in 1 2 3 4; do rsync -e "$SSH" --partial --timeout=600 "$E" "$HOST:$REMOTE/$B.partial" && { ok=1; break; }; say "upload attempt $a failed"; sleep 30; done
[ $ok = 1 ] || { $SSH "$HOST" "rm -f $REMOTE/$B.partial"; fail "upload failed"; }
RS=$($SSH "$HOST" "sha256sum $REMOTE/$B.partial | cut -d' ' -f1")
[ "$RS" = "$SUM" ] && $SSH "$HOST" "mv -f $REMOTE/$B.partial $REMOTE/$B" || { $SSH "$HOST" "rm -f $REMOTE/$B.partial"; fail "remote checksum mismatch"; }
$SSH "$HOST" "ls -t $REMOTE/state_*.tar.gz.enc 2>/dev/null | tail -n +$((KEEP+1)) | xargs -r rm -f"
echo "enc $NOW" >> "$MARK"; say "ok $B $SZ files=$N sha256=$SUM"; status true "$B $SZ files=$N"; exit 0
