import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from veteran_desk import desk as DK
def test_note_has_no_markdown_bold():
    n = DK.compose_note({"picks": [("Theo Ashby", "SHORT", "EURUSD=X", "A")]}); assert "**" not in n and n.startswith("Desk note - ")
