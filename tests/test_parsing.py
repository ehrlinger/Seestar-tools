"""
Characterization tests for the pure parsing/grouping functions.
Run with:  python3 -m unittest discover -s tests -v
"""
import contextlib
import io
import os
import sys
import tempfile
import unittest
from collections import defaultdict
from pathlib import Path

# Make the repo root importable so we can `import organize_subs` etc.
REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

import batch_stack
import organize_subs
import count_subs
import purge_siril_cruft
import rename_seestar_folders
import seestar_common
import sort_by_exptime


class CanonicalTargetNameTests(unittest.TestCase):
    """organize_subs.canonical_target_name — strips _sub/_subs + session suffix."""

    def test_plain_sub_suffix(self):
        self.assertEqual(organize_subs.canonical_target_name("M 51_sub"), "M 51")

    def test_plain_subs_suffix(self):
        self.assertEqual(organize_subs.canonical_target_name("NGC 6946_subs"), "NGC 6946")

    def test_iso_date_session(self):
        self.assertEqual(organize_subs.canonical_target_name("M 51 2026-05-22_sub"), "M 51")

    def test_compact_date_session(self):
        self.assertEqual(organize_subs.canonical_target_name("M 51_20260522_sub"), "M 51")

    def test_bare_index_session(self):
        # "_sub_2" middle suffix — distinct from a bare trailing digit on the target
        self.assertEqual(organize_subs.canonical_target_name("M 51_sub_2"), "M 51")

    def test_subs_index_session(self):
        self.assertEqual(organize_subs.canonical_target_name("M 51_subs_3"), "M 51")

    def test_parenthesised_index_session(self):
        self.assertEqual(organize_subs.canonical_target_name("M 51 (2)_sub"), "M 51")

    def test_no_suffix_returned_unchanged(self):
        self.assertEqual(organize_subs.canonical_target_name("M 51"), "M 51")

    def test_target_without_digits_unchanged(self):
        self.assertEqual(organize_subs.canonical_target_name("Veil_sub"), "Veil")

    def test_target_without_digits_with_date(self):
        self.assertEqual(organize_subs.canonical_target_name("Veil 2026-05-22_sub"), "Veil")

    def test_does_not_strip_target_number_from_bare_name(self):
        # Regression: the old bare-\d+ branch would turn "NGC 6946" into "NGC"
        self.assertEqual(organize_subs.canonical_target_name("NGC 6946"), "NGC 6946")


class IsProcessedTests(unittest.TestCase):
    """organize_subs.is_processed — recognises stacked/processed outputs."""

    def test_raw_seestar_light_is_not_processed(self):
        self.assertFalse(organize_subs.is_processed("Light_M_51_10.0s_IRCUT_20260509-013344.fit"))

    def test_starless_prefix(self):
        self.assertTrue(organize_subs.is_processed("starless_M51.fit"))

    def test_starmask_prefix(self):
        self.assertTrue(organize_subs.is_processed("starmask_M51.fit"))

    def test_stack_prefix(self):
        self.assertTrue(organize_subs.is_processed("stack_M51.fit"))

    def test_r_pp_prefix(self):
        self.assertTrue(organize_subs.is_processed("r_pp_Light_001.fit"))

    def test_graxpert_marker(self):
        self.assertTrue(organize_subs.is_processed("M51_GraXpert.fits"))

    def test_pp_marker(self):
        self.assertTrue(organize_subs.is_processed("M51_pp.fit"))

    def test_processed_marker(self):
        self.assertTrue(organize_subs.is_processed("M51_processed.fit"))

    def test_stacked_filename_pattern(self):
        self.assertTrue(organize_subs.is_processed("M_51_1175x20sec_T25degC_2026-05-15.fit"))


class CleanSubFolderScaffoldTests(unittest.TestCase):
    """organize_subs.clean_sub_folder — must not leave a misleading empty lights/
    scaffold over an already-sorted target (the thing that traps a hand-run Siril
    `cd lights`). lights/ is created lazily, only when a raw FITS needs it."""

    def _run(self, sub_dir: Path):
        with contextlib.redirect_stdout(io.StringIO()):
            organize_subs.clean_sub_folder(sub_dir, dry_run=False)

    def test_no_empty_lights_created_when_already_sorted(self):
        # Mixed target already nested: 10s/lights + 20s/lights, no root FITS.
        with tempfile.TemporaryDirectory() as td:
            sub = Path(td) / "M_51_sub"
            for exp in ("10s", "20s"):
                (sub / exp / "lights").mkdir(parents=True)
                (sub / exp / "lights" / "Light.fit").write_bytes(b"fits")
            self._run(sub)
            self.assertFalse(
                (sub / "lights").exists(),
                "clean_sub_folder created an empty top-level lights/ scaffold",
            )

    def test_lights_created_when_root_fits_present(self):
        # New raw subs at the root still get filed into a freshly-made lights/.
        with tempfile.TemporaryDirectory() as td:
            sub = Path(td) / "M_99_sub"
            sub.mkdir()
            (sub / "Light_M_99_20.0s_x.fit").write_bytes(b"fits")
            self._run(sub)
            self.assertTrue((sub / "lights" / "Light_M_99_20.0s_x.fit").exists())


