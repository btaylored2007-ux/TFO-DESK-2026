import datetime as dt
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import private_wealth_exit_refresh as runner


class ExitRunnerTests(unittest.TestCase):
    def setup_fixture(self, root):
        reports=root/'reports';reports.mkdir()
        active={'cycle_id':'fixture','expires_utc':(dt.datetime.now(dt.timezone.utc)+dt.timedelta(minutes=9)).isoformat()}
        (reports/'private_wealth_gate.json').write_text(json.dumps({'active':active}))
        ledger=root/'ledger.json';ledger.write_text('{"positions":{}}')
        return reports,[('one',root/'unused.py',[],ledger)]

    def test_complete_is_not_reexecuted(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);reports,routes=self.setup_fixture(root)
            with patch.object(runner,'command',return_value={'returncode':0,'timed_out':False}) as call, patch.object(runner,'refresh_maker_evidence',return_value={'read_only':True}):
                result=runner.run('fixture',root,root/'archives',routes)
                self.assertEqual(result['status'],'COMPLETE')
                self.assertEqual(runner.run('fixture',root,root/'archives',routes),result)
                self.assertEqual(call.call_count,1)
            self.assertFalse((reports/'.tick.lock').exists())

    def test_busy_never_deletes_lock_or_launches(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);reports,routes=self.setup_fixture(root);(reports/'.tick.lock').mkdir()
            with patch.object(runner,'command') as call:
                with self.assertRaises(RuntimeError):runner.run('fixture',root,root/'archives',routes)
                call.assert_not_called()
            self.assertTrue((reports/'.tick.lock').exists())

    def test_timeout_stops_remaining_routes_and_refuses_replay(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);reports,routes=self.setup_fixture(root);routes=routes+[('two',*routes[0][1:])]
            with patch.object(runner,'command',return_value={'returncode':-15,'timed_out':True}) as call:
                result=runner.run('fixture',root,root/'archives',routes)
                self.assertEqual(result['status'],'RECOVERY_REQUIRED');self.assertEqual(call.call_count,1)
                with self.assertRaises(ValueError):runner.run('fixture',root,root/'archives',routes)


if __name__=='__main__':unittest.main()
