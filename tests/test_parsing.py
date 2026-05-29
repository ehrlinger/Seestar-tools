"""
Characterization tests for the pure parsing/grouping functions.
Run with:  python3 -m unittest discover -s tests -v
"""
import sys
import tempfile
import unittest
from collections import defaultdict
from pathlib import Path

# Make the repo root importable so we can `import cleanup_seestar` etc.
REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

import batch_stack
import cleanup_seestar
import count_subs
import rename_seestar_folders


class CanonicalTargetNameTests(unittest.TestCase):
    """cleanup_seestar.canonical_target_name — strips _sub/_subs + session suffix."""

    def test_plain_sub_suffix(self):
        self.assertEqual(cleanup_seestar.canonical_target_name("M 51_sub"), "M 51")

    def test_plain_subs_suffix(self):
        self.assertEqual(cleanup_seestar.canonical_target_name("NGC 6946_subs"), "NGC 6946")

    def test_iso_date_session(self):
        self.assertEqual(cleanup_seestar.canonical_target_name("M 51 2026-05-22_sub"), "M 51")

    def test_compact_date_session(self):
        self.assertEqual(cleanup_seestar.canonical_target_name("M 51_20260522_sub"), "M 51")

    def test_bare_index_session(self):
        # "_sub_2" middle suffix — distinct from a bare trailing digit on the target
        self.assertEqual(cleanup_seestar.canonical_target_name("M 51_sub_2"), "M 51")

    def test_subs_index_session(self):
        self.assertEqual(cleanup_seestar.canonical_target_name("M 51_subs_3"), "M 51")

    def test_parenthesised_index_session(self):
        self.assertEqual(cleanup_seestar.canonical_target_name("M 51 (2)_sub"), "M 51")

    def test_no_suffix_returned_unchanged(self):
        self.assertEqual(cleanup_seestar.canonical_target_name("M 51"), "M 51")

    def test_target_without_digits_unchanged(self):
        self.assertEqual(cleanup_seestar.canonical_target_name("Veil_sub"), "Veil")

    def test_target_without_digits_with_date(self):
        self.assertEqual(cleanup_seestar.canonical_target_name("Veil 2026-05-22_sub"), "Veil")

    def test_does_not_strip_target_number_from_bare_name(self):
        # Regression: the old bare-\d+ branch would turn "NGC 6946" into "NGC"
        self.assertEqual(cleanup_seestar.canonical_target_name("NGC 6946"), "NGC 6946")


class IsProcessedTests(unittest.TestCase):
    """cleanup_seestar.is_processed — recognises stacked/processed outputs."""

    def test_raw_seestar_light_is_not_processed(self):
        self.assertFalse(cleanup_seestar.is_processed("Light_M_51_10.0s_IRCUT_20260509-013344.fit"))

    def test_starless_prefix(self):
        self.assertTrue(cleanup_seestar.is_processed("starless_M51.fit"))

    def test_starmask_prefix(self):
        self.assertTrue(cleanup_seestar.is_processed("starmask_M51.fit"))

    def test_stack_prefix(self):
        self.assertTrue(cleanup_seestar.is_processed("stack_M51.fit"))

    def test_r_pp_prefix(self):
        self.assertTrue(cleanup_seestar.is_processed("r_pp_Light_001.fit"))

    def test_graxpert_marker(self):
        self.assertTrue(cleanup_seestar.is_processed("M51_GraXpert.fits"))

    def test_pp_marker(self):
        self.assertTrue(cleanup_seestar.is_processed("M51_pp.fit"))

    def test_processed_marker(self):
        self.assertTrue(cleanup_seestar.is_processed("M51_processed.fit"))

    def test_stacked_filename_pattern(self):
        self.assertTrue(cleanup_seestar.is_processed("M_51_1175x20sec_T25degC_2026-05-15.fit"))


class SafeDestTests(unittest.TestCase):
    """cleanup_seestar.safe_dest — appends _dupN when destination exists."""

    def test_returns_dest_unchanged_when_free(self):
        with tempfile.TemporaryDirectory() as td:
            dest = Path(td) / "foo.fit"
            self.assertEqual(cleanup_seestar.safe_dest(dest), dest)

    def test_appends_dup1_when_dest_exists(self):
        with tempfile.TemporaryDirectory() as td:
            dest = Path(td) / "foo.fit"
            dest.touch()
            result = cleanup_seestar.safe_dest(dest)
            self.assertEqual(result.name, "foo_dup1.fit")

    def test_increments_dup_counter_until_free(self):
        with tempfile.TemporaryDirectory() as td:
            dest = Path(td) / "foo.fit"
            dest.touch()
            (Path(td) / "foo_dup1.fit").touch()
            (Path(td) / "foo_dup2.fit").touch()
            result = cleanup_seestar.safe_dest(dest)
            self.assertEqual(result.name, "foo_dup3.fit")


class GroupByTargetTests(unittest.TestCase):
    """cleanup_seestar.group_by_target — groups folder paths by canonical name."""

    def test_groups_by_canonical_name(self):
        # Use date-suffixed names so the canonical_target_name bug doesn't trip us
        folders = [
            Path("/x/M 51 2026-05-22_sub"),
            Path("/x/M 51 2026-05-23_sub"),
            Path("/x/Veil_subs"),
        ]
        groups = cleanup_seestar.group_by_target(folders)
        self.assertEqual(set(groups.keys()), {"M 51", "Veil"})
        self.assertEqual(len(groups["M 51"]), 2)
        self.assertEqual(len(groups["Veil"]), 1)

    def test_primary_is_alphabetically_first(self):
        folders = [
            Path("/x/M 51 2026-05-23_sub"),
            Path("/x/M 51 2026-05-22_sub"),
        ]
        groups = cleanup_seestar.group_by_target(folders)
        primary = groups["M 51"][0]
        self.assertEqual(primary.name, "M 51 2026-05-22_sub")  # earlier date sorts first


