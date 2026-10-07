#!/usr/bin/env python3
"""CCTNS configs load the API only. Derived jobs live in ETL-3."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path("/home/eagle/DOPAMS-ETL")
sys.path.insert(0, str(ROOT / "etl_master"))
sys.path.insert(0, str(ROOT))

from preflight_check import parse_input_file  # noqa: E402


class TestPureCctnsNoGeoKb(unittest.TestCase):
    def test_pure_config_excludes_etl_address(self):
        procs = parse_input_file(str(ROOT / "etl_master" / "input.cctns-pure.txt"))
        names = [(p.get("name") or "").strip().lower() for p in procs]
        for removed in (
            "etl-address",
            "brief_facts_ai",
            "class_classification",
            "case_status",
            "domicile classification",
            "fix_person_names",
            "full_name_fix",
            "name_fix",
            "surname_fix",
        ):
            self.assertNotIn(removed, names)
        self.assertIn("persons", names)
        self.assertIn("crimes", names)
        self.assertIn("arrests", names)
        self.assertIn("fsl_case_property", names)
        # Sequential orders 1..N with no gaps
        orders = [int(p["order"]) for p in procs]
        self.assertEqual(orders, list(range(1, len(orders) + 1)))

    def test_non_pure_input_excludes_removed_enrichment(self):
        """Derived jobs were removed. ETL-3 owns that work."""
        procs = parse_input_file(str(ROOT / "etl_master" / "input.txt"))
        names = [(p.get("name") or "").strip().lower() for p in procs]
        for removed in (
            "etl-address",
            "brief_facts_ai",
            "class_classification",
            "case_status",
            "domicile classification",
            "fix_person_names",
        ):
            self.assertNotIn(removed, names)
        self.assertIn("persons", names)
        self.assertIn("crimes", names)

    def test_pure_config_file_has_no_geokb_markers(self):
        text = (ROOT / "etl_master" / "input.cctns-pure.txt").read_text()
        for marker in (
            "etl_address.py",
            "\netl-address\n",
            "process_sections.py",
            "domicile_classifier.py",
            "fix_person_names.py",
            "fix_all_fullnames.py",
        ):
            self.assertNotIn(marker, text)
        self.assertNotIn("update_crimes.py", text)


if __name__ == "__main__":
    unittest.main()
