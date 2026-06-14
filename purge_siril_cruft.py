#!/usr/bin/env python3
"""
purge_siril_cruft.py  —  Reclaim the Seestar archive from Siril cruft.

The CORR/Seestar archive folders (e.g. M_51_sub/) accumulate three kinds of
disposable junk that bloat file counts and space while the *real* data (raw
Light_ subs in lights/, and stacked masters at the target root) stays intact:

  1. process/ working dirs   — Siril intermediates (light_NNNNN.fit, pp_light_*,
                               r_pp_light_*, *.seq, cache). Regenerated on every
                               re-stack, so always safe to delete.
  2. "Copy #N of X" files     — Finder-style duplicates. Deleted ONLY when the
                               original X is present in the same directory
                               (otherwise kept + flagged, so no data is lost).
  3. starless_starless_* and  — double-processed StarNet errors (StarNet applied
     starmask_starless_*        to an already-starless frame). Single starless_/
                               starmask_ outputs are KEPT.

After deletion it hands the surviving lights/ folders to sort_by_exptime.py so
10 s alt-az and 20 s EQ subs end up in separate stacks.

SAFETY
------
  • DRY-RUN BY DEFAULT. Nothing is deleted unless you pass --apply.
  • Idempotent: safe to re-run.
  • Never touches: lights/, darks/, flats/, <exptime>/ dirs, raw Light_ subs,
    stacked masters (*x*sec*.fit), single starless_/starmask_/_processed outputs.

USAGE
-----
  # Always preview first (default is dry-run):
  python3 purge_siril_cruft.py /Volumes/personal_folder/Astro/Seestar

  # One target only:
  python3 purge_siril_cruft.py /Volumes/personal_folder/Astro/Seestar --target M_51_sub

  # Apply for real:
  python3 purge_siril_cruft.py /Volumes/personal_folder/Astro/Seestar --apply

  # Skip the exposure-sort hand-off:
  python3 purge_siril_cruft.py <root> --apply --no-sort

NOTES
-----
  • Run from LOCAL DISK, not the network share — macOS kills Python spawned
    from SMB/NFS volumes. Point the path argument at the NAS instead.
  • Requires sort_by_exptime.py beside this script (for --sort, the default).
    That step needs astropy: pip install astropy --break-system-packages
"""

import argparse
import os
import re
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

# ── what to delete ───────────────────────────────────────────────────────────

# Directory names that are pure Siril working dirs (whole tree is disposable).
PROCESS_DIR_NAMES = {"process", "processing"}

# Finder duplicate prefix: "Copy #1 of <original>"
COPY_RE = re.compile(r"^Copy #\d+ of (.+)$")

# Double-processed StarNet errors.
DOUBLE_RE = re.compile(r"^(?:starless_starless_|starmask_starless_)")

FITS_SUFFIXES = {".fit", ".fits"}
SKIP_PREFIXES = ("._", ".DS_Store")   # macOS resource-fork noise


# ── NAS keepalive (prevents SMB spindown during long deletes) ─────────────────

def start_keepalive(root: Path, interval: int = 15) -> threading.Event:
    stop = threading.Event()

    def _ping():
        while not stop.wait(interval):
            try:
                os.stat(root)
            except OSError:
                pass

    threading.Thread(target=_ping, daemon=True).start()
    return stop


# ── helpers ───────────────────────────────────────────────────────────────────

def human(nbytes: int) -> str:
    f = float(nbytes)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if f < 1024 or unit == "TB":
            return f"{f:.1f} {unit}"
        f /= 1024
    return f"{f:.1f} TB"


def dir_stats(path: Path) -> tuple[int, int]:
    """(file_count, total_bytes) for a directory tree, resilient to errors."""
    files = 0
    total = 0
    for p in path.rglob("*"):
        try:
            if p.is_file():
                files += 1
                total += p.stat().st_size
        except OSError:
            pass
    return files, total


def safe_unlink(path: Path, dry_run: bool) -> tuple[int, int]:
    """Delete one file. Return (count_removed, bytes_removed)."""
    try:
        size = path.stat().st_size
    except OSError:
        size = 0
    if dry_run:
        return 1, size
    try:
        path.unlink()
        return 1, size
    except OSError as e:
        print(f"      ! could not remove {path.name}: {e}")
        return 0, 0


def safe_rmtree(path: Path, dry_run: bool) -> tuple[int, int]:
    """Delete a directory tree. Return (files_removed, bytes_removed)."""
    files, total = dir_stats(path)
    if dry_run:
        return files, total
    try:
        shutil.rmtree(path)
    except OSError as e:
        print(f"      ! could not fully remove {path}/: {e}")
        # report what actually went
        files2, total2 = dir_stats(path) if path.exists() else (0, 0)
        return files - files2, total - total2
    return files, total


# ── per-target cleanup ──────────────────────────────────────────────────────

class Tally:
    def __init__(self):
        self.proc_files = self.proc_bytes = 0
        self.copy_files = self.copy_bytes = 0
        self.dbl_files = self.dbl_bytes = 0
        self.copy_kept = 0

    @property
    def files(self):
        return self.proc_files + self.copy_files + self.dbl_files

    @property
    def bytes(self):
        return self.proc_bytes + self.copy_bytes + self.dbl_bytes


def find_target_dirs(root: Path) -> list[Path]:
    """Top-level *_sub / *_subs directories under root (default target set)."""
    return sorted(
        d for d in root.iterdir()
        if d.is_dir() and (d.name.endswith("_sub") or d.name.endswith("_subs"))
    )


