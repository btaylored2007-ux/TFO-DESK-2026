"""Head Trader only switch (Brett TFO mandate, 2026-09-25).

While reports/HEAD_TRADER_ONLY.flag exists, engine code may MARK but must never
ticket: no automatic exits, no automatic entries. Only the Head Trader tickets,
via tfo_ticket.py. Remove the flag to hand trading back to the engine.
"""
import os
FLAG = os.path.join(os.path.dirname(os.path.abspath(__file__)), "reports", "HEAD_TRADER_ONLY.flag")

def active():
    return os.path.exists(FLAG)
