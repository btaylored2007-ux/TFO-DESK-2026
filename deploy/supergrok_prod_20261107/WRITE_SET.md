# Production candidate write set

Written on all three bound roots, `/opt/tfo/live/thf`, `/opt/tfo/dash/base`, and `/opt/tfo/dash/codemerge`:

- `veteran_desk/thf_msgdesk.py` from base `9607803a718f16041a712544bea1582df7e153a8286cb695b03c92f5ea12376e`
- `veteran_desk/thf_chat_clarity.py` (absent on the preflight trees; this file is new)
- `veteran_desk/supergrok_bridge.py` from base `adbd30076046f5c589baec601dfbdc071eeda129eb4b2c5bd8be925735d04f54`
- `tools/grok_web.py` from base `b7b2fac73e03c600fdb7078b8e55352dc0a5dd52f3a63896fe8e196a7abdae4b`
- `veteran_desk/thf_chat_home.py`
- `veteran_desk/ai_router.py`
- `veteran_desk/ai_readiness.py`
- `veteran_desk/thf_funder.py`

Checked and not written: `book.py` `a739477001c33b23ad87ebdf8ca5fc40c76b441eb13100daf1bc67136eb12c7b` (module pin only; ledger data is not read), `chatgpt_web.py` `cc86f0afeef4e26ca609b669356c95c4f4009b9d571dbd710303d8a4a73779a4`, `chatgpt_bridge.py` `26446b5b61218bfc370fff62988db47efecb7b8afafd15e694ceaecf35deac0c`, `seat_brain.py` `a294b07d3503411ef441dcaa709cc4e98caba8746a3d69ce42c095342a2c7ab0`, `thf_round.py` `8ee00fb7519b2c27b5b32b98e56b29d42d3faa0528f7004e5772ad839b5c78f6`.

The preflight `5b1c028dda1bc25df1abc9a27ba896735dfd34c6a23e4037cfd85606b6ca3f87` found those bases and pins equal on the three roots. Codemerge is a bound write root so it cannot be left stale. It is not a proved service working directory.

Seat brain journals a round. The round runner admits trades and acknowledges mail. Neither posts a second owner chat card.

Not in this write set: the display guard, nginx, engine, tick, firm risk, and any restart of `tfo-dash-thf.service`, `tfo-thf-msgdesk.service`, or `tfo-dash-vd.service`.
