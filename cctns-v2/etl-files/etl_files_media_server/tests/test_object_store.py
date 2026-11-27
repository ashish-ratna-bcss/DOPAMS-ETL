"""Unit tests for V2's object-store key guard (assert_v2_key / valid_key),
in particular the corrected V1-reserved-prefix list.

Run: python3 tests/test_object_store.py
"""
from __future__ import annotations

import os
import sys
import unittest

from pathlib import Path as _Path

_HERE = _Path(__file__).resolve()
PACKAGE_DIR = str(_HERE.parents[1])  # .../etl_files_media_server/ (outer)
REPO_ROOT = str(_HERE.parents[3])  # .../cctns-v2/ -- where env_utils.py lives
for _p in (REPO_ROOT, PACKAGE_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from etl_files_media_server.object_store import assert_v2_key, valid_key  # noqa: E402


class AssertV2KeyTests(unittest.TestCase):
    def test_accepts_crimes_prefix(self):
        assert_v2_key("crimes/some-file-id.pdf")  # must not raise

    def test_accepts_person_prefix(self):
        assert_v2_key("person/identitydetails/some-id.pdf")  # must not raise

    def test_rejects_fir_prefix(self):
        with self.assertRaises(ValueError):
            assert_v2_key("FIR/2020/2020020/x.pdf")

    def test_rejects_investigation_prefix(self):
        # The actual bug fixed on 2026-11-27: real V1 COURT-sourced rows use
        # "Investigation/", not "COURT/" -- confirmed against the live V1 DB
        # (cctns.cctns_court.attach_path, 3908/3908 rows). Before the fix,
        # this prefix was NOT guarded at all, so V2 could have silently
        # written into roughly half of V1's key space.
        with self.assertRaises(ValueError):
            assert_v2_key("Investigation/CS/ScannedCopy/x.pdf")

    def test_old_wrong_court_prefix_is_not_guarded(self):
        # Pins the (corrected) current behavior: a literal "COURT/" key was
        # never real V1 data, so it is intentionally NOT in the reserved
        # list -- V1 never produces such a key, so guarding it would have
        # been a no-op guard giving false confidence, not real protection.
        assert_v2_key("COURT/not-a-real-v1-prefix.pdf")  # must not raise


class ValidKeyTests(unittest.TestCase):
    def test_valid_key_ok(self):
        self.assertTrue(valid_key("crimes/some-id.pdf"))

    def test_valid_key_rejects_leading_slash(self):
        self.assertFalse(valid_key("/crimes/some-id.pdf"))

    def test_valid_key_rejects_traversal(self):
        self.assertFalse(valid_key("crimes/../../etc/passwd"))

    def test_valid_key_rejects_empty(self):
        self.assertFalse(valid_key(""))


if __name__ == "__main__":
    unittest.main()
