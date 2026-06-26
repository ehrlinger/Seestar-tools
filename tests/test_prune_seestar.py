"""
Unit tests for prune_seestar — deletes EMMC subs already archived on the NAS.

Kept in its own module (auto-discovered by `unittest discover`) so the prune
tool's tests stay independent of test_parsing.py.
Run with:  python3 -m unittest discover -s tests -v
"""
import contextlib
import io
import sys
import tempfile
import unittest
from pathlib import Path

# Make the repo root importable so we can `import prune_seestar` etc.
REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

import prune_seestar
import rename_seestar_folders


class PruneImportTests(unittest.TestCase):
    """prune_seestar exposes the expected constants and reuses shared helpers."""

    def test_fits_suffixes(self):
        self.assertEqual(prune_seestar.FITS_SUFFIXES, {".fit", ".fits"})

    def test_jpg_suffixes(self):
        self.assertEqual(prune_seestar.JPG_SUFFIXES, {".jpg", ".jpeg"})

    def test_reuses_new_name(self):
        # forward EMMC->NAS folder transform is the rename script's new_name
        self.assertIs(prune_seestar.new_name, rename_seestar_folders.new_name)


if __name__ == "__main__":
    unittest.main()
