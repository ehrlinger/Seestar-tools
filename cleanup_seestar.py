#!/usr/bin/env python3
"""
cleanup_seestar.py

Cleans up and merges Seestar _sub/_subs folders.
Assumes you are running from the NAS Seestar archive — path defaults to '.'.

Usage:
    python3 cleanup_seestar.py                    # organize current directory
    python3 cleanup_seestar.py /path/to/Seestar   # explicit path
    python3 cleanup_seestar.py --dry-run          # preview, no changes
    python3 cleanup_seestar.py --merge            # merge multi-night sessions
    python3 cleanup_seestar.py --remove-empty     # delete empty _sub folders
    python3 cleanup_seestar.py -h                 # this help

The script is safe to run multiple times (idempotent):
  - Won't move files already in lights/
  - Won't overwrite existing files (renames with _dupN suffix instead)
  - Skips processed/stacked files (starless_, starmask_, _processed., etc.)

Merge behaviour:
  - Groups _sub/_subs folders by target name (the part before any date/index suffix)
  - The alphabetically-first folder for each target becomes the PRIMARY (receives all files)
  - Secondary (donor) folders are left in place but empty of lights/ — delete manually
    once you've verified the merge. Use --dry-run first.
"""

import sys
import shutil
import re
from pathlib import Path
from collections import defaultdict

# ---------------------------------------------------------------------------
# Patterns that identify PROCESSED files — never move these to lights/
# ---------------------------------------------------------------------------
PROCESSED_PATTERNS = [
    r"^starless_",
    r"^starmask_",
    r"_processed\.",
    r"_GraXpert\.",
    r"_pp\.",
    r"^r_pp_",
    r"^stack_",
    r".*\d+x\d+sec.*\.(fit|fits)$",
]

FITS_EXTENSIONS = {".fit", ".fits", ".FIT", ".FITS"}
JPG_EXTENSIONS  = {".jpg", ".jpeg", ".JPG", ".JPEG"}
SIRIL_SUBDIRS   = ["lights", "darks", "flats"]

# ---------------------------------------------------------------------------
# Suffix-stripping for deriving the canonical target name used to group
# multi-night sessions.
#
# Handles patterns like:
#   "M 51_sub"
#   "M 51_sub_2"
#   "M 51 2026-05-22_sub"
#   "M 51_20260522_sub"
#   "M 51_subs"
#   "M 51 (2)_sub"
#
# NOTE: the session-suffix regex deliberately does NOT include a bare \d+
# branch. That would match the target's own catalog number (the "51" in
# "M 51") once the _sub suffix is stripped, collapsing every Messier/NGC
# target into just its prefix letter. The middle _sub_N case is handled
# separately below.
# ---------------------------------------------------------------------------
_SUB_INDEX_SUFFIX_RE = re.compile(r"_(?:subs|sub)_\d+$")
_SUB_SUFFIX_RE       = re.compile(r"_(?:subs|sub)$")
_SESSION_SUFFIX_RE = re.compile(
    r"""
    [\s_-]+           # required separator (prevents matching at end of "M 51")
    (?:
        \d{4}-\d{2}-\d{2}   # ISO date: 2026-05-22
      | \d{8}               # compact date: 20260522
      | \(\d+\)             # parenthesised index: (2)
    )
    $
    """,
    re.VERBOSE,
)


