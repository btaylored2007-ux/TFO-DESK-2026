"""SEC EDGAR User-Agent. The live desk keeps the contact string in research_controls/sec_contact.json.
This module never prints that file. A missing file (code-only tree, CI) uses a non-secret fallback so
imports do not crash; it is not an API key."""
import json, os

def user_agent():
    cands = []
    env = os.environ.get("SEC_CONTACT_JSON")
    if env:
        cands.append(env)
    here = os.path.dirname(os.path.abspath(__file__))
    cands.append(os.path.join(here, "research_controls", "sec_contact.json"))
    cands.append(os.path.expanduser("~/Applications/MultiVenueDesk/research_controls/sec_contact.json"))
    for p in cands:
        try:
            ua = json.load(open(p)).get("user_agent")
            if ua:
                return ua
        except Exception:
            continue
    return os.environ.get("SEC_USER_AGENT", "TFO-DESK paper research (contact file absent)")
