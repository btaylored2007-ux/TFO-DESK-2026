"""Same launchd-target check as tests/test_launchd_plist_targets.py, so the veteran suite gates it too."""
import importlib.util
import os

_PATH = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "tests", "test_launchd_plist_targets.py"))
_spec = importlib.util.spec_from_file_location("tfo_launchd_plist_targets_impl", _PATH)
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)


def test_every_launchd_plist_target_exists():
    _mod.test_every_launchd_plist_target_exists()


def test_known_absent_plist_targets_are_exactly_the_preexisting_holes():
    _mod.test_known_absent_plist_targets_are_exactly_the_preexisting_holes()
