import tempfile
import unittest
from pathlib import Path

from share_v1_media import migrate


class ShareV1MediaTests(unittest.TestCase):
    def test_migration_is_dry_run_by_default(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "source"
            destination = Path(tmp) / "destination"
            source.mkdir()
            destination.mkdir()
            (source / "FIR").mkdir()
            (source / "FIR" / "document.pdf").write_bytes(b"pdf")

            stats = migrate(source, destination, apply=False, verify_hash=False)

            self.assertEqual(stats.discovered_files, 1)
            self.assertEqual(stats.copied_files, 0)
            self.assertFalse((destination / "FIR" / "document.pdf").exists())

    def test_migration_copies_and_resumes(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "source"
            destination = Path(tmp) / "destination"
            source.mkdir()
            destination.mkdir()
            (source / "Investigation").mkdir()
            source_file = source / "Investigation" / "court.pdf"
            source_file.write_bytes(b"court-document")

            first = migrate(source, destination, apply=True, verify_hash=True)
            second = migrate(source, destination, apply=True, verify_hash=True)

            self.assertEqual(first.copied_files, 1)
            self.assertEqual(first.failures, 0)
            self.assertEqual(second.copied_files, 0)
            self.assertEqual(second.existing_files, 1)
            self.assertEqual((destination / "Investigation" / "court.pdf").read_bytes(), b"court-document")

    def test_migration_does_not_overwrite_conflict(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "source"
            destination = Path(tmp) / "destination"
            source.mkdir()
            destination.mkdir()
            (source / "same.pdf").write_bytes(b"source")
            (destination / "same.pdf").write_bytes(b"different")

            stats = migrate(source, destination, apply=True, verify_hash=False)

            self.assertEqual(stats.conflicts, 1)
            self.assertEqual((destination / "same.pdf").read_bytes(), b"different")


if __name__ == "__main__":
    unittest.main()
