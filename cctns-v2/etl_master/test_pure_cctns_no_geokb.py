#!/usr/bin/env python3
"""Pure-CCTNS config must not include external GeoKB/etl-address enrichment."""
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
        self.assertNotIn("etl-address", names)
        self.assertIn("persons", names)
        self.assertIn("arrests", names)
        self.assertIn("fsl_case_property", names)
        # Sequential orders 1..N with no gaps
        orders = [int(p["order"]) for p in procs]
        self.assertEqual(orders, list(range(1, len(orders) + 1)))

    def test_non_pure_input_still_has_etl_address(self):
        """Enrichment path preserved outside pure-cctns."""
        procs = parse_input_file(str(ROOT / "etl_master" / "input.txt"))
        names = [(p.get("name") or "").strip().lower() for p in procs]
        self.assertIn("etl-address", names)

    def test_pure_config_file_has_no_geokb_markers(self):
        text = (ROOT / "etl_master" / "input.cctns-pure.txt").read_text()
        self.assertNotIn("etl_address.py", text)
        self.assertNotIn("\netl-address\n", text)
        self.assertIn("GeoKB", text)  # architecture comment explaining exclusion


if __name__ == "__main__":
    unittest.main()
