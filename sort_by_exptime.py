#!/usr/bin/env python3
"""
sort_by_exptime.py  —  Sort Seestar FITS subs into per-exposure subfolders.

Alt-az captures (10 s, to suppress star trails) and EQ captures (20 s+)
should be stacked separately in Siril.  This script reads EXPTIME from
each FITS primary header and moves files into  10s/,  20s/,  etc.
subfolders.

The script recurses into date/session subdirectories so it works regardless
of how deep the FITS files are nested inside a target folder.  Each
directory that contains FITS files is sorted independently.  Directories
whose names already look like an exposure label (e.g. "10s", "20s") are
skipped so re-runs are safe.

USAGE
-----
  # Always dry-run first:
  python3 sort_by_exptime.py --dry-run M_51_sub/

  # Sort a single target folder (recurses automatically):
  python3 sort_by_exptime.py M_51_sub/

  # Sort every *_sub folder under a root:
  python3 sort_by_exptime.py --all /Volumes/NAS/Seestar/

  # Dry-run everything:
  python3 sort_by_exptime.py --dry-run --all /Volumes/NAS/Seestar/

  # Pass multiple explicit folders:
  python3 sort_by_exptime.py M_51_sub/ M_27_sub/ M_57_sub/

AFTER SORTING
-------------
  Files move from  M_101_sub/lights/  into:
    M_101_sub/10s/lights/   (alt-az subs)
    M_101_sub/20s/lights/   (EQ subs)

  Point batch_stack.py at the exposure folder — it finds lights/ inside:

    python3 batch_stack.py "M 101_sub/20s/"

  Or loop all exposure subfolders for a target:

    for d in "M 101_sub"/*/; do python3 batch_stack.py "$d"; done

NOTES
-----
  • Requires astropy  →  pip install astropy --break-system-packages
  • Run from local disk, not from a network share — macOS kills Python
    processes spawned from NFS/SMB volumes.
  • macOS ._  resource-fork files are skipped automatically.
"""

import argparse
import pathlib
import re
import shutil
import sys
from collections import defaultdict
from typing import Optional

# ── dependencies ─────────────────────────────────────────────────────────────
# astropy is imported lazily (see _fits_module) so this module can be imported —
# and its pure helpers unit-tested — without astropy installed. Only the actual
# FITS-header read in read_exptime() needs it.

_FITS_MODULE = None


def _fits_module():
    """Import astropy.io.fits on first use; exit with an install hint if missing."""
    global _FITS_MODULE
    if _FITS_MODULE is None:
        try:
            from astropy.io import fits
        except ImportError:
            sys.exit(
                "astropy not found.\n"
                "Install:  pip install astropy --break-system-packages\n"
                "     or:  /opt/homebrew/bin/pip3 install astropy"
            )
        _FITS_MODULE = fits
    return _FITS_MODULE

# ── constants ─────────────────────────────────────────────────────────────────

FITS_SUFFIXES = {".fit", ".fits"}
SKIP_PREFIXES = ("._", ".DS_Store")    # macOS resource-fork noise

# ── helpers ───────────────────────────────────────────────────────────────────

def is_fits(path: pathlib.Path) -> bool:
    """True for real FITS files; skips macOS metadata cruft."""
    return (
        path.suffix.lower() in FITS_SUFFIXES
        and not path.name.startswith(SKIP_PREFIXES)
    )


def read_exptime(path: pathlib.Path) -> Optional[float]:
    """Return EXPTIME (seconds) from the FITS primary header, or None on error."""
    fits = _fits_module()
    try:
        with fits.open(path, memmap=False, ignore_missing_simple=True) as hdul:
            val = hdul[0].header.get("EXPTIME")
            if val is not None:
                return float(val)
    except Exception:
        pass
    return None


def exptime_label(exptime: float) -> str:
    """Convert a float exposure to a clean folder name: 10.0 → '10s', 20.5 → '20.5s'."""
    return f"{int(exptime)}s" if exptime == int(exptime) else f"{exptime}s"


# ── exptime-label pattern (matches already-sorted folders like "10s", "20.5s") ─

EXPTIME_DIR_RE = re.compile(r'^\d+(\.\d+)?s$')

# Siril working dirs and other non-raw locations to leave alone
SKIP_DIR_NAMES = {
    "process", "processing",  # Siril preprocessed frames (pp_light_*, r_pp_light_*)
    "registered",             # Siril registered frames
    "stack",                  # stacked outputs
    "biases", "darks", "flats",  # calibration frames
}


# ── core ──────────────────────────────────────────────────────────────────────

def is_already_sorted(directory: pathlib.Path, root: pathlib.Path) -> bool:
    """
    True if *directory* already sits under an exposure-label folder, e.g.
    root/M_51_sub/10s/lights or root/M_51_sub/20s. Such locations are canonical
    and must be skipped so re-runs are idempotent and never re-nest
    (…/10s/10s/…). Pure function — no filesystem access.
    """
    try:
        rel = directory.relative_to(root)
    except ValueError:
        return False
    return any(EXPTIME_DIR_RE.match(part) for part in rel.parts)


