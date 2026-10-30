# Maker-calibration coverage denominator correction — preregistered 2026-09-02

PAPER reporting only. Written before the code correction.

`book_coverage._load_maker_probe()` currently assigns the probe's changing
current `equity` to `start_usd`. This makes reported inception return exactly
zero regardless of the actual result. The probe registration and state both
carry the fixed book size in `params.book_usd` (US$10,000).

The loader will use only `params.book_usd` as the contributed-capital
denominator. If it is absent or invalid, start remains unknown and a warning is
raised; current equity is never substituted. Equity, positions, strategy rules,
orders and capital are unchanged.

Acceptance: a US$10,000 start and US$9,617.44 mark report the two distinct
figures; missing registered book size is loud and does not become a zero return.

## Implementation receipt — 2026-09-02

Implemented. Two focused tests pass. The live loader now reports start
US$10,000, equity US$9,617.44 and return -3.826%, rather than 0.00%.

```text
book_coverage.py                  bee26f02794ddfa1223fba8583d8056e06d6d1ebca7c6b09c0b9d7498c72ee5c
tests/test_book_coverage_maker.py cc0b14b5ea35eae4d3ef7d9f496248cbe3ee003e240940d6d1fce2c08a963abc
```
