"""THF seats. Trader brains are per seat id. Dex has two seats, each with its own brain.

Admin brains: Marcus (head), Sloane (blind challenger), Jonah (devil's advocate).
The risk gate is code (gate_v2), not an AI brain.
"""

TRADERS = (
    ("fx_commodities", "Lena", "Lena Voss"),
    ("energy", "Rex", "Rex Calder"),
    ("crypto", "Kai", "Kai Navarro"),
    ("tech_ai_infra", "Nico", "Nico Hart"),
    ("trend", "Dex", "Dex Romano"),
    ("price_action", "Dex", "Dex Romano"),
    ("macro", "Theo", "Theo Ashby"),
    ("banks", "Victor", "Victor Crane"),
    ("essentials_utilities", "Nora", "Nora Whitlock"),
    ("healthcare", "Iris", "Iris Mendel"),
    ("news_filings", "Quinn", "Quinn Mercer"),
)
ADMIN = (
    ("head_of_desk", "Marcus", "Marcus Hale"),
    ("blind_challenger", "Sloane", "Sloane Pike"),
    ("devils_advocate", "Jonah", "Jonah Grieve"),
)
# risk_gate is Ada's code gate, shown on the dashboard as code, never probed as a model.
CODE_SEATS = (("risk_gate", "Ada", "Ada Stroud"),)

NAME_TO_SEATS = {}
for sid, short, _full in TRADERS + ADMIN:
    NAME_TO_SEATS.setdefault(short.lower(), []).append(sid)
    NAME_TO_SEATS.setdefault(sid, [sid])

HUNT_SEATS = [sid for sid, _s, _f in TRADERS]
AI_SEATS = [sid for sid, _s, _f in TRADERS + ADMIN]
SEAT_LABEL = {sid: "%s (%s)" % (short, sid) for sid, short, _f in TRADERS + ADMIN + CODE_SEATS}


def resolve_seats(text):
    """Map 'all', 'Lena', 'Dex' onto seat ids. Dex -> trend and price_action."""
    raw = (text or "").lower()
    tokens = raw.replace(",", " ").replace("/", " ").split()
    token_set = set(tokens)
    if "everyone" in token_set or "all seats" in raw or "all traders" in raw or ("all" in token_set and ("seat" in token_set or "trader" in token_set or "traders" in token_set)):
        return list(HUNT_SEATS), []
    if token_set <= {"all"} or raw.strip() == "all":
        return list(HUNT_SEATS), []
    found = []
    known = set(NAME_TO_SEATS)
    for tok in tokens:
        if tok not in known:
            continue
        for sid in NAME_TO_SEATS[tok]:
            if sid not in found:
                found.append(sid)
    return found, []