class SafeDestTests(unittest.TestCase):
    """organize_subs.safe_dest — appends _dupN when destination exists."""

    def test_returns_dest_unchanged_when_free(self):
        with tempfile.TemporaryDirectory() as td:
            dest = Path(td) / "foo.fit"
            self.assertEqual(organize_subs.safe_dest(dest), dest)

    def test_appends_dup1_when_dest_exists(self):
        with tempfile.TemporaryDirectory() as td:
            dest = Path(td) / "foo.fit"
            dest.touch()
            result = organize_subs.safe_dest(dest)
            self.assertEqual(result.name, "foo_dup1.fit")

    def test_increments_dup_counter_until_free(self):
        with tempfile.TemporaryDirectory() as td:
            dest = Path(td) / "foo.fit"
            dest.touch()
            (Path(td) / "foo_dup1.fit").touch()
            (Path(td) / "foo_dup2.fit").touch()
            result = organize_subs.safe_dest(dest)
            self.assertEqual(result.name, "foo_dup3.fit")


class GroupByTargetTests(unittest.TestCase):
    """organize_subs.group_by_target — groups folder paths by canonical name."""

    def test_groups_by_canonical_name(self):
        # Use date-suffixed names so the canonical_target_name bug doesn't trip us
        folders = [
            Path("/x/M 51 2026-05-22_sub"),
            Path("/x/M 51 2026-05-23_sub"),
            Path("/x/Veil_subs"),
        ]
        groups = organize_subs.group_by_target(folders)
        self.assertEqual(set(groups.keys()), {"M 51", "Veil"})
        self.assertEqual(len(groups["M 51"]), 2)
        self.assertEqual(len(groups["Veil"]), 1)

    def test_primary_is_alphabetically_first(self):
        folders = [
            Path("/x/M 51 2026-05-23_sub"),
            Path("/x/M 51 2026-05-22_sub"),
        ]
        groups = organize_subs.group_by_target(folders)
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


class ExptimeLabelTests(unittest.TestCase):
    """sort_by_exptime.exptime_label — float exposure → clean folder name."""

    def test_integer_exposure(self):
        self.assertEqual(sort_by_exptime.exptime_label(10.0), "10s")

    def test_twenty(self):
        self.assertEqual(sort_by_exptime.exptime_label(20.0), "20s")

    def test_fractional_exposure(self):
        self.assertEqual(sort_by_exptime.exptime_label(20.5), "20.5s")


class ExptimeDirRegexTests(unittest.TestCase):
    """sort_by_exptime.EXPTIME_DIR_RE — recognises already-sorted exposure dirs."""

    def test_matches_integer_label(self):
        self.assertIsNotNone(sort_by_exptime.EXPTIME_DIR_RE.match("10s"))

    def test_matches_decimal_label(self):
        self.assertIsNotNone(sort_by_exptime.EXPTIME_DIR_RE.match("20.5s"))

    def test_does_not_match_lights(self):
        self.assertIsNone(sort_by_exptime.EXPTIME_DIR_RE.match("lights"))

    def test_does_not_match_target(self):
        self.assertIsNone(sort_by_exptime.EXPTIME_DIR_RE.match("M_51_sub"))


class IsAlreadySortedTests(unittest.TestCase):
    """sort_by_exptime.is_already_sorted — pure path predicate, no filesystem."""

    def setUp(self):
        self.root = Path("/nas/Seestar")

    def test_canonical_exptime_lights_is_sorted(self):
        self.assertTrue(
            sort_by_exptime.is_already_sorted(self.root / "M_51_sub/10s/lights", self.root)
        )

    def test_exptime_dir_itself_is_sorted(self):
        self.assertTrue(
            sort_by_exptime.is_already_sorted(self.root / "M_51_sub/20s", self.root)
        )

    def test_flat_lights_is_not_sorted(self):
        self.assertFalse(
            sort_by_exptime.is_already_sorted(self.root / "M_51_sub/lights", self.root)
        )

    def test_target_root_is_not_sorted(self):
        self.assertFalse(
            sort_by_exptime.is_already_sorted(self.root / "M_51_sub", self.root)
        )

    def test_outside_root_returns_false(self):
        self.assertFalse(
            sort_by_exptime.is_already_sorted(Path("/other/10s/lights"), self.root)
        )


class MountModeTests(unittest.TestCase):
    """sort_by_exptime.mount_mode — exposure → mount mode. Seestar shoots ~10 s
    in alt-az and 20 s+ in EQ, so short subs are alt-az and long subs are EQ."""

    def test_ten_second_is_altaz(self):
        self.assertEqual(sort_by_exptime.mount_mode(10.0), "altaz")

    def test_twenty_and_thirty_second_are_eq(self):
        self.assertEqual(sort_by_exptime.mount_mode(20.0), "eq")
        self.assertEqual(sort_by_exptime.mount_mode(30.0), "eq")