def clean_target(target: Path, dry_run: bool) -> Tally:
    t = Tally()
    print(f"\n  {target.name}")

    # 1. process/ working directories (any depth)
    for d in sorted(target.rglob("*")):
        try:
            if d.is_dir() and d.name.lower() in PROCESS_DIR_NAMES:
                f, b = safe_rmtree(d, dry_run)
                t.proc_files += f
                t.proc_bytes += b
        except OSError:
            pass

    # 2 + 3. file-level deletions (skip anything inside an already-removed
    #        process/ dir; in dry-run that dir still exists so guard explicitly)
    def in_process_dir(p: Path) -> bool:
        return any(part.lower() in PROCESS_DIR_NAMES for part in p.relative_to(target).parts[:-1])

    for p in sorted(target.rglob("*")):
        try:
            if not p.is_file() or p.name.startswith(SKIP_PREFIXES):
                continue
        except OSError:
            continue
        if in_process_dir(p):
            continue  # already accounted for under process/

        # double-processed StarNet errors
        if DOUBLE_RE.match(p.name):
            f, b = safe_unlink(p, dry_run)
            t.dbl_files += f
            t.dbl_bytes += b
            continue

        # Finder duplicates — only if the original is present alongside it
        m = COPY_RE.match(p.name)
        if m:
            original = p.with_name(m.group(1))
            if original.exists():
                f, b = safe_unlink(p, dry_run)
                t.copy_files += f
                t.copy_bytes += b
            else:
                t.copy_kept += 1
                print(f"      keep (no original): {p.name}")

    tag = "would reclaim" if dry_run else "reclaimed"
    print(f"      process/: {t.proc_files} files ({human(t.proc_bytes)})")
    print(f"      Copy # dupes: {t.copy_files} files ({human(t.copy_bytes)})"
          + (f"  [{t.copy_kept} kept — original missing]" if t.copy_kept else ""))
    print(f"      starless_starless: {t.dbl_files} files ({human(t.dbl_bytes)})")
    print(f"      → {tag} {t.files} files, {human(t.bytes)}")
    return t


# ── exposure-sort hand-off ────────────────────────────────────────────────────

def run_exposure_sort(root: Path, targets: list[Path], dry_run: bool) -> None:
    sorter = Path(__file__).resolve().parent / "sort_by_exptime.py"
    if not sorter.exists():
        print(f"\n  (skip exposure-sort: {sorter.name} not found beside this script)")
        return
    print("\n" + "─" * 64)
    print("  Exposure-sort (lights/ → 10s/ 20s/) via sort_by_exptime.py")
    print("─" * 64)
    cmd = [sys.executable, str(sorter)]
    if dry_run:
        cmd.append("--dry-run")
    cmd += [str(t) for t in targets]
    subprocess.run(cmd, check=False)


# ── main ───────────────────────────────────────────────────────────────────────

def main() -> None:
    ap = argparse.ArgumentParser(
        description="Reclaim the Seestar archive from Siril process/ dirs, Copy # dupes, and double-starless files.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("root", type=Path, help="Archive root (e.g. /Volumes/personal_folder/Astro/Seestar)")
    ap.add_argument("--target", action="append", default=[],
                    help="Limit to one target folder (repeatable). Default: every *_sub under root.")
    ap.add_argument("--apply", action="store_true",
                    help="Actually delete. Without this, runs as a dry-run.")
    ap.add_argument("--no-sort", action="store_true",
                    help="Skip the sort_by_exptime.py hand-off.")
    args = ap.parse_args()

    root = args.root.expanduser().resolve()
    if not root.is_dir():
        sys.exit(f"ERROR: not a directory: {root}")

    if args.target:
        targets = [root / t for t in args.target]
    else:
        targets = find_target_dirs(root)
    targets = [t for t in targets if t.is_dir()]
    if not targets:
        sys.exit(f"No target folders found under {root}")

    dry_run = not args.apply
    mode = "DRY RUN — nothing will be deleted (use --apply to execute)" if dry_run else "APPLYING — files will be deleted"
    print("=" * 64)
    print(f"  purge_siril_cruft  [{mode}]")
    print(f"  root: {root}")
    print(f"  targets: {len(targets)}")
    print("=" * 64)

    stop = start_keepalive(root)
    grand = Tally()
    try:
        for target in targets:
            tt = clean_target(target, dry_run)
            grand.proc_files += tt.proc_files; grand.proc_bytes += tt.proc_bytes
            grand.copy_files += tt.copy_files; grand.copy_bytes += tt.copy_bytes
            grand.dbl_files += tt.dbl_files;   grand.dbl_bytes += tt.dbl_bytes
    finally:
        stop.set()

    print("\n" + "=" * 64)
    verb = "Would reclaim" if dry_run else "Reclaimed"
    print(f"  {verb}: {grand.files} files, {human(grand.bytes)}")
    print(f"    process/ intermediates : {grand.proc_files:>7d}  ({human(grand.proc_bytes)})")
    print(f"    Copy # duplicates      : {grand.copy_files:>7d}  ({human(grand.copy_bytes)})")
    print(f"    starless_starless      : {grand.dbl_files:>7d}  ({human(grand.dbl_bytes)})")
    print("=" * 64)

    if not args.no_sort:
        run_exposure_sort(root, targets, dry_run)

    if dry_run:
        print("\nDry run complete. Re-run with --apply to delete.")
    else:
        print("\nDone.")


if __name__ == "__main__":
    main()
