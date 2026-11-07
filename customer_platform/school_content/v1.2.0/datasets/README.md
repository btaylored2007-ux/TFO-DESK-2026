# SYNTHETIC mini-backtest (not market data)

Files:

- `synthetic_mini_backtest.csv` — 180 weekday-dated rows labelled SYNTHETIC. Timezone of the date column: Australia/Sydney. Columns are date, timezone, open, high, low, close, volume, tradable_flag, split_adjustment_factor, series_label, note. There is no next-return or other future-looking input column, and there is no order book.
- `synthetic_corporate_actions.csv` — the invented 2-for-1 split.
- `compute_backtest.py` — generator and calculator. Seed `20261107`.
- `results.json` — reproducible totals written by the script.

Re-run:

```
python3 datasets/compute_backtest.py
```

What the file is for: practising split adjustment versus raw prices, halt/missing prints, honest lagged signals versus same-bar look-ahead, costs of 10 bps each way, and choosing a lookback on the first 120 rows then checking the unused last 60 rows.

What it is not: historical prices, evidence of a tradable edge, an alpha claim, or something you can send to a live book.