class PlanLayoutTests(unittest.TestCase):
    """sort_by_exptime.plan_layout — pure planner keyed on MOUNT MODE, not exact
    exposure. A target with one mount mode is FLAT (<target>_sub/lights/), even if
    it mixes lengths within that mode (20 s + 30 s EQ stack together); a target
    with both modes is split into <mode>/lights/ (altaz/, eq/). Keys are mode
    labels ('altaz'/'eq') or None when unknown. No filesystem access."""

    def setUp(self):
        self.t = Path("/nas/Seestar/M_57_sub")

    def test_single_mode_already_flat_no_moves(self):
        modes = {
            self.t / "lights" / "a.fit": "eq",
            self.t / "lights" / "b.fit": "eq",
        }
        self.assertEqual(sort_by_exptime.plan_layout(modes, self.t), {})

    def test_single_mode_combines_mixed_lengths_to_flat(self):
        # M_57: 20 s + 30 s, both EQ → one flat lights/ (combine, don't split).
        modes = {
            self.t / "20s" / "lights" / "a.fit": "eq",
            self.t / "30s" / "lights" / "b.fit": "eq",
        }
        self.assertEqual(
            sort_by_exptime.plan_layout(modes, self.t),
            {
                self.t / "20s" / "lights" / "a.fit": self.t / "lights" / "a.fit",
                self.t / "30s" / "lights" / "b.fit": self.t / "lights" / "b.fit",
            },
        )

    def test_two_modes_split_into_altaz_and_eq(self):
        m = Path("/nas/Seestar/M_63_sub")
        modes = {
            m / "lights" / "a.fit": "altaz",
            m / "lights" / "b.fit": "eq",
        }
        self.assertEqual(
            sort_by_exptime.plan_layout(modes, m),
            {
                m / "lights" / "a.fit": m / "altaz" / "lights" / "a.fit",
                m / "lights" / "b.fit": m / "eq" / "lights" / "b.fit",
            },
        )

    def test_two_modes_combine_eq_lengths_into_one_eq_folder(self):
        # M_63: 10 s alt-az + (20 s, 30 s) EQ → altaz/ + a SINGLE eq/ (20 s & 30 s
        # together), not three folders.
        m = Path("/nas/Seestar/M_63_sub")
        modes = {
            m / "10s" / "lights" / "a.fit": "altaz",
            m / "20s" / "lights" / "b.fit": "eq",
            m / "30s" / "lights" / "c.fit": "eq",
        }
        moves = sort_by_exptime.plan_layout(modes, m)
        self.assertEqual(moves[m / "20s" / "lights" / "b.fit"], m / "eq" / "lights" / "b.fit")
        self.assertEqual(moves[m / "30s" / "lights" / "c.fit"], m / "eq" / "lights" / "c.fit")
        self.assertEqual(moves[m / "10s" / "lights" / "a.fit"], m / "altaz" / "lights" / "a.fit")

    def test_two_modes_already_split_no_moves(self):
        m = Path("/nas/Seestar/M_63_sub")
        modes = {
            m / "altaz" / "lights" / "a.fit": "altaz",
            m / "eq" / "lights" / "b.fit": "eq",
        }
        self.assertEqual(sort_by_exptime.plan_layout(modes, m), {})

    def test_unknown_mode_in_two_mode_target_stays_put(self):
        m = Path("/nas/Seestar/M_63_sub")
        modes = {
            m / "lights" / "a.fit": "altaz",
            m / "lights" / "b.fit": "eq",
            m / "lights" / "c.fit": None,
        }
        self.assertNotIn(m / "lights" / "c.fit", sort_by_exptime.plan_layout(modes, m))

    def test_single_mode_with_unknown_stays_flat(self):
        modes = {
            self.t / "20s" / "lights" / "a.fit": "eq",
            self.t / "20s" / "lights" / "b.fit": None,
        }
        self.assertEqual(
            sort_by_exptime.plan_layout(modes, self.t),
            {
                self.t / "20s" / "lights" / "a.fit": self.t / "lights" / "a.fit",
                self.t / "20s" / "lights" / "b.fit": self.t / "lights" / "b.fit",
            },
        )