def canonical_target_name(folder_name: str) -> str:
    """
    Strip _sub/_subs and any session-distinguishing suffix to get the
    canonical target name used for grouping multi-night sessions.

    Spaces and underscores are treated as equivalent separators so that
    "M 51_sub" and "M_51_sub" both map to the same canonical key "M 51".

    Examples:
        "M 51_sub"               → "M 51"
        "M_51_sub"               → "M 51"   (underscore normalised to space)
        "M 51 2026-05-22_sub"    → "M 51"
        "NGC 6946_subs"          → "NGC 6946"
        "NGC_6946_sub"           → "NGC 6946"
        "M 51_sub_2"             → "M 51"
        "M 51 (2)_sub"           → "M 51"
    """
    name = folder_name
    name = _SUB_INDEX_SUFFIX_RE.sub("", name)  # _sub_2 / _subs_3 → ""
    name = _SUB_SUFFIX_RE.sub("", name)        # trailing _sub / _subs → ""
    name = _SESSION_SUFFIX_RE.sub("", name)    # trailing date or (N)  → ""
    # Normalise separators: treat space and underscore as equivalent so that
    # "M 51_sub" and "M_51_sub" land in the same group.
    name = name.replace("_", " ")
    name = " ".join(name.split())              # collapse any runs of whitespace
    return name


def is_processed(filename: str) -> bool:
    for pattern in PROCESSED_PATTERNS:
        if re.search(pattern, filename):
            return True
    return False


def find_sub_folders(root: Path) -> list[Path]:
    return sorted(
        p for p in root.rglob("*")
        if p.is_dir() and (p.name.endswith("_sub") or p.name.endswith("_subs"))
    )


def safe_dest(dest: Path) -> Path:
    """If dest already exists, return dest with a _dupN suffix."""
    if not dest.exists():
        return dest
    stem = dest.stem
    suffix = dest.suffix
    parent = dest.parent
    n = 1
    while True:
        candidate = parent / f"{stem}_dup{n}{suffix}"
        if not candidate.exists():
            return candidate
        n += 1


# ---------------------------------------------------------------------------
# Step 1-3: organize a single _sub/_subs folder
# ---------------------------------------------------------------------------

def collect_root_fits(sub_dir: Path) -> list[Path]:
    """FITS files sitting directly in the _sub root (not in subdirs)."""
    return [
        f for f in sub_dir.iterdir()
        if f.is_file() and f.suffix in FITS_EXTENSIONS
    ]


def collect_lights_fits(sub_dir: Path) -> list[Path]:
    lights = sub_dir / "lights"
    if not lights.exists():
        return []
    return [f for f in lights.iterdir() if f.is_file() and f.suffix in FITS_EXTENSIONS]


def clean_sub_folder(sub_dir: Path, dry_run: bool) -> dict:
    summary = {
        "jpgs_deleted": 0,
        "dupes_deleted": 0,
        "fits_moved": 0,
        "dirs_created": [],
        "skipped_fits": [],
        "warnings": [],
    }

    # 1. Delete JPG previews
    for jpg in sub_dir.rglob("*"):
        if jpg.is_file() and jpg.suffix.lower() in JPG_EXTENSIONS:
            print(f"  🗑  DELETE  {jpg.relative_to(sub_dir.parent)}")
            if not dry_run:
                jpg.unlink()
            summary["jpgs_deleted"] += 1

    # 2. Create lights/ darks/ flats/ if missing
    for subdir_name in SIRIL_SUBDIRS:
        target = sub_dir / subdir_name
        if not target.exists():
            print(f"  📁 CREATE  {target.relative_to(sub_dir.parent)}/")
            if not dry_run:
                target.mkdir(parents=True, exist_ok=True)
            summary["dirs_created"].append(subdir_name)

    # 3. Move raw FITS from root into lights/
    lights_dir = sub_dir / "lights"
    existing = collect_lights_fits(sub_dir)
    if existing:
        print(f"  ℹ️  lights/ already has {len(existing)} FITS — checking root for stragglers")

    root_fits = collect_root_fits(sub_dir)
    if not root_fits:
        print(f"  ✅ No FITS files at root — already organised")
    else:
        for fits_file in sorted(root_fits):
            if is_processed(fits_file.name):
                print(f"  ⏭  SKIP    {fits_file.name}  (processed/stacked)")
                summary["skipped_fits"].append(fits_file.name)
            else:
                dest = lights_dir / fits_file.name
                if dest.exists():
                    print(f"  🗑  DELETE  {fits_file.name}  (already in lights/)")
                    if not dry_run:
                        fits_file.unlink()
                    summary["dupes_deleted"] += 1
                else:
                    print(f"  ➡️  MOVE    {fits_file.name} → lights/")
                    if not dry_run:
                        shutil.move(str(fits_file), str(dest))
                    summary["fits_moved"] += 1

    return summary


