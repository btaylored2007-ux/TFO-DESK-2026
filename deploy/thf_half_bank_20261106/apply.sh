#!/bin/sh
# This file is not the installer. It refuses a second half-bank policy.
# The guarded copy is deploy/incremental_20261106/install.py (dry-run by default).
echo "refusing: this script does not install. Use deploy/incremental_20261106/install.py --root <tree> (dry-run). See docs/THF_HALF_BANK_20261106.md" >&2
exit 1
