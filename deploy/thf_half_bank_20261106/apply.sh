#!/bin/sh
# Paper-only install of the THF +0.7R half-bank module.
# Does not edit book.py, ledgers, fills, or services.
# Refuses to copy if the destination module already exists with a different hash.
set -eu
ROOT="${1:-/opt/tfo/live/thf}"
HERE=$(CDPATH= cd -- "$(dirname "$0")" && pwd)
SRC="$HERE/../../veteran_desk/thf_half_bank.py"
DEST="$ROOT/veteran_desk/thf_half_bank.py"
if [ ! -f "$SRC" ]; then
  echo "missing source $SRC" >&2
  exit 2
fi
NEW=$(sha256sum "$SRC" | awk '{print $1}')
if [ -f "$DEST" ]; then
  OLD=$(sha256sum "$DEST" | awk '{print $1}')
  if [ "$OLD" != "$NEW" ]; then
    echo "refuse: $DEST hash $OLD != package $NEW" >&2
    exit 3
  fi
  echo "already installed $NEW"
  exit 0
fi
install -m 0644 "$SRC" "$DEST"
echo "installed $DEST $NEW"
echo "book.py was not modified. The tick does not call this module until the live hook in docs/THF_HALF_BANK_20261106.md is applied to the droplet book.py."
