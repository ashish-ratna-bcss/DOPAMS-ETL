"""Unit tests for V1's object-store key guard (assert_v1_key / valid_key).

Run: python3 tests/test_object_store_v1.py
"""
from __future__ import annotations

import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from storage.object_store import assert_v1_key, valid_key  # noqa: E402


class AssertV1KeyTests(unittest.TestCase):
    def test_accepts_fir_prefix(self):
        assert_v1_key("FIR/2020/2020020/x.pdf")  # must not raise

    def test_accepts_investigation_prefix(self):
        # Real V1 COURT-sourced attach_path rows start with "Investigation/",
        # not "COURT/" -- entity_type='COURT' is a bookkeeping label, never
        # the path prefix (verified against the live V1 DB 2026-11-27).
        assert_v1_key("Investigation/CS/ScannedCopy/x.pdf")  # must not raise

    def test_rejects_v2_crimes_prefix(self):
        with self.assertRaises(ValueError):
            assert_v1_key("crimes/some-file-id.pdf")

    def test_rejects_v2_person_prefix(self):
        with self.assertRaises(ValueError):
            assert_v1_key("person/identitydetails/some-id.pdf")

    def test_rejects_v2_mo_seizures_prefix(self):
        with self.assertRaises(ValueError):
            assert_v1_key("mo_seizures/some-id.pdf")

    def test_old_wrong_court_prefix_is_not_the_real_guard(self):
        # Documents the fixed bug: a literal "COURT/" key was never real V1
        # data, so a key starting with "COURT/" is NOT one of V1's actual
        # prefixes and is correctly left unguarded by assert_v1_key (V1 never
        # produces such a key in practice; this just pins current behavior).
        assert_v1_key("COURT/not-a-real-v1-prefix.pdf")  # must not raise


class ValidKeyTests(unittest.TestCase):
    def test_valid_key_ok(self):
        self.assertTrue(valid_key("FIR/2020/x.pdf"))

    def test_valid_key_rejects_leading_slash(self):
        self.assertFalse(valid_key("/FIR/2020/x.pdf"))

    def test_valid_key_rejects_traversal(self):
        self.assertFalse(valid_key("FIR/../../etc/passwd"))

    def test_valid_key_rejects_empty(self):
        self.assertFalse(valid_key(""))


if __name__ == "__main__":
    unittest.main()
