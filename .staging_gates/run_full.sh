#!/bin/zsh
cd /Users/test/Applications/MultiVenueDesk/.staging_gates
export PYTHONPATH=/Users/test/Applications/MultiVenueDesk
P=/Library/Frameworks/Python.framework/Versions/3.14/bin/python3
fail=0; n=0
for f in tests/test_*.py; do
  n=$((n+1))
  out=$($P -W ignore "$f" 2>&1 | tail -3 | tr '\n' ' ')
  if echo "$out" | grep -q "OK"; then echo "PASS $f :: $out"; else echo "FAIL $f :: $out"; fail=$((fail+1)); fi
done
echo "FILES $n FAILED $fail"
echo "EXIT $fail"
