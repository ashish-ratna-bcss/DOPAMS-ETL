import tempfile
import unittest
from pathlib import Path

from reconcile_v1_media import file_is_present, rewrite_local_path, shared_path


class ReconcileV1MediaTests(unittest.TestCase):
    def test_rewrite_old_prefix(self):
        self.assertEqual(
            rewrite_local_path("/home/tganb/dopams/media_cctnsv1/FIR/a.pdf"),
            "/data-drive/etl-files/FIR/a.pdf",
        )

    def test_rewrite_leaves_shared_path(self):
        self.assertEqual(
            rewrite_local_path("/data-drive/etl-files/FIR/a.pdf"),
            "/data-drive/etl-files/FIR/a.pdf",
        )

    def test_shared_path_and_presence(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            relative = "FIR/2011/doc.pdf"
            missing = shared_path(root, relative)
            self.assertIsNotNone(missing)
            self.assertFalse(file_is_present(missing))
            missing.parent.mkdir(parents=True)
            missing.write_bytes(b"pdf")
            self.assertTrue(file_is_present(missing))


if __name__ == "__main__":
    unittest.main()
