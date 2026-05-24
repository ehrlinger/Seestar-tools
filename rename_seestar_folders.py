#!/usr/bin/env python3
"""
rename_seestar_folders.py

Removes spaces from Seestar _sub/_subs folder names.
Assumes you are running from the NAS Seestar archive — path defaults to '.'.

  "M 101_sub"        → "M101_sub"
  "NGC 281_sub"      → "NGC281_sub"
  "IC 434_mosaic_sub"→ "IC434_mosaic_sub"
  "M 81 mosaic_sub"  → "M81_mosaic_sub"   (interior spaces → underscores)

Usage:
    python3 rename_seestar_folders.py             # live run in current dir
    python3 rename_seestar_folders.py /path       # explicit path
    python3 rename_seestar_folders.py --dry-run   # preview
    python3 rename_seestar_folders.py -h          # this help
"""

import sys
from pathlib import Path


def find_sub_folders(root: Path) -> list[Path]:
    return sorted(
        p for p in root.rglob("*")
        if p.is_dir() and (p.name.endswith("_sub") or p.name.endswith("_subs"))
    )


def new_name(folder_name: str) -> str:
    """Return the renamed folder name (spaces stripped/replaced)."""
    return folder_name.strip().replace(" ", "_")


def main():
    if "-h" in sys.argv or "--help" in sys.argv:
        print(__doc__)
        sys.exit(0)

    args  = [a for a in sys.argv[1:] if not a.startswith("-")]
    flags = [a for a in sys.argv[1:] if a.startswith("-")]
    dry_run = "--dry-run" in flags

    root = Path(args[0] if args else ".").expanduser().resolve()
    if not root.exists():
        print(f"Error: path does not exist: {root}")
        sys.exit(1)

    if dry_run:
        print(f"\n{'='*60}")
        print("  DRY RUN — no folders will be renamed")
        print(f"{'='*60}\n")

    folders = find_sub_folders(root)
    needs_rename = [f for f in folders if " " in f.name]

    if not needs_rename:
        print("✅ All _sub/_subs folders already have no spaces — nothing to do.")
        sys.exit(0)

    print(f"Found {len(needs_rename)} folder(s) with spaces to rename:\n")

    renamed = 0
    skipped = 0

    for folder in needs_rename:
        dest = folder.parent / new_name(folder.name)
        print(f"  {'RENAME' if not dry_run else 'WOULD RENAME'}")
        print(f"    FROM: {folder.name}")
        print(f"    TO:   {dest.name}")

        if dest.exists():
            print(f"    ⚠️  SKIPPED — destination already exists")
            skipped += 1
        else:
            if not dry_run:
                folder.rename(dest)
            renamed += 1
        print()

    print(f"{'='*60}")
    if dry_run:
        print(f"DRY RUN — would rename {renamed} folder(s)" +
              (f", skip {skipped}" if skipped else ""))
    else:
        print(f"DONE — renamed {renamed} folder(s)" +
              (f", skipped {skipped} (destination exists)" if skipped else ""))
    print(f"{'='*60}\n")


if __name__ == "__main__":
    main()
