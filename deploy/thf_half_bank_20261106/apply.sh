#!/bin/sh
# Do not install a second half-bank policy. The live tick already uses book.py RAIL_BANK_R=0.7.
echo "refusing: live tfo-thf-tick already banks half at net +0.7R. See docs/THF_HALF_BANK_20261106.md" >&2
exit 1