class NormalizeTargetTests(unittest.TestCase):
    """sort_by_exptime.normalize_target — gather lights, plan, move, tidy empties.
    read_exptime (the astropy boundary) is stubbed; the move/cleanup behaviour is
    exercised for real against temp directories."""

    def _fits(self, d: Path, name: str):
        d.mkdir(parents=True, exist_ok=True)
        (d / name).write_bytes(b"fits")

    @contextlib.contextmanager
    def _exptime_from_name(self):
        """Stub read_exptime to parse '_<n>.0s' out of the filename (or None)."""
        import re as _re
        orig = sort_by_exptime.read_exptime
        def fake(p):
            m = _re.search(r"_(\d+(?:\.\d+)?)s", p.name)
            return float(m.group(1)) if m else None
        sort_by_exptime.read_exptime = fake
        try:
            yield
        finally:
            sort_by_exptime.read_exptime = orig

    def _norm(self, t, dry_run=False):
        # Swallow normalize_target's stdout: it prints a '→' that a Windows cp1252
        # console can't encode (UnicodeEncodeError) when not captured.
        with contextlib.redirect_stdout(io.StringIO()):
            return sort_by_exptime.normalize_target(t, dry_run=dry_run)

    def test_single_exposure_nested_is_flattened(self):
        with tempfile.TemporaryDirectory() as td:
            t = Path(td) / "M_13_sub"
            self._fits(t / "20s" / "lights", "a.fit")
            self._fits(t / "20s" / "lights", "b.fit")
            self._norm(t)
            self.assertEqual(
                sorted(p.name for p in (t / "lights").iterdir()), ["a.fit", "b.fit"]
            )
            self.assertFalse((t / "20s").exists())  # emptied nest removed

    def test_empty_toplevel_lights_scaffold_removed_on_flatten(self):
        # The exact M_13 shape: empty top-level lights/ beside populated 20s/lights/.
        with tempfile.TemporaryDirectory() as td:
            t = Path(td) / "M_13_sub"
            (t / "lights").mkdir(parents=True)            # empty scaffold
            self._fits(t / "20s" / "lights", "a.fit")
            self._norm(t)
            self.assertEqual([p.name for p in (t / "lights").iterdir()], ["a.fit"])
            self.assertFalse((t / "20s").exists())

    def test_both_modes_flat_split_into_altaz_and_eq(self):
        # 10 s (alt-az) + 20 s (EQ) loose in lights/ → split by mode.
        with tempfile.TemporaryDirectory() as td:
            t = Path(td) / "M_51_sub"
            self._fits(t / "lights", "Light_10.0s_x.fit")
            self._fits(t / "lights", "Light_20.0s_y.fit")
            with self._exptime_from_name():
                self._norm(t)
            self.assertEqual(
                [p.name for p in (t / "altaz" / "lights").iterdir()], ["Light_10.0s_x.fit"]
            )
            self.assertEqual(
                [p.name for p in (t / "eq" / "lights").iterdir()], ["Light_20.0s_y.fit"]
            )
            self.assertFalse((t / "lights").exists())  # emptied flat removed

    def test_single_mode_eq_combines_20s_and_30s_to_flat(self):
        # The M_57 case: 20 s + 30 s are both EQ → ONE flat lights/, not split.
        with tempfile.TemporaryDirectory() as td:
            t = Path(td) / "M_57_sub"
            self._fits(t / "20s" / "lights", "a.fit")
            self._fits(t / "30s" / "lights", "b.fit")
            self._norm(t)
            self.assertEqual(
                sorted(p.name for p in (t / "lights").iterdir()), ["a.fit", "b.fit"]
            )
            self.assertFalse((t / "20s").exists())
            self.assertFalse((t / "30s").exists())

    def test_three_exposure_target_collapses_to_altaz_plus_combined_eq(self):
        # The M_63 case: 10 s + 20 s + 30 s → altaz/ (10 s) + eq/ (20 s & 30 s).
        with tempfile.TemporaryDirectory() as td:
            t = Path(td) / "M_63_sub"
            self._fits(t / "10s" / "lights", "alt.fit")
            self._fits(t / "20s" / "lights", "eq20.fit")
            self._fits(t / "30s" / "lights", "eq30.fit")
            self._norm(t)
            self.assertEqual([p.name for p in (t / "altaz" / "lights").iterdir()], ["alt.fit"])
            self.assertEqual(
                sorted(p.name for p in (t / "eq" / "lights").iterdir()), ["eq20.fit", "eq30.fit"]
            )
            for legacy in ("10s", "20s", "30s"):
                self.assertFalse((t / legacy).exists())

    def test_idempotent_when_already_mode_split(self):
        # A canonical two-mode target (altaz/ + eq/) must be a no-op on re-run.
        with tempfile.TemporaryDirectory() as td:
            t = Path(td) / "M_63_sub"
            self._fits(t / "altaz" / "lights", "alt.fit")
            self._fits(t / "eq" / "lights", "eq.fit")
            summary = self._norm(t)
            self.assertEqual(summary["moved"], 0)
            self.assertTrue((t / "altaz" / "lights" / "alt.fit").exists())
            self.assertTrue((t / "eq" / "lights" / "eq.fit").exists())

    def test_dry_run_counts_existing_destination_as_skipped(self):
        # A frame whose destination already exists must count as *skipped*, not
        # moved — and the dry-run summary must agree with a real run.
        with tempfile.TemporaryDirectory() as td:
            t = Path(td) / "M_13_sub"
            self._fits(t / "30s" / "lights", "Light_30.0s_a.fit")   # → lights/…
            self._fits(t / "lights", "Light_30.0s_a.fit")           # …already there
            with self._exptime_from_name():
                summary = self._norm(t, dry_run=True)
            self.assertEqual(summary["moved"], 0)
            self.assertEqual(summary["skipped"], 1)

    def test_idempotent_when_already_flat_single(self):
        with tempfile.TemporaryDirectory() as td:
            t = Path(td) / "M_13_sub"
            self._fits(t / "lights", "Light_20.0s_a.fit")
            with self._exptime_from_name():
                self._norm(t)
                self.assertEqual(
                    [p.name for p in (t / "lights").iterdir()], ["Light_20.0s_a.fit"]
                )
                self._norm(t)  # second run
            self.assertEqual(
                [p.name for p in (t / "lights").iterdir()], ["Light_20.0s_a.fit"]
            )

    def test_dry_run_moves_nothing(self):
        with tempfile.TemporaryDirectory() as td:
            t = Path(td) / "M_13_sub"
            self._fits(t / "20s" / "lights", "a.fit")
            self._norm(t, dry_run=True)
            self.assertTrue((t / "20s" / "lights" / "a.fit").exists())  # untouched
            self.assertFalse((t / "lights").exists())

    def test_dry_run_reports_only_dirs_that_would_actually_empty(self):
        # M_13 shape: empty top-level lights/ (will RECEIVE the flattened files)
        # beside populated 20s/lights/. The dry-run must report removing 20s/lights/
        # and 20s/ — NOT the top-level lights/, which ends up populated.
        with tempfile.TemporaryDirectory() as td:
            t = Path(td) / "M_13_sub"
            (t / "lights").mkdir(parents=True)        # empty scaffold
            self._fits(t / "20s" / "lights", "a.fit")
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                sort_by_exptime.normalize_target(t, dry_run=True)
            out = buf.getvalue()
            self.assertIn("20s/lights/", out)
            self.assertIn("20s/", out)
            # The bare top-level lights/ must NOT be reported as a removal.
            self.assertNotIn("remove empty lights/", out)
            self.assertNotIn("removed empty lights/", out)