class ExptimeRegexTests(unittest.TestCase):
    """count_subs._EXPTIME_RE — extracts exposure seconds from Seestar filenames."""

    def test_integer_seconds(self):
        m = count_subs._EXPTIME_RE.search("Light_M_63_10s_IRCUT_20260509-013344.fit")
        self.assertIsNotNone(m)
        self.assertEqual(float(m.group(1)), 10.0)

    def test_decimal_seconds(self):
        m = count_subs._EXPTIME_RE.search("Light_M_63_10.0s_IRCUT_20260509-013344.fit")
        self.assertIsNotNone(m)
        self.assertEqual(float(m.group(1)), 10.0)

    def test_twenty_second_sub(self):
        m = count_subs._EXPTIME_RE.search("Light_NGC_6946_20.0s_IRCUT_20260601-220000.fit")
        self.assertIsNotNone(m)
        self.assertEqual(float(m.group(1)), 20.0)

    def test_no_match_returns_none(self):
        self.assertIsNone(count_subs._EXPTIME_RE.search("random_name.fit"))


class FormatDurationTests(unittest.TestCase):
    """count_subs.format_duration — humanises seconds."""

    def test_seconds_only(self):
        self.assertEqual(count_subs.format_duration(42), "42s")

    def test_minutes_and_seconds(self):
        self.assertEqual(count_subs.format_duration(125), "2m 5s")

    def test_hours_minutes_seconds(self):
        self.assertEqual(count_subs.format_duration(3725), "1h 2m 5s")

    def test_zero(self):
        self.assertEqual(count_subs.format_duration(0), "0s")


class NormalizeTests(unittest.TestCase):
    """count_subs.normalize — lowercase, strip whitespace/underscores."""

    def test_spaces_removed(self):
        self.assertEqual(count_subs.normalize("M 51"), "m51")

    def test_underscores_removed(self):
        self.assertEqual(count_subs.normalize("NGC_6946"), "ngc6946")

    def test_mixed_case_and_separators(self):
        self.assertEqual(count_subs.normalize("IC 434 Mosaic"), "ic434mosaic")


class CanonicalFolderNameTests(unittest.TestCase):
    """count_subs.canonical_folder_name — strip sub/subs suffix only."""

    def test_subs_stripped(self):
        self.assertEqual(count_subs.canonical_folder_name("NGC6946_subs"), "NGC6946")

    def test_sub_stripped(self):
        self.assertEqual(count_subs.canonical_folder_name("M51_sub"), "M51")

    def test_no_suffix_unchanged(self):
        self.assertEqual(count_subs.canonical_folder_name("M51"), "M51")


class DeduplicateTests(unittest.TestCase):
    """count_subs.deduplicate — merge result dicts with same canonical name."""

    def _make(self, name, total, by_exp, unknown=0, total_sec=None):
        if total_sec is None:
            total_sec = sum(exp * cnt for exp, cnt in by_exp.items())
        return {
            "name": name,
            "total_files": total,
            "by_exptime": dict(by_exp),
            "unknown_exptime": unknown,
            "total_integration_sec": total_sec,
        }

    def test_single_result_unchanged(self):
        results = [self._make("M51_sub", 10, {10.0: 10})]
        out = count_subs.deduplicate(results)
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["total_files"], 10)

    def test_merges_same_canonical_name(self):
        results = [
            self._make("M51_sub", 10, {10.0: 10}),
            self._make("M 51_subs", 5, {20.0: 5}),
        ]
        out = count_subs.deduplicate(results)
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["total_files"], 15)
        self.assertEqual(out[0]["by_exptime"], {10.0: 10, 20.0: 5})

    def test_display_name_from_largest(self):
        results = [
            self._make("M51_sub", 3, {10.0: 3}),
            self._make("M 51_subs", 50, {10.0: 50}),
        ]
        out = count_subs.deduplicate(results)
        self.assertEqual(out[0]["name"], "M 51_subs")


class NewNameTests(unittest.TestCase):
    """rename_seestar_folders.new_name — spaces → underscores."""

    def test_single_space(self):
        self.assertEqual(rename_seestar_folders.new_name("M 51_sub"), "M_51_sub")

    def test_multiple_interior_spaces(self):
        self.assertEqual(rename_seestar_folders.new_name("M 81 mosaic_sub"), "M_81_mosaic_sub")

    def test_no_spaces_unchanged(self):
        self.assertEqual(rename_seestar_folders.new_name("M51_sub"), "M51_sub")

    def test_leading_trailing_spaces_stripped(self):
        self.assertEqual(rename_seestar_folders.new_name("  M 51_sub  "), "M_51_sub")


class StackedRegexTests(unittest.TestCase):
    """batch_stack.STACKED_RE — recognises Siril stacked-output filenames."""

    def test_matches_stacked_output(self):
        self.assertIsNotNone(
            batch_stack.STACKED_RE.search("M_51_1175x20sec_T25degC_2026-05-15.fit")
        )

    def test_matches_short_stack(self):
        self.assertIsNotNone(batch_stack.STACKED_RE.search("M51_50x10sec.fit"))

    def test_does_not_match_raw_sub(self):
        self.assertIsNone(
            batch_stack.STACKED_RE.search("Light_M_51_10.0s_IRCUT_20260509-013344.fit")
        )


if __name__ == "__main__":
    unittest.main()
