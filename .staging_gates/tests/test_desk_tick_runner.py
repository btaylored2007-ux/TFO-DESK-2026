"""Exercise only the shell runner function, never the financial heartbeat."""
from pathlib import Path
import subprocess
import tempfile
import unittest


SCRIPT = Path(__file__).resolve().parents[1] / "desk_tick.sh"


class RunnerTests(unittest.TestCase):
    def test_failed_step_recorded_and_next_step_runs(self):
        source = SCRIPT.read_text()
        function = source[source.index("FAILED_STEPS=0"):source.index("run desk_healer.py")]
        with tempfile.TemporaryDirectory() as directory:
            log = Path(directory) / "tick.log"
            body = function + '''
PY=/usr/bin/false
run synthetic_failure "$2"
PY=/usr/bin/true
run synthetic_success "$2"
printf '%s' "$FAILED_STEPS"
'''
            result = subprocess.run(["bash", "-c", 'LOG="$1"\n' + body,
                                     "test", str(log), directory],
                                    capture_output=True, text=True, check=True)
            self.assertEqual(result.stdout, "1")
            self.assertIn("STEP FAILED exit=1: synthetic_failure", log.read_text())
            self.assertIn("-- synthetic_success", log.read_text())

    def test_retired_writer_not_invoked(self):
        active = [line for line in SCRIPT.read_text().splitlines()
                  if line.lstrip().startswith("run ")]
        self.assertFalse(any("worldwide_gap_book.py" in line for line in active))

    def test_script_syntax(self):
        subprocess.run(["bash", "-n", str(SCRIPT)], check=True)


if __name__ == "__main__":
    unittest.main()