def fits_dirs(root: pathlib.Path) -> list[pathlib.Path]:
    """
    Return every directory under *root* that contains FITS files directly,
    excluding:
      • directories already under an <exptime>s/ label (canonical — done)
      • Siril working directories (process/, registered/, stack/, etc.)
      • *root* itself (top-level stacked masters live there; leave them alone)
    """
    seen: set[pathlib.Path] = set()
    for f in root.rglob("*"):
        if not (f.is_file() and is_fits(f)):
            continue
        parent = f.parent
        if parent == root:
            continue                              # skip top-level masters
        if is_already_sorted(parent, root):
            continue                              # already in <exptime>s/…
        if parent.name.lower() in SKIP_DIR_NAMES:
            continue                              # Siril working dirs
        seen.add(parent)
    return sorted(seen)


def sort_directory(target: pathlib.Path, dry_run: bool) -> None:
    """
    Read EXPTIME from every FITS file directly in *target* and move each
    file into a matching  <exptime>/  subfolder.  Already-sorted subfolders
    (name matches e.g. "20s") are never touched, so re-running is safe.
    """
    fits_files = [f for f in target.iterdir() if f.is_file() and is_fits(f)]

    if not fits_files:
        return   # nothing here; fits_dirs() already filtered empties

    by_exptime: dict[float, list[pathlib.Path]] = defaultdict(list)
    unreadable: list[pathlib.Path] = []

    print(f"  {target}: reading headers ({len(fits_files)} files) …", end="", flush=True)
    for f in fits_files:
        exp = read_exptime(f)
        if exp is None:
            unreadable.append(f)
        else:
            by_exptime[exp].append(f)
    print(" done")

    if unreadable:
        print(f"  ⚠  {len(unreadable)} unreadable file(s):")
        for p in unreadable[:5]:
            print(f"       {p.name}")
        if len(unreadable) > 5:
            print(f"       … and {len(unreadable) - 5} more")

    if not by_exptime:
        print(f"    no readable FITS — nothing to do")
        return

    # Sort into per-exposure subfolders — ALWAYS, even when only one exposure
    # length is present, so every target ends up in the same canonical shape:
    #   <target>_sub/<exptime>s/lights/
    # The destination preserves the source dir name (usually "lights") so
    # batch_stack.py works unchanged.  e.g.
    #   M_101_sub/lights/ → M_101_sub/10s/lights/ + M_101_sub/20s/lights/
    total = sum(len(v) for v in by_exptime.values())
    n_exp = len(by_exptime)
    plural = "exposure length" if n_exp == 1 else "exposure lengths"
    print(f"    {total} files across {n_exp} {plural} — SORTING")

    for exp in sorted(by_exptime):
        files = by_exptime[exp]
        # sibling of target's parent: ../10s/lights/
        dest_dir = target.parent / exptime_label(exp) / target.name
        tag = "[dry-run] " if dry_run else ""
        print(f"      {tag}{len(files):>4d} × {exptime_label(exp):>6s}  →  {dest_dir.relative_to(target.parent.parent)}/")

        if not dry_run:
            dest_dir.mkdir(parents=True, exist_ok=True)
            skipped = 0
            for f in files:
                dest = dest_dir / f.name
                if dest.exists():
                    skipped += 1
                else:
                    shutil.move(str(f), dest)
            if skipped:
                print(f"             (skipped {skipped} already-present file(s))")

    # The old flat source dir (e.g. lights/) is now empty — remove it for
    # tidiness so only the canonical <exptime>s/lights/ folders remain.
    if not dry_run:
        try:
            if not any(target.iterdir()):
                target.rmdir()
                print(f"    removed empty {target.name}/")
        except OSError:
            pass

    if dry_run:
        print(f"    → re-run without --dry-run to apply")


# ── main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    ap = argparse.ArgumentParser(
        description="Sort Seestar FITS subs into per-exposure-time subfolders.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument(
        "paths",
        nargs="*",
        type=pathlib.Path,
        metavar="PATH",
        help="Target folder(s) containing FITS subs (e.g. M_51_sub/)",
    )
    ap.add_argument(
        "--all",
        dest="root",
        type=pathlib.Path,
        metavar="ROOT",
        help="Scan ROOT and sort every *_sub directory found inside it",
    )
    ap.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview what would happen — no files are moved",
    )
    args = ap.parse_args()

    if not args.paths and args.root is None:
        ap.print_help()
        sys.exit(1)

    targets: list[pathlib.Path] = []

    if args.root:
        root = args.root.expanduser().resolve()
        if not root.is_dir():
            sys.exit(f"ERROR: not a directory: {root}")
        targets = sorted(d for d in root.iterdir() if d.is_dir() and d.name.endswith("_sub"))
        if not targets:
            sys.exit(f"No *_sub directories found under {root}")
        print(f"Found {len(targets)} target folder(s) under {root}\n")

    for p in args.paths:
        targets.append(p.expanduser().resolve())

    mode = "DRY RUN — no files will be moved" if args.dry_run else "MOVING FILES"
    print(f"sort_by_exptime  [{mode}]")
    print("─" * 60)

    for target in targets:
        if not target.is_dir():
            print(f"  SKIP (not a directory): {target}")
            continue
        # Recurse to find every directory that actually contains FITS files
        # (e.g. M_51_sub/Light/, M_51_sub/20240101/Light/, etc.)
        dirs = fits_dirs(target)
        if not dirs:
            print(f"  {target.name}: no FITS files found anywhere inside")
            continue
        for d in dirs:
            sort_directory(d, dry_run=args.dry_run)

    print("─" * 60)
    if args.dry_run:
        print("Dry run complete.  Re-run without --dry-run to move files.")
    else:
        print("Done.")


if __name__ == "__main__":
    main()
