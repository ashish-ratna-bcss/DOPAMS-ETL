"""Unit tests for CCTNS V1 entity run locks."""
import os
import tempfile
import unittest
from unittest import mock

from db.run_lock import EntityRunLock, entity_lock_path


class EntityRunLockTests(unittest.TestCase):
    def test_second_lock_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.dict(os.environ, {"CCTNS_V1_ETL_LOCK_DIR": tmp}):
                path = entity_lock_path("accused")
                self.assertTrue(path.startswith(tmp))
                with EntityRunLock("accused"):
                    with self.assertRaises(RuntimeError) as ctx:
                        with EntityRunLock("accused"):
                            pass
                    self.assertIn("already running", str(ctx.exception))

    def test_different_entities_do_not_block(self):
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.dict(os.environ, {"CCTNS_V1_ETL_LOCK_DIR": tmp}):
                with EntityRunLock("fir"):
                    with EntityRunLock("accused"):
                        pass


if __name__ == "__main__":
    unittest.main()