class CollectFitsFilesTests(unittest.TestCase):
    """count_subs.collect_fits_files — must count subs in EVERY layout: a flat
    lights/, the mount-mode folders altaz//eq/, and legacy <exp>s/, without
    picking up darks/ or flats/."""

    def _fits(self, d: Path, name: str):
        d.mkdir(parents=True, exist_ok=True)
        (d / name).write_bytes(b"fits")

    def test_counts_mode_split_altaz_and_eq(self):
        with tempfile.TemporaryDirectory() as td:
            t = Path(td) / "M_51_sub"
            self._fits(t / "altaz" / "lights", "Light_10.0s_a.fit")
            self._fits(t / "eq" / "lights", "Light_20.0s_b.fit")
            self._fits(t / "eq" / "lights", "Light_30.0s_c.fit")
            names = sorted(f.name for f in count_subs.collect_fits_files(t))
            self.assertEqual(
                names, ["Light_10.0s_a.fit", "Light_20.0s_b.fit", "Light_30.0s_c.fit"]
            )

    def test_still_counts_flat_and_legacy_exposure_folders(self):
        with tempfile.TemporaryDirectory() as td:
            t = Path(td) / "M_13_sub"
            self._fits(t / "lights", "Light_20.0s_flat.fit")
            self._fits(t / "10s" / "lights", "Light_10.0s_legacy.fit")
            names = sorted(f.name for f in count_subs.collect_fits_files(t))
            self.assertEqual(names, ["Light_10.0s_legacy.fit", "Light_20.0s_flat.fit"])

    def test_ignores_darks_and_flats(self):
        with tempfile.TemporaryDirectory() as td:
            t = Path(td) / "M_1_sub"
            self._fits(t / "eq" / "lights", "Light_20.0s_a.fit")
            self._fits(t / "darks", "dark.fit")   # no lights/ inside → ignored
            self._fits(t / "flats", "flat.fit")
            names = [f.name for f in count_subs.collect_fits_files(t)]
            self.assertEqual(names, ["Light_20.0s_a.fit"])


