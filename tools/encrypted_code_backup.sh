#!/bin/bash
# 2026-10-30 enccode (Brett: no plaintext to droplet). ENCRYPTED CODE backup, sibling of encrypted_state_backup.sh. Daily.
# Replaces the code part of retired plaintext offsite_backup.sh tier1. Dest droplet /root/tfo_backups/enc (code_*.tar.gz.enc), keep 7.
# Same openssl AES-256-CBC+PBKDF2 200k, passphrase file ~/.tfo_secrets/offsite_backup.pass (never printed). Same secret exclusion + refuse-on-match.
# Scope: MultiVenueDesk + KrakenQuantDesk source/config/docs files (<2MB). Excludes .git, backups, data/, reports/, state/ (state is in
# encrypted_state_backup.sh), logs, codex_cutover_*, caches, sqlite/db/parquet, research/work/handover json data.
# Restore: openssl enc -d -aes-256-cbc -pbkdf2 -iter 200000 -pass file:PASSFILE -in X.tar.gz.enc | tar xzf -
set -uo pipefail
DESK="/Users/test/Applications/MultiVenueDesk"; APPS="/Users/test/Applications"
KEY="$HOME/.ssh/id_ed25519_backup"; HOST="root@168.144.170.165"; PORT=8443; REMOTE="/root/tfo_backups/enc"
PASS="$HOME/.tfo_secrets/offsite_backup.pass"
LOG="$DESK/reports/offsite_backup.log"; ST="$DESK/reports/offsite_code_backup_status.json"; MARK="$DESK/reports/.offsite_backup_state"
STAMP=$(date -u +%Y%m%d_%H%M%SZ); KEEP=7; MINGAP=${ENC_BACKUP_MINGAP:-72000}
say(){ echo "$(date -u '+%F %T')Z enccode: $*" >> "$LOG"; }
status(){ printf '{"ok":%s,"at_utc":"%s","detail":"%s","dest":"%s:%s","encryption":"openssl aes-256-cbc pbkdf2 200k"}\n' "$1" "$(date -u +%FT%TZ)" "$2" "${HOST#*@}" "$REMOTE" > "$ST"; }
fail(){ say "ERROR: $*"; status false "$*"; exit 1; }
LAST=$(grep '^enccode ' "$MARK" 2>/dev/null | tail -1 | awk '{print $2}'); NOW=$(date +%s)
if [ -n "${LAST:-}" ] && [ $((NOW-LAST)) -lt "$MINGAP" ] && [ "${1:-}" != "--force" ]; then say "skip: last ok $(( (NOW-LAST)/3600 ))h ago (daily)"; exit 0; fi
[ -s "$PASS" ] || fail "passphrase file missing"
P=$(mktemp -t enccode); E="$P.tar.gz.enc"; trap 'rm -f "$P" "$P.in" "$P.list" "$E"' EXIT
SECRE='(^|/)\.env|\.env$|\.key$|\.pem$|(^|/)id_|secret|credential|token|\.p12$|\.pass$|passphrase'
EX=(--exclude='.env' --exclude='*.env' --exclude='.env.*' --exclude='*.key' --exclude='*.pem' --exclude='id_*' --exclude='*secret*'
    --exclude='*credential*' --exclude='*token*' --exclude='*.p12' --exclude='*passphrase*' --exclude='*.pass' --exclude='__pycache__'
    --exclude='*.bak*' --exclude='*.png' --exclude='*.jpg' --exclude='*.log' --exclude='local_ledger_backup' --exclude='backups')
cd "$APPS" || fail "no apps dir"
find MultiVenueDesk KrakenQuantDesk \( -name .git -o -name backups -o -name __pycache__ -o -name node_modules -o -name .venv -o -name venv \
  -o -path MultiVenueDesk/data -o -path MultiVenueDesk/reports -o -path MultiVenueDesk/state -o -path MultiVenueDesk/logs \
  -o -path KrakenQuantDesk/reports -o -name 'codex_cutover_*' -o -name .kraken_v870_backups -o -name microstructure \
  -o -name gold_basis_cache -o -name local_ledger_backup \) -prune -o -type f -size -2M \
  ! -name '*.sqlite*' ! -name '*.db' ! -name '*.parquet' ! -name '*.log' ! -name '*.bak*' ! -name '*.zip' ! -name '*.png' ! -name '*.jpg' -print 2>/dev/null \
 | grep -E '\.(py|sh|md|toml|ya?ml|txt|plist|html|js|ts|css|cfg|ini|sql|json|command|zsh)$|/(Makefile|Dockerfile|requirements[^/]*)$' \
 | grep -Ev '^MultiVenueDesk/(research|work|handover)/.*\.json$' | grep -Eiv "$SECRE" > "$P.in"
[ -s "$P.in" ] || fail "empty file list"
tar czf "$P" "${EX[@]}" -T "$P.in" 2>/dev/null; [ -s "$P" ] || fail "archive empty"
tar tzf "$P" > "$P.list" || fail "archive unreadable"
BAD=$(grep -Ei '(^|/)\.env|\.env$|\.key$|\.pem$|(^|/)id_|secret|credential|token|\.p12$|\.pass$' "$P.list" | head -3)
[ -z "$BAD" ] || fail "secret-like path in archive, refused: $(echo $BAD | tr -d '\"')"
N=$(wc -l < "$P.list" | tr -d ' ')
openssl enc -aes-256-cbc -pbkdf2 -iter 200000 -salt -pass "file:$PASS" -in "$P" -out "$E" || fail "encrypt failed"
[ "$(head -c 8 "$E")" = "Salted__" ] || fail "ciphertext header missing"
openssl enc -d -aes-256-cbc -pbkdf2 -iter 200000 -pass "file:$PASS" -in "$E" | tar tzf - >/dev/null 2>&1 || fail "decrypt self-test failed"
SSH="ssh -i $KEY -p $PORT -o StrictHostKeyChecking=no -o ConnectTimeout=20 -o BatchMode=yes"
B="code_$STAMP.tar.gz.enc"; SUM=$(shasum -a 256 "$E" | awk '{print $1}'); SZ=$(du -h "$E" | cut -f1)
$SSH "$HOST" "mkdir -p $REMOTE && rm -f $REMOTE/code_*.partial" || fail "droplet unreachable"
ok=0; for a in 1 2 3 4; do rsync -e "$SSH" --partial --timeout=600 "$E" "$HOST:$REMOTE/$B.partial" && { ok=1; break; }; say "upload attempt $a failed"; sleep 30; done
[ $ok = 1 ] || { $SSH "$HOST" "rm -f $REMOTE/$B.partial"; fail "upload failed"; }
RS=$($SSH "$HOST" "sha256sum $REMOTE/$B.partial | cut -d' ' -f1")
[ "$RS" = "$SUM" ] && $SSH "$HOST" "mv -f $REMOTE/$B.partial $REMOTE/$B" || { $SSH "$HOST" "rm -f $REMOTE/$B.partial"; fail "remote checksum mismatch"; }
$SSH "$HOST" "ls -t $REMOTE/code_*.tar.gz.enc 2>/dev/null | tail -n +$((KEEP+1)) | xargs -r rm -f"
echo "enccode $NOW" >> "$MARK"; say "ok $B $SZ files=$N sha256=$SUM"; status true "$B $SZ files=$N"; exit 0
