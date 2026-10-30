import contextlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import fundamental_batch_manifest as manifest


class BatchManifestExitTests(unittest.TestCase):
    def test_verify_cli_is_nonzero_after_ready_artifact_is_invalidated(self):
        with tempfile.TemporaryDirectory() as tmp:
            run = Path(tmp)
            (run / "blocks_test.md").write_text("SYMBOL: TEST\n", encoding="utf-8")
            aggregate = run / "aggregate.json"
            aggregate.write_text(
                json.dumps({"blocks_parsed": 1, "unique_symbols": 1}),
                encoding="utf-8",
            )
            manifest.finalize(run)

            with mock.patch.object(
                sys, "argv", ["fundamental_batch_manifest.py", "verify", "--run", str(run)]
            ), contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(manifest.main(), 0)

            aggregate.write_text(
                json.dumps({"blocks_parsed": 1, "unique_symbols": 1, "mutated": True}),
                encoding="utf-8",
            )
            with mock.patch.object(
                sys, "argv", ["fundamental_batch_manifest.py", "verify", "--run", str(run)]
            ), contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(manifest.main(), 2)


if __name__ == "__main__":
    unittest.main()
