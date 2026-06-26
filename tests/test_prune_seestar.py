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


class PruneIndexTests(unittest.TestCase):
    """is_fits + index_nas_target build the name->sizes match index."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _touch(self, rel, size=10):
        p = self.tmp / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"x" * size)
        return p

    def test_is_fits_true_for_fit_and_fits(self):
        self.assertTrue(prune_seestar.is_fits(Path("Light_M51.fit")))
        self.assertTrue(prune_seestar.is_fits(Path("Light_M51.FITS")))

    def test_is_fits_false_for_jpg_and_noise(self):
        self.assertFalse(prune_seestar.is_fits(Path("Light_M51.jpg")))
        self.assertFalse(prune_seestar.is_fits(Path("._Light_M51.fit")))

    def test_index_collapses_reorg_subtree(self):
        self._touch("M_51_sub/20s/lights/Light_a.fit", size=100)
        self._touch("M_51_sub/lights/Light_b.fit", size=200)
        self._touch("M_51_sub/Stacked_master.jpg", size=999)  # not FITS
        idx = prune_seestar.index_nas_target(self.tmp / "M_51_sub")
        self.assertEqual(idx, {"Light_a.fit": {100}, "Light_b.fit": {200}})

    def test_index_records_multiple_sizes_for_same_name(self):
        self._touch("T_sub/lights/Light_a.fit", size=100)
        self._touch("T_sub/20s/lights/Light_a.fit", size=101)
        idx = prune_seestar.index_nas_target(self.tmp / "T_sub")
        self.assertEqual(idx, {"Light_a.fit": {100, 101}})


class PruneFindTargetsTests(unittest.TestCase):
    """find_emmc_targets returns top-level _sub/_subs dirs, minus excluded."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _mkdir(self, rel):
        d = self.tmp / rel
        d.mkdir(parents=True, exist_ok=True)
        return d

    def test_returns_sub_and_subs_dirs(self):
        self._mkdir("M 51_sub")
        self._mkdir("NGC 7000_subs")
        names = sorted(p.name for p in prune_seestar.find_emmc_targets(self.tmp))
        self.assertEqual(names, ["M 51_sub", "NGC 7000_subs"])

    def test_ignores_non_sub_dirs_and_files(self):
        self._mkdir("random_folder")
        (self.tmp / "loose.fit").write_bytes(b"x")
        self.assertEqual(prune_seestar.find_emmc_targets(self.tmp), [])

    def test_excludes_trash_and_scripts(self):
        self._mkdir("_trash/M 51_sub")
        self._mkdir("scripts/Foo_sub")
        self.assertEqual(prune_seestar.find_emmc_targets(self.tmp), [])


class PruneEligibleSubsTests(unittest.TestCase):
    """eligible_subs splits root subs into (to_delete, kept_unmatched)."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.target = self.tmp / "M 51_sub"
        self.target.mkdir(parents=True)

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _sub(self, name, size=10):
        p = self.target / name
        p.write_bytes(b"x" * size)
        return p

    def test_name_and_size_match_is_deletable(self):
        self._sub("Light_a.fit", size=100)
        index = {"Light_a.fit": {100}}
        to_delete, kept = prune_seestar.eligible_subs(self.target, index)
        self.assertEqual([p.name for p in to_delete], ["Light_a.fit"])
        self.assertEqual(kept, [])

    def test_size_mismatch_is_kept(self):
        self._sub("Light_a.fit", size=100)
        index = {"Light_a.fit": {999}}  # different size
        to_delete, kept = prune_seestar.eligible_subs(self.target, index)
        self.assertEqual(to_delete, [])
        self.assertEqual([p.name for p in kept], ["Light_a.fit"])

    def test_name_absent_is_kept(self):
        self._sub("Light_b.fit", size=100)
        to_delete, kept = prune_seestar.eligible_subs(self.target, {})
        self.assertEqual(to_delete, [])
        self.assertEqual([p.name for p in kept], ["Light_b.fit"])

    def test_only_root_fits_considered_not_jpgs_or_noise(self):
        self._sub("Light_a.fit", size=100)
        self._sub("Light_a.jpg", size=100)        # preview, not a candidate
        self._sub("._Light_a.fit", size=100)      # macOS noise
        index = {"Light_a.fit": {100}, "Light_a.jpg": {100}}
        to_delete, kept = prune_seestar.eligible_subs(self.target, index)
        self.assertEqual([p.name for p in to_delete], ["Light_a.fit"])
        self.assertEqual(kept, [])


class PruneSiblingJpgsTests(unittest.TestCase):
    """sibling_jpgs finds the preview + thumbnail for a given sub."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _touch(self, name):
        p = self.tmp / name
        p.write_bytes(b"x")
        return p

    def test_finds_preview_and_thumbnail(self):
        sub = self._touch("Light_a.fit")
        self._touch("Light_a.jpg")
        self._touch("Light_a_thn.jpg")
        got = sorted(p.name for p in prune_seestar.sibling_jpgs(sub))
        self.assertEqual(got, ["Light_a.jpg", "Light_a_thn.jpg"])

    def test_case_insensitive_suffix(self):
        sub = self._touch("Light_b.fit")
        self._touch("Light_b.JPG")
        got = [p.name for p in prune_seestar.sibling_jpgs(sub)]
        self.assertEqual(got, ["Light_b.JPG"])

    def test_no_jpgs_returns_empty(self):
        sub = self._touch("Light_c.fit")
        self.assertEqual(prune_seestar.sibling_jpgs(sub), [])

    def test_does_not_match_other_subs_jpg(self):
        sub = self._touch("Light_a.fit")
        self._touch("Light_aa.jpg")   # different stem
        self.assertEqual(prune_seestar.sibling_jpgs(sub), [])


if __name__ == "__main__":
    unittest.main()
