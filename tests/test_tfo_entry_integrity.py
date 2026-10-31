"""Entry-only data-integrity gate; no strategy tick or financial writes."""
import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch
_FLOOR = '/Users/test/Applications/KrakenQuantDesk/coinspot_floor.py'
if not os.path.exists(_FLOOR):
    import pytest
    pytest.skip("coinspot_floor.py is not in this tree (KrakenQuantDesk). See TEST_TRIAGE.md.", allow_module_level=True)
sys.path.insert(0, '/Users/test/Applications/KrakenQuantDesk')
spec = importlib.util.spec_from_file_location('coinspot_guard_test', _FLOOR)
floor = importlib.util.module_from_spec(spec)
spec.loader.exec_module(floor)


class EntryIntegrity(unittest.TestCase):
    def test_active_integrity_report_blocks_even_if_risk_gate_allows(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            reports=root/'MultiVenueDesk/reports';reports.mkdir(parents=True)
            (reports/'coinspot_entry_integrity_20260921.json').write_text(
                json.dumps({'block_new_entries':True}))
            with patch.object(floor,'BASE',str(root/'KrakenQuantDesk')), \
                 patch.object(floor,'_kill',SimpleNamespace(entries_allowed=lambda *a,**kw:(True,'OK'))):
                allowed,reason=floor.kill_entries_allowed({},'Breakout')
                self.assertFalse(allowed)
                self.assertIn('reconciliation',reason)

    def test_corrupt_integrity_report_fails_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);reports=root/'MultiVenueDesk/reports';reports.mkdir(parents=True)
            (reports/'coinspot_entry_integrity_20260921.json').write_text('{broken')
            with patch.object(floor,'BASE',str(root/'KrakenQuantDesk')):
                self.assertFalse(floor.kill_entries_allowed({},'Breakout')[0])


if __name__=='__main__':unittest.main()
