# Source still required

These live files were redacted in the chat-clarity capture, so this package does not patch them. Send the unredacted source only. Do not send conversation logs, ledgers, or account records.

- `/opt/tfo/live/thf/veteran_desk/thf_msgdesk.py` sha256 `9607803a718f16041a712544bea1582df7e153a8286cb695b03c92f5ea12376e`
- `/opt/tfo/live/thf/veteran_desk/seat_brain.py` sha256 `a294b07d3503411ef441dcaa709cc4e98caba8746a3d69ce42c095342a2c7ab0`
- `/opt/tfo/live/thf/veteran_desk/thf_round.py` sha256 `8ee00fb7519b2c27b5b32b98e56b29d42d3faa0528f7004e5772ad839b5c78f6`

The synthetic clarity fixtures do not prove those three files are free of duplicate delivery or context drift.

SuperGrok is not connected. A later capture that would show a real page session, without cookies or tokens, is a fresh `GET http://127.0.0.1:18892/health` body after the worker exists, plus one owner-requested reply record whose route is `grok_com_page_ui`. This package does not create that session.
