# Firm book (PAPER)

One read of every desk ledger, in Australian dollars, plus a daily check against the paper brokers. The ledgers stay the books of record. This job does not trade and does not edit them.

## What it reads

| Desk | Ledger | In the firm total |
| --- | --- | --- |
| Main TFO book | `reports/value_book.json`, `reports/gap_close_levered_book.json`, `reports/carry_paper_book.json`, `reports/tournament/*.json`, `replica_floor.json` | Yes |
| Veteran Desk | `reports/veteran_desk_book.json` | Yes |
| Taylor Hedge Fund | `state/thf/thf_book.json` | Yes |
| Trend18 | `state/trend18.json` | Yes |
| Kraken / crypto sleeves | `reports/crypto_*.json`, `reports/tfo_ht_crypto.json`, and the Kraken desk floor / bot state when those files are on this Mac | Yes |
| Fundamental | `reports/fundamental_book.json`, `reports/fundamental_spec_book.json` | Yes |
| Hunt books | `state/hunt48_*/ledger.json` | Yes |
| OANDA sleeves | `state/oanda/snapshot.json` (AGGRO `state/oanda_aggro/stats.json` is a view of the same account) | No. The practice NAV is the whole account. Adding it would invent capital. |
| V2 | `reports/v2/tfo_v2_book.json` | No. V2 is a separate paper fund. |

A missing file is absent, not zero. A file that is present but unreadable is an error, and that desk is withheld from the firm total. `firm.complete` is false until every desk that belongs in the total has an equity.

## What it writes

- `state/firm_book.json` — positions, cash, equity, and exposure per desk and for the firm, in A$.
- `static/firm_book.json` — the same document, so the war-room card can fetch `/static/firm_book.json`.
- `reports/recon_YYYYMMDD.json` — the broker check. The date is the Asia/Makassar (WITA) date.

The writer refuses any other path. It will not replace a ledger.

## AUDUSD and the weekend freeze

The rate is `veteran_desk.book.audusd` (sane band 0.40–1.20). That helper already keeps a last-good rate for a short window and refuses an invented 0.65.

When the FX market is inside the desk's weekend freeze — New York Friday 17:00 through Sunday 17:00, and 25 December / 1 January, from `tfo_equity_refresh.fx_market_closed` — a last-good rate inside the closed-market window (72 hours) is kept and labelled `frozen`. Outside that freeze, a failed live rate fails the conversion. USD desks are withheld. The firm total is not published as a partial sum.

## Reconciliation

Mapped desks are compared to the paper broker that holds them:

- OANDA practice (`api-fxpractice.oanda.com` only) — the OANDA snapshot's trades and AUD balance.
- Kraken private Balance and OpenPositions — the crypto sleeves. The key stays in `~/.tfo_secrets/kraken.env` (`KRAKEN_API_KEY` / `KRAKEN_API_SECRET`). It is not printed.
- IBKR paper gateway `127.0.0.1:4002`, read-only. Any other port is refused.

Kraken and OANDA are looked up through the existing DNS-over-HTTPS pins (`kraken_dns.py`, `desk_doh.py`). System DNS on this network sinkholes `api.kraken.com`.

A break is a quantity gap above 0.0001 (or 0.01% of the larger size, whichever is greater) or a cash gap above A$1 (or 0.1%, whichever is greater).

These are not matches:

- the broker did not answer (`UNREACHABLE`)
- the key file is missing (`NO_CREDENTIALS`)
- the broker returned an error
- the desk ledger is absent (`NO_BOOK`) — absence is not a flat book
- cash on a mapped desk was not in the ledger (`CASH_UNMEASURED`)
- `--book-only` (`NOT_RUN`)

Exit code 0 means the book was valued and every mapped broker matched. Exit code 2 means the rate failed, a broker was unread, or a break was flagged. The report is still written.

## Firm risk service

When `veteran_desk.exec_speed.firm_risk_service` is importable (the pending exec-speed work), the book calls its read-only `query`. This job does not publish that service's state and does not copy its rails. If the module is not on the tree, `firm_risk_service.present` is false. No risk snapshot is invented. `reports/firm_risk.json` is copied across as `legacy_firm_equity_usd` for comparison and is not added in.

Paid-feed files are left untouched.

## Schedule

`launchd/com.tfo.firm-book.plist` runs every day at 05:30. launchd uses the Mac clock. This desk's clock is Asia/Makassar, so that is 05:30 WITA.

```bash
cp launchd/com.tfo.firm-book.plist ~/Library/LaunchAgents/
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.tfo.firm-book.plist
```

On demand:

```bash
launchctl start com.tfo.firm-book
# or, from the desk root:
python3 firm_book.py
python3 firm_book.py --book-only
```

Logs: `logs/firm-book.log` and `logs/firm-book.err.log`.

## The card

The war-room simple view (`command_dashboard.html`, `#firmbook`) reads the static copy. It shows firm equity, each desk, and whether each broker matched, broke, or was unread.

## Tests

```bash
python3 -m unittest tests.test_firm_book_20261031
```

The tests use fixture ledgers and mocked broker calls. They do not open a socket and they do not read `~/.tfo_secrets`.