# ---------------------------------------------------------------------------
# Step 4 (--merge): consolidate multi-night sessions into primary folder
# ---------------------------------------------------------------------------

def group_by_target(folders: list[Path]) -> dict[str, list[Path]]:
    """
    Group folders by canonical target name.
    Returns {canonical_name: [sorted list of folder paths]}.
    Groups with only one folder are still included (nothing to merge).
    """
    groups: dict[str, list[Path]] = defaultdict(list)
    for f in folders:
        key = canonical_target_name(f.name)
        groups[key].append(f)
    # Sort each group so primary is consistently the "oldest" / shortest name
    for key in groups:
        groups[key].sort()
    return dict(sorted(groups.items()))


def merge_sessions(groups: dict[str, list[Path]], dry_run: bool) -> None:
    """
    For each target with multiple session folders, move all lights from
    secondary folders into the primary folder's lights/ dir.
    """
    multi = {k: v for k, v in groups.items() if len(v) > 1}

    if not multi:
        print("No targets with multiple session folders found — nothing to merge.\n")
        return

    print(f"Found {len(multi)} target(s) with multiple sessions:\n")

    for target, folders in multi.items():
        primary = folders[0]
        secondaries = folders[1:]

        print(f"{'─'*60}")
        print(f"🎯 TARGET: {target}")
        print(f"   PRIMARY  : {primary.name}")
        for s in secondaries:
            print(f"   SECONDARY: {s.name}")
        print()

        primary_lights = primary / "lights"
        if not primary_lights.exists():
            print(f"  📁 CREATE  {primary.name}/lights/")
            if not dry_run:
                primary_lights.mkdir(parents=True, exist_ok=True)

        total_moved = 0
        for donor in secondaries:
            donor_lights = donor / "lights"
            if not donor_lights.exists():
                print(f"  ⚠️  {donor.name}/lights/ does not exist — run cleanup first")
                continue

            donor_files = [
                f for f in donor_lights.iterdir()
                if f.is_file() and f.suffix in FITS_EXTENSIONS
            ]

            if not donor_files:
                print(f"  ✅ {donor.name}/lights/ is empty — nothing to merge")
                continue

            print(f"  📦 Merging {len(donor_files)} subs from {donor.name}/lights/ → {primary.name}/lights/")

            for fits_file in sorted(donor_files):
                dest = safe_dest(primary_lights / fits_file.name)
                label = f"{fits_file.name}" + (f" → {dest.name}" if dest.name != fits_file.name else "")
                print(f"    ➡️  {label}")
                if not dry_run:
                    shutil.move(str(fits_file), str(dest))
                total_moved += 1

        if total_moved:
            print(f"\n  ✅ Merged {total_moved} subs into {primary.name}/lights/")
            print(f"  ℹ️  Secondary folders left in place — verify then delete manually:")
            for s in secondaries:
                print(f"       rm -rf \"{s}\"")
        print()


# ---------------------------------------------------------------------------
# Step 5 (--remove-empty): delete _sub/_subs folders with no FITS inside
# ---------------------------------------------------------------------------

def has_any_fits(folder: Path) -> bool:
    """Return True if any FITS file exists anywhere inside folder."""
    for ext in FITS_EXTENSIONS:
        if any(folder.rglob(f"*{ext}")):
            return True
    return False


