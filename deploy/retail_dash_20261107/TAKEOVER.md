# Retail dashboard handover — 2026-11-07

This file is inside the review archive. It cannot quote that archive's own sha256. The checksum written beside the archive is `deploy/retail-dash-20261107.tar.gz.sha256`. Member hashes are `CHECKSUMS.sha256` in this directory.

## What is already on the dash

19:17:06 WITA supported release applied retail package `9d58be9e311b7e279bbb6549a7039c4ecd90abeefd4ca0cdc59d1e22d8f14b96` to both trees. Served hashes matched disk:

- `tools/thf_eli10.html` `9da1e4ac511c8173eb02bf2c460100ccb8e7fdfbb14352708429d95cfbf453ee`
- `tools/thf_eli10.js` `c4a977ad2ca04131e22355cc62e32fa04d075dfc88243758e7eab04a03bd8718`
- `tools/thf_chat.js` `ef3e4def8fd3dcc39093895ddf433d0633f3dfc3a6faa800bb9cfb615e461c44`
- `tools/thf_chat_page.html` `92d98ecd72b0dbc6231f725a953ff33927c37708f1c2267051ac949f8b6646e3`

`thf_dash.py` and `thf_chat_home.py` were already the target and were not rewritten. Those four hashes are `also_base_sha256` on this bundle, after the 08:32 hashes. A tree at either baseline can be pinned. This bundle has not been applied. No SSH. No production write.

The owner phone shot at 20:11 WITA is `review/owner-live-phone-20261107-2011.png`. It shows the 19:17 page: navy header, `UP · SINCE 06:00 WITA · PAPER`, `+A$311`, 7 open, 1 waiting. That is the live screen. It is not a picture of this review bundle, and it is not phone acceptance of the bundle.

## What this review changes

UI files only. Diff against the 19:17 bytes: `review/since-19-17.patch`.

The main card still leads with the 06:00 WITA figure the leaderboard already sends (`today_txt` / `today_clock`). Under it, three lines use fields `home()` already returns from the book:

- Capital = `seed_txt` (`seed_aud`)
- Equity = `equity_txt` (`equity_aud`)
- Since inception = `since_txt` (`pnl_since_inception_aud`, equity minus seed, the book's own net)

The client does not add closed trades to open marks. If a field is missing the line shows an em dash, not zero.

Book source: `pnl_since_inception_aud` is equity minus seed. Prize credits add the same amount to cash and to seed, so they stay out of that difference. Costs, open marks, hedge P&L, and FX inside equity are inside it. Invested is not capital. There is no separate external-flow line. The on-screen label is equity minus capital.

Since reset is a second line. It is current equity-minus-seed minus a mark saved later. The difference is 0 only when those two book figures still match. The UI does not hardcode 0. No baseline file is shipped. The earlier 12:12:34 reading was withdrawn and is not a reset. A fresh mark is taken only after the read model is on the dash. `thf_home.py` in this repo is not the live file and is not in either payload.

`deploy/display_release_20261107/control.py` is fail-closed source. Without a sanitized capture it prints `WAITING_CAPTURE` and `NOT_DEPLOYMENT_READY`. It is not a production apply and it does not replace the installers' `REFUSED_ROOT` checks. The reviewed 19:17 runner is not in this checkout.

Header and full-page chat header use the cream page color, not navy. The price-chart canvas stays dark so the plotted lines remain visible. Figures use the system font already on the phone, with tabular numbers. No font file is downloaded. The five tabs stay: Home, Trades, Chat, Seats, Health.

## References that were actually on this machine

- `review/etoro-user-nav-photo.png` — owner's eToro portfolio navigation photo. Navigation evidence only.
- `review/etoro-style-review.md` and `review/visual-acceptance-brief.md` — style notes. The store sheet is marketing. It does not show a logged-in tab bar.
- OANDA owner photos were not on this machine. No OANDA layout was reviewed or copied.
- `review/local-preview-home-facts-390-not-live.png` is a local Chrome emulation of this source with a fixture book (`+A$1,240` since 06:00, capital `A$100,000`, equity `A$104,200`, since inception `+A$4,200`). It is not the Singapore book and not a live render.

## Not claimed

Physical phone visual, send, or upload acceptance. A speed gain. A live visual match. An inception figure of zero. A capital reset.

## Operator

The installer still returns `REFUSED_ROOT` for `/opt/tfo/live`, `/opt/tfo/dash`, and any git checkout. Staging steps are in `ROLLBACK.txt`. Do not restart tick, engine, or book. Do not edit nginx.
