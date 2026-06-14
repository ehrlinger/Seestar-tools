#!/usr/bin/env python3
"""
rename_seestar_folders.py

Removes spaces from Seestar _sub/_subs folder names.
Assumes you are running from the NAS Seestar archive — path defaults to '.'.

  "M 101_sub"        → "M101_sub"
  "NGC 281_sub"      → "NGC281_sub"
  "IC 434_mosaic_sub"→ "IC434_mosaic_sub"
  "M 81 mosaic_sub"  → "M81_mosaic_sub"   (interior spaces → underscores)

When the canonical destination already exists (e.g. an incremental sync drops
a fresh "M 51_sub" alongside an existing "M_51_sub"), the donor folder is
MERGED into the existing one instead of being skipped: its files are moved into
the matching location under the destination and deduped by filename, then the
emptied donor is removed. The later organize → exposure-sort steps then file
the new subs into <target>_sub/<exptime>s/lights/. This keeps incremental subs
from getting stranded in space-named folders. Dry-run first.

Usage:
    python3 rename_seestar_folders.py             # live run in current dir
    python3 rename_seestar_folders.py /path       # explicit path
    python3 rename_seestar_folders.py --dry-run   # preview
    python3 rename_seestar_folders.py -h          # this help
"""

import shutil
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


def _safe_dest(dest: Path) -> Path:
    """Return a non-clobbering path, appending _dupN before the suffix if needed.
    Mirrors organize_subs.safe_dest so a genuine collision is preserved, never
    overwritten."""
    if not dest.exists():
        return dest
    n = 1
    while True:
        candidate = dest.with_name(f"{dest.stem}_dup{n}{dest.suffix}")
        if not candidate.exists():
            return candidate
        n += 1


def merge_into_existing(donor: Path, dest: Path, dry_run: bool) -> dict:
    """
    Fold a space-named *donor* folder into an already-canonical *dest* folder.

    Used when renaming "M 51_sub" → "M_51_sub" but "M_51_sub" already exists.
    Rather than skipping (which strands new subs in a space-named folder that
    later steps never consolidate), move every file from the donor into the
    matching relative location under dest, then remove the emptied donor.

    Dedupe is by filename AND size: a donor file is dropped only when a file of
    the same basename *and* identical size already exists anywhere under dest
    (e.g. the same sub was sorted into 20s/lights/ on a previous run) — Seestar
    filenames embed a second-resolution timestamp, so a name+size match is the
    same frame re-delivered. A same-name-but-different-size file is NOT a
    duplicate: it is moved under a collision-safe _dupN name instead of being
    unlinked, so no real data is ever silently dropped. This keeps the canonical
    sorted subtree untouched and makes re-runs idempotent.

    Raw subs left at the donor root land at the dest root, where the subsequent
    organize → exposure-sort steps file them into <exp>s/lights/.

    Returns a summary dict: {"moved": int, "deduped": int, "donor_removed": bool}.
    """
    # Map basename -> set of sizes already archived under dest (the dedupe key).
    existing: dict[str, set[int]] = {}
    for p in dest.rglob("*"):
        if p.is_file():
            try:
                existing.setdefault(p.name, set()).add(p.stat().st_size)
            except OSError:
                continue

    moved = 0
    deduped = 0
    donor_files = sorted(p for p in donor.rglob("*") if p.is_file())

    for src in donor_files:
        rel = src.relative_to(donor)
        try:
            size = src.stat().st_size
        except OSError:
            size = -1

        if size in existing.get(src.name, set()):
            print(f"    🗑  DEDUPE  {rel}  (already in {dest.name}/)")
            if not dry_run:
                src.unlink()
            deduped += 1
        else:
            target = _safe_dest(dest / rel)
            label = rel if target.name == src.name else f"{rel} → {target.name}"
            print(f"    ➡️  MERGE   {label} → {dest.name}/")
            if not dry_run:
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(src), str(target))
            # Reserve this name+size so identical later donor files dedupe too.
            existing.setdefault(src.name, set()).add(size)
            moved += 1

    donor_removed = False
    if not dry_run:
        # Every file was moved or deleted; only empty dirs remain.
        shutil.rmtree(donor)
        donor_removed = True

    return {"moved": moved, "deduped": deduped, "donor_removed": donor_removed}


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
    merged = 0
    subs_moved = 0
    subs_deduped = 0

    for folder in needs_rename:
        dest = folder.parent / new_name(folder.name)

        if dest.exists():
            # Canonical folder already exists — fold the donor in instead of
            # stranding its new subs in a space-named folder (see the bug in
            # incremental syncs). organize → sort then file everything.
            verb = "WOULD MERGE" if dry_run else "MERGE"
            print(f"  {verb} (destination exists)")
            print(f"    FROM: {folder.name}")
            print(f"    INTO: {dest.name}")
            res = merge_into_existing(folder, dest, dry_run)
            subs_moved += res["moved"]
            subs_deduped += res["deduped"]
            merged += 1
        else:
            print(f"  {'WOULD RENAME' if dry_run else 'RENAME'}")
            print(f"    FROM: {folder.name}")
            print(f"    TO:   {dest.name}")
            if not dry_run:
                folder.rename(dest)
            renamed += 1
        print()

    print(f"{'='*60}")
    verb = "DRY RUN — would" if dry_run else "DONE —"
    summary = f"{verb} rename {renamed} folder(s)"
    if merged:
        summary += (f", merge {merged} into existing "
                    f"({subs_moved} subs moved, {subs_deduped} deduped)")
    print(summary)
    print(f"{'='*60}\n")


if __name__ == "__main__":
    main()