def remove_empty_folders(folders: list[Path], dry_run: bool) -> None:
    empty = [f for f in folders if not has_any_fits(f)]

    if not empty:
        print("No empty _sub/_subs folders found.\n")
        return

    print(f"Found {len(empty)} empty folder(s) to remove:\n")
    for folder in empty:
        print(f"  🗑  REMOVE  {folder.name}")
        if not dry_run:
            shutil.rmtree(folder)
    print()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    if "-h" in sys.argv or "--help" in sys.argv:
        print(__doc__)
        sys.exit(0)

    args  = [a for a in sys.argv[1:] if not a.startswith("-")]
    flags = [a for a in sys.argv[1:] if a.startswith("-")]

    dry_run      = "--dry-run"      in flags
    do_merge     = "--merge"        in flags
    remove_empty = "--remove-empty" in flags

    root = Path(args[0] if args else ".").expanduser().resolve()
    if not root.exists():
        print(f"Error: path does not exist: {root}")
        sys.exit(1)

    if dry_run:
        print(f"\n{'='*60}")
        print("  DRY RUN — no files will be changed")
        print(f"{'='*60}\n")

    print(f"Searching for _sub/_subs folders under: {root}\n")
    sub_folders = find_sub_folders(root)

    if not sub_folders:
        print("No _sub/_subs folders found.")
        sys.exit(0)

    # ------------------------------------------------------------------
    # Phase 1: organize each folder (move Lights_*.fits → lights/)
    # ------------------------------------------------------------------
    print(f"Found {len(sub_folders)} folder(s). Organizing...\n")
    totals = {"jpgs": 0, "dupes": 0, "fits": 0, "dirs": 0, "warnings": 0}

    for sub_dir in sub_folders:
        print(f"{'─'*60}")
        print(f"📂 {sub_dir.name}")
        print(f"{'─'*60}")
        summary = clean_sub_folder(sub_dir, dry_run)
        totals["jpgs"]     += summary["jpgs_deleted"]
        totals["dupes"]    += summary["dupes_deleted"]
        totals["fits"]     += summary["fits_moved"]
        totals["dirs"]     += len(summary["dirs_created"])
        totals["warnings"] += len(summary["warnings"])
        for w in summary["warnings"]:
            print(f"  ⚠️  {w}")
        print()

    print(f"{'='*60}")
    print("ORGANIZE COMPLETE" + (" (dry run)" if dry_run else ""))
    print(f"{'='*60}")
    print(f"  JPGs deleted:      {totals['jpgs']}")
    print(f"  Dupes deleted:     {totals['dupes']}")
    print(f"  FITS moved:        {totals['fits']}")
    print(f"  Directories made:  {totals['dirs']}")
    if totals["warnings"]:
        print(f"  Warnings:          {totals['warnings']}  (see above)")
    print()

    # ------------------------------------------------------------------
    # Phase 2 (optional): merge multi-night sessions
    # Run before remove-empty so donor folders aren't deleted before merge
    # ------------------------------------------------------------------
    if do_merge:
        sub_folders = find_sub_folders(root)
        print(f"\n{'='*60}")
        print("MERGE PHASE — consolidating multi-night sessions")
        print(f"{'='*60}\n")
        groups = group_by_target(sub_folders)
        merge_sessions(groups, dry_run)
        print(f"{'='*60}")
        print("MERGE COMPLETE" + (" (dry run)" if dry_run else ""))
        print(f"{'='*60}\n")

    # ------------------------------------------------------------------
    # Phase 3 (optional): remove empty folders
    # Runs after merge so donor folders emptied by merge are also caught
    # ------------------------------------------------------------------
    if remove_empty:
        sub_folders = find_sub_folders(root)
        print(f"\n{'='*60}")
        print("REMOVE EMPTY FOLDERS")
        print(f"{'='*60}\n")
        remove_empty_folders(sub_folders, dry_run)
        print(f"{'='*60}")
        print("REMOVE EMPTY COMPLETE" + (" (dry run)" if dry_run else ""))
        print(f"{'='*60}\n")


if __name__ == "__main__":
    main()
