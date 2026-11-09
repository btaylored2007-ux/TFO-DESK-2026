"""Identity for an isolated synthetic school fixture.

A relabelled copy of the real course does not match. The real course file
is never published and never rewritten.
"""

import hashlib
import json

FIXTURE_ID = "isolated-synthetic-fixture-not-a-course"
FIXTURE_MODULE = "fixture.only"
FIXTURE_SCHEMA = "tts-synthetic-fixture"
FIXTURE_VERSION = "synthetic-fixture-isolated"
REAL_COURSE_SHA256 = "47710df87eba1bac393bb7bbc85d129add2424fd6b343a7c20e7498585a0f702"
MAX_FIXTURE_BYTES = 12000


def is_isolated_fixture(course, real_bytes):
    if not isinstance(course, dict):
        return False
    if course.get("schema") != FIXTURE_SCHEMA or course.get("fixture_id") != FIXTURE_ID:
        return False
    if course.get("version") != FIXTURE_VERSION:
        return False
    modules = course.get("modules")
    if not isinstance(modules, list) or len(modules) != 1 or modules[0].get("id") != FIXTURE_MODULE:
        return False
    raw = json.dumps(course, sort_keys=True).encode("utf-8")
    if len(raw) > MAX_FIXTURE_BYTES:
        return False
    if hashlib.sha256(real_bytes).hexdigest() != REAL_COURSE_SHA256:
        return False
    text = real_bytes.decode("utf-8", "replace")
    if FIXTURE_MODULE in text or FIXTURE_ID in text:
        return False
    module_blob = json.dumps(modules[0], sort_keys=True)
    if module_blob in text:
        return False
    return True
