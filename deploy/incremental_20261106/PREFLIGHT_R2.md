# Preflight r2 — the seven live paths

The `d601fe4` archive is unchanged and is not apply-ready. This r2 archive records a reviewed base for each of the seven. Apply still refuses any other hash. `book.py` stays `b4453b503f33e34110519bf4d7a9bf391ecc2802edbd87acca3e77dc96b7e891`.

| Path | Reviewed live base | Payload change |
|---|---|---|
| `asia_intraday_sgp.py` | `be9bc8592f1d6e0792a9e3b58aa20c57c572952a2d8e2d3787e94e39f8e5a053` | Session check uses `feed_health.asia_session_open`. Status uses underlying bars. |
| `thf_feedkeeper.py` | `a6b435ff1c00af63c2d275382709b90bc14ea72c778a8e4e12bceafa604023db` | Empty or old in-session bars are `degraded`. A closed session is `closed`. |
| `tools/intraday_bars_pull.py` | `e382d170ec55c6eb2a93be99049687e6ce6467281650402449f891967faa825d` | Non-US names stay inside the REST cap. `quote_stream` is false. |
| `tools/thf_chat.html` | `2209a95bfa5b0856fb5d4d13144f4672f4049a67e6699484c1ac6ebf791fd1f4` | Comment only. The embed is still `#dchat`. |
| `tools/thf_chat_page.html` | `d2cf8aa0b9fd7cc6beaeeecd5f6e689f1c881086937f1bb1ebc6eb56d4344bd1` | Transparent Trading title and paper label. The node is still `#dchat` `data-mode=page`. |
| `tools/thf_eli10.html` | `503a2c6aecb908904aa464d3cf3d7aaf173c988471ce2cba094d1c27e24601f9` | Live page kept. Header is Transparent Trading, PAPER, not a live broker. |
| `tools/thf_eli10.js` | `c085f878b7c0fbc8eda40c757b312fcbfd1853cf0f6397b2d7d5a13015270bc8` | Live renderer kept, including schedules, charts, seats, and health. Tab icons are words. Chat uses `data-mode=hub` and hides the refresh bar on that tab. Both WITA clocks stay. |

`veteran_desk/thf_chat_home.py` in this r2 payload is `788b979aaedbb681383500db7695a256f91d72700833cb160a7b3e16d31959c6`, the same bytes as the stall payload. After this main apply, the stall dry-run shows that file as `CURRENT`. The stall base `b1184435dc3ccad8771133e59b26852d3ab184171ae35e383c7601f0119de7ff` remains for a tree that already has the `d601fe4` main payload. Apply main first, then stall. Do not restart `tfo-thf-tick`.

This is not a seven-day result and not a commercial launch.