class ResolveInventoryPathTests(unittest.TestCase):
    """count_subs.resolve_inventory_path — --inventory > SEESTAR_VAULT_INV > default."""

    def test_explicit_file_wins(self):
        with tempfile.TemporaryDirectory() as td:
            f = Path(td) / "Inv.md"
            f.write_text("x", encoding="utf-8")
            self.assertEqual(count_subs.resolve_inventory_path(str(f)), f)

    def test_directory_gets_standard_relpath_appended(self):
        with tempfile.TemporaryDirectory() as td:
            inv = Path(td) / "Astrophotography" / "AstroImages Inventory.md"
            inv.parent.mkdir(parents=True)
            inv.write_text("x", encoding="utf-8")
            resolved = count_subs.resolve_inventory_path(str(td))
            self.assertEqual(resolved, inv)

    def test_missing_returns_none(self):
        import os
        with tempfile.TemporaryDirectory() as td:
            old_default = count_subs._VAULT_INV
            old_env = os.environ.pop("SEESTAR_VAULT_INV", None)
            count_subs._VAULT_INV = Path(td) / "no-such-default.md"
            try:
                self.assertIsNone(
                    count_subs.resolve_inventory_path(str(Path(td) / "nope.md"))
                )
            finally:
                count_subs._VAULT_INV = old_default
                if old_env is not None:
                    os.environ["SEESTAR_VAULT_INV"] = old_env

    def test_env_var_used_when_no_explicit(self):
        import os
        with tempfile.TemporaryDirectory() as td:
            f = Path(td) / "Env.md"
            f.write_text("x", encoding="utf-8")
            old = os.environ.get("SEESTAR_VAULT_INV")
            os.environ["SEESTAR_VAULT_INV"] = str(f)
            try:
                self.assertEqual(count_subs.resolve_inventory_path(None), f)
            finally:
                if old is None:
                    os.environ.pop("SEESTAR_VAULT_INV", None)
                else:
                    os.environ["SEESTAR_VAULT_INV"] = old


