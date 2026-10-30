#!/bin/bash
# 2026-10-30 enccode: RETIRED (Brett: no plaintext backups to the droplet). Original kept as offsite_backup.sh.bak-20261030-enccode.
# Encrypted replacements: tools/encrypted_state_backup.sh (ledgers/state) + tools/encrypted_code_backup.sh (code),
# run daily by launchd com.tfo.offsite-backup via tools/run_offsite_backup.sh. This stub uploads NOTHING.
echo "$(date -u '+%F %T')Z offsite_backup.sh RETIRED — plaintext upload refused; use tools/run_offsite_backup.sh (encrypted)" >> /Users/test/Applications/MultiVenueDesk/reports/offsite_backup.log
exit 0
