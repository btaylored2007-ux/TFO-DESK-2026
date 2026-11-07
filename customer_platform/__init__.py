"""Hosted customer web app template. Off unless a caller passes enabled=True.

The first product is a browser app: signup, login, sessions, and an isolated
dashboard with a private learning brain. It is not a desktop install and not a
self-host distribution. Marketing pages and a public leaderboard are later, and
this package does not publish private performance.

The package is not mounted on the owner desk. It does not import the live book,
the gate, or the owner learning loop.
"""

VERSION = "2026.11.07"

__all__ = ["VERSION"]