class FindStackUnitsTests(unittest.TestCase):
    """batch_stack.find_stack_units — locate stackable units (dirs holding a
    lights/ of raw FITS) under BOTH the legacy flat and canonical exp-sorted
    layouts."""

    def _mkfits(self, d: Path, name: str = "Light_M_51_20.0s_IRCUT_20260501.fit"):
        d.mkdir(parents=True, exist_ok=True)
        (d / name).write_bytes(b"fits")

    def test_legacy_flat_layout(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            self._mkfits(root / "M_57_sub" / "lights")
            units = batch_stack.find_stack_units(root)
            self.assertEqual([u.name for u in units], ["M_57_sub"])

    def test_canonical_exposure_layout(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            self._mkfits(root / "M_51_sub" / "10s" / "lights")
            self._mkfits(root / "M_51_sub" / "20s" / "lights")
            units = batch_stack.find_stack_units(root)
            # .as_posix() so the assertion is OS-agnostic (Windows uses \).
            rels = sorted(u.relative_to(root).as_posix() for u in units)
            # Each exposure folder is its own unit (10s and 20s stack separately).
            self.assertEqual(rels, ["M_51_sub/10s", "M_51_sub/20s"])

    def test_handles_subs_suffix(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            self._mkfits(root / "NGC_6946_subs" / "20s" / "lights")
            units = batch_stack.find_stack_units(root)
            self.assertEqual(
                [u.relative_to(root).as_posix() for u in units], ["NGC_6946_subs/20s"]
            )

    def test_ignores_lights_without_fits(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "M_99_sub" / "lights").mkdir(parents=True)  # empty lights/
            self.assertEqual(batch_stack.find_stack_units(root), [])

    def test_single_exposure_with_empty_toplevel_lights(self):
        # The real M_13 shape: organize re-creates an empty <target>_sub/lights/
        # scaffold while the actual subs live in <target>_sub/20s/lights/. The
        # unit must be ONLY the populated 20s folder — never the _sub root, whose
        # empty lights/ would send Siril's `cd lights` into an empty directory and
        # stack nothing. Guards the "set the working dir to 20s, not M_13_sub" trap.
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "M_13_sub" / "lights").mkdir(parents=True)  # empty scaffold
            self._mkfits(root / "M_13_sub" / "20s" / "lights")
            units = batch_stack.find_stack_units(root)
            self.assertEqual(
                [u.relative_to(root).as_posix() for u in units], ["M_13_sub/20s"]
            )

    def test_ignores_macos_resource_fork_only_lights(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            self._mkfits(root / "M_1_sub" / "lights", name="._Light_M_1.fit")
            self.assertEqual(batch_stack.find_stack_units(root), [])

    @unittest.skipUnless(os.name == "posix", "POSIX permission semantics")
    @unittest.skipIf(hasattr(os, "geteuid") and os.geteuid() == 0, "root bypasses perms")
    def test_unreadable_lights_dir_is_skipped_not_fatal(self):
        # A lights/ dir we can't list (permissions / transient NAS error) must be
        # skipped, not crash the whole scan.
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            self._mkfits(root / "M_good_sub" / "20s" / "lights")
            bad = root / "M_bad_sub" / "lights"
            self._mkfits(bad)
            os.chmod(bad, 0o000)
            try:
                units = batch_stack.find_stack_units(root)
                self.assertEqual(
                    [u.relative_to(root).as_posix() for u in units], ["M_good_sub/20s"]
                )
            finally:
                os.chmod(bad, 0o755)  # restore so TemporaryDirectory can clean up

    def test_filter_matches_target_in_path(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            self._mkfits(root / "M_51_sub" / "20s" / "lights")
            self._mkfits(root / "M_57_sub" / "20s" / "lights")
            units = batch_stack.find_stack_units(root, filter_name="M_51")
            self.assertEqual(
                [u.relative_to(root).as_posix() for u in units], ["M_51_sub/20s"]
            )

    def test_pointed_directly_at_exposure_folder(self):
        # README workaround: point batch_stack at the <exp>s/ folder itself.
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            self._mkfits(root / "lights")  # root IS the exposure unit
            units = batch_stack.find_stack_units(root)
            self.assertEqual(units, [root])


class FindTargetDirsTests(unittest.TestCase):
    """sort_by_exptime / purge_siril_cruft target discovery includes both
    _sub and _subs suffixes."""

    def _make_tree(self, root: Path):
        (root / "M_51_sub").mkdir()
        (root / "NGC_6946_subs").mkdir()
        (root / "not_a_target").mkdir()
        (root / "loose_file.fit").write_bytes(b"x")

    def test_sort_by_exptime_includes_sub_and_subs(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            self._make_tree(root)
            names = sorted(d.name for d in sort_by_exptime.find_target_dirs(root))
            self.assertEqual(names, ["M_51_sub", "NGC_6946_subs"])

    def test_purge_includes_sub_and_subs(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            self._make_tree(root)
            names = sorted(d.name for d in purge_siril_cruft.find_target_dirs(root))
            self.assertEqual(names, ["M_51_sub", "NGC_6946_subs"])


class MergeIntoExistingTests(unittest.TestCase):
    """rename_seestar_folders.merge_into_existing — fold a space-named donor
    folder into the already-canonical destination instead of skipping."""

    def _mkfits(self, path: Path, name: str) -> Path:
        path.mkdir(parents=True, exist_ok=True)
        f = path / name
        f.write_bytes(b"fits")
        return f

    def _merge(self, donor, dest, dry_run):
        # Capture stdout: merge_into_existing prints emoji progress, which
        # crashes on a cp1252 console (Windows CI). The test asserts on
        # filesystem state + return value, not console output.
        with contextlib.redirect_stdout(io.StringIO()):
            return rename_seestar_folders.merge_into_existing(donor, dest, dry_run=dry_run)

    def test_moves_new_donor_root_fits_into_existing_dest(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            donor = root / "M 51_sub"
            dest = root / "M_51_sub"
            # Existing canonical archive already has a sorted sub.
            self._mkfits(dest / "20s" / "lights", "Light_0001.fit")
            # New incremental sync dropped a raw sub at the donor root.
            self._mkfits(donor, "Light_0003.fit")

            res = self._merge(donor, dest, dry_run=False)

            self.assertTrue((dest / "Light_0003.fit").exists())
            self.assertFalse(donor.exists())
            self.assertEqual(res["moved"], 1)
            self.assertEqual(res["deduped"], 0)

    def test_dedupes_filename_already_present_anywhere_in_dest(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            donor = root / "M 51_sub"
            dest = root / "M_51_sub"
            already = self._mkfits(dest / "20s" / "lights", "Light_0001.fit")
            # Donor re-delivers a sub that already lives in the sorted tree.
            self._mkfits(donor, "Light_0001.fit")

            res = self._merge(donor, dest, dry_run=False)

            self.assertEqual(res["moved"], 0)
            self.assertEqual(res["deduped"], 1)
            self.assertFalse(donor.exists())
            # The already-sorted sub is untouched; no stray copy at dest root.
            self.assertTrue(already.exists())
            self.assertFalse((dest / "Light_0001.fit").exists())

    def test_same_name_different_size_is_kept_not_deleted(self):
        # Hardening: dedupe only when name AND size match. A same-name file with
        # a DIFFERENT size is not the same frame, so it must be preserved, never
        # silently unlinked. (Existing copy lives elsewhere in the tree, so the
        # donor copy lands at the dest root un-renamed — no path collision.)
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            donor = root / "M 51_sub"
            dest = root / "M_51_sub"
            sorted_dir = dest / "20s" / "lights"
            sorted_dir.mkdir(parents=True)
            (sorted_dir / "Light_0001.fit").write_bytes(b"AAAA")          # size 4
            donor.mkdir()
            (donor / "Light_0001.fit").write_bytes(b"BBBBBBBB")           # size 8

            res = self._merge(donor, dest, dry_run=False)

            self.assertEqual(res["deduped"], 0)
            self.assertEqual(res["moved"], 1)
            self.assertFalse(donor.exists())
            # Original sorted sub untouched; donor copy preserved at dest root.
            self.assertEqual((sorted_dir / "Light_0001.fit").read_bytes(), b"AAAA")
            self.assertEqual((dest / "Light_0001.fit").read_bytes(), b"BBBBBBBB")

    def test_true_path_collision_renamed_to_dup_not_overwritten(self):
        # Same name + different size at the SAME relative path → collision-safe
        # _dupN rename, original preserved (never overwritten or unlinked).
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            donor = root / "M 51_sub"
            dest = root / "M_51_sub"
            dest.mkdir()
            (dest / "Light_0002.fit").write_bytes(b"AAAA")               # size 4
            donor.mkdir()
            (donor / "Light_0002.fit").write_bytes(b"BBBBBBBB")          # size 8

            res = self._merge(donor, dest, dry_run=False)

            self.assertEqual(res["deduped"], 0)
            self.assertEqual(res["moved"], 1)
            self.assertEqual((dest / "Light_0002.fit").read_bytes(), b"AAAA")
            self.assertEqual((dest / "Light_0002_dup1.fit").read_bytes(), b"BBBBBBBB")

    def test_preserves_donor_subtree_structure(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            donor = root / "M 51_sub"
            dest = root / "M_51_sub"
            dest.mkdir()
            self._mkfits(donor / "lights", "Light_0009.fit")

            self._merge(donor, dest, dry_run=False)

            self.assertTrue((dest / "lights" / "Light_0009.fit").exists())
            self.assertFalse(donor.exists())

    def test_dry_run_makes_no_changes_but_reports_counts(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            donor = root / "M 51_sub"
            dest = root / "M_51_sub"
            self._mkfits(dest / "20s" / "lights", "Light_0001.fit")
            self._mkfits(donor, "Light_0003.fit")

            res = self._merge(donor, dest, dry_run=True)

            # Nothing on disk changed...
            self.assertTrue(donor.exists())
            self.assertTrue((donor / "Light_0003.fit").exists())
            self.assertFalse((dest / "Light_0003.fit").exists())
            # ...but the report shows what WOULD happen.
            self.assertEqual(res["moved"], 1)
            self.assertEqual(res["deduped"], 0)


class IsInExcludedTests(unittest.TestCase):
    """seestar_common.is_in_excluded — pure path predicate for _trash / scripts."""

    def test_top_level_target_not_excluded(self):
        root = Path("/x")
        self.assertFalse(seestar_common.is_in_excluded(root / "M_51_sub", root))

    def test_under_trash_is_excluded(self):
        root = Path("/x")
        self.assertTrue(
            seestar_common.is_in_excluded(root / "_trash" / "strays" / "M 51_sub", root)
        )

    def test_under_scripts_is_excluded(self):
        root = Path("/x")
        self.assertTrue(seestar_common.is_in_excluded(root / "scripts" / "M_1_sub", root))

    def test_path_outside_root_not_excluded(self):
        self.assertFalse(seestar_common.is_in_excluded(Path("/y/_trash/a"), Path("/x")))

    def test_excluded_match_is_case_insensitive(self):
        # Case-insensitive filesystems (macOS/Windows) may create Scripts/, _Trash/.
        root = Path("/x")
        self.assertTrue(seestar_common.is_in_excluded(root / "Scripts" / "a", root))
        self.assertTrue(seestar_common.is_in_excluded(root / "_Trash" / "strays" / "M 1_sub", root))


class ExcludesTrashTests(unittest.TestCase):
    """All recursive target-discovery scanners skip _trash/ (and scripts/)."""

    def _mkfits(self, d: Path, name="Light_M_1_20.0s_IRCUT_20260501.fit"):
        d.mkdir(parents=True, exist_ok=True)
        (d / name).write_bytes(b"fits")

    def test_rename_find_sub_folders_skips_trash(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "M 51_sub").mkdir()                      # real, top level
            (root / "_trash" / "strays" / "M 57_sub").mkdir(parents=True)
            names = [p.name for p in rename_seestar_folders.find_sub_folders(root)]
            self.assertEqual(names, ["M 51_sub"])

    def test_count_find_sub_folders_skips_trash(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "M_51_sub").mkdir()
            (root / "_trash" / "M_57_sub").mkdir(parents=True)
            names = [p.name for p in count_subs.find_sub_folders(root)]
            self.assertEqual(names, ["M_51_sub"])

    def test_organize_find_sub_folders_skips_trash(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "M_51_sub").mkdir()
            (root / "_trash" / "M_57_sub").mkdir(parents=True)
            names = [p.name for p in organize_subs.find_sub_folders(root)]
            self.assertEqual(names, ["M_51_sub"])

    def test_batch_stack_find_stack_units_skips_trash(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            self._mkfits(root / "M_1_sub" / "lights")
            self._mkfits(root / "_trash" / "M_2_sub" / "lights")
            units = [u.relative_to(root).as_posix() for u in batch_stack.find_stack_units(root)]
            self.assertEqual(units, ["M_1_sub"])

    def test_sort_find_target_dirs_skips_trash(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "M_1_sub").mkdir()
            (root / "_trash" / "M_2_sub").mkdir(parents=True)
            names = [d.name for d in sort_by_exptime.find_target_dirs(root)]
            self.assertEqual(names, ["M_1_sub"])


if __name__ == "__main__":
    unittest.main()
