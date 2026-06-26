#!/usr/bin/env python3
"""
prune_seestar.py  —  Delete Seestar EMMC subs already archived on the NAS.

The inverse-safety companion to sync_seestar.sh. It deletes FITS subs *from the
device*, so it errs hard toward caution: a sub is removed only when an identical
copy (same filename AND byte size) is confirmed under its archived target folder
on the NAS. Each deleted sub's per-sub JPG preview (and *_thn.jpg thumbnail) is
removed alongside it — those JPGs are never on the NAS (the sync excludes them),
so they ride along with the data they preview, not a NAS match.

Matching is name+size, not same-path, because the sync pipeline reorganizes
what comes off the scope:
    "M 51_sub"  (EMMC, spaces, subs at folder root)
        -> "M_51_sub/20s/lights/Light_*.fit"  (NAS, renamed + sorted)
The EMMC folder maps to the NAS folder via rename_seestar_folders.new_name();
the sub is matched by name+size anywhere under that NAS subtree.

USAGE
-----
    # Dry-run is the DEFAULT — preview only, nothing deleted:
    python3 prune_seestar.py

    # Actually delete (asks for confirmation first):
    python3 prune_seestar.py --execute

    # Limit to specific target folder(s) on the EMMC:
    python3 prune_seestar.py --execute "M 51_sub" "NGC 7000_sub"

    # Override paths (else from seestar.conf):
    python3 prune_seestar.py --emmc "/Volumes/EMMC Images/MyWorks/" \\
                             --nas  "/Volumes/personal_folder/Seestar/"

    # Skip the confirmation prompt (scripted runs):
    python3 prune_seestar.py --execute --yes

NOTES
-----
    • Dry-run by default; --execute required to delete anything.
    • Never touches the NAS (read-only there).
    • Per-sub JPG removed only when its sibling .fit is removed.
    • Empty target folders are pruned; a kept sub or stray JPG blocks pruning.
    • macOS ._ / .DS_Store files are ignored throughout.
"""

import argparse
import errno
import sys
from pathlib import Path

from rename_seestar_folders import new_name
from seestar_common import is_in_excluded

# ── constants ───────────────────────────────────────────────────────────────
FITS_SUFFIXES = {".fit", ".fits"}
JPG_SUFFIXES = {".jpg", ".jpeg"}
SKIP_PREFIXES = ("._", ".DS_Store")     # macOS resource-fork noise

DEFAULT_CONF_KEYS = {
    "SEESTAR_EMMC": "/Volumes/EMMC Images/MyWorks/",
    "SEESTAR_NAS":  "/Volumes/personal_folder/Seestar/",
}


def load_conf() -> dict:
    """Parse KEY="value" lines from seestar.conf next to this script (if present)."""
    conf = dict(DEFAULT_CONF_KEYS)
    conf_path = Path(__file__).resolve().parent / "seestar.conf"
    if conf_path.exists():
        for line in conf_path.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, val = line.partition("=")
            conf[key.strip()] = val.strip().strip('"').strip("'")
    return conf


def is_fits(path: Path) -> bool:
    """True for a .fit/.fits file that is not macOS resource-fork noise."""
    return (path.suffix.lower() in FITS_SUFFIXES
            and not path.name.startswith(SKIP_PREFIXES))


def index_nas_target(nas_target_dir: Path) -> dict[str, set[int]]:
    """Map FITS basename -> set of byte sizes seen anywhere under the target.

    Collapses the archive's lights/ and <exp>s/lights/ reorg so an EMMC sub at
    the folder root can be matched wherever its NAS copy was filed. Same key as
    rename_seestar_folders.merge_into_existing's dedupe.
    """
    index: dict[str, set[int]] = {}
    for p in nas_target_dir.rglob("*"):
        if p.is_file() and is_fits(p):
            try:
                index.setdefault(p.name, set()).add(p.stat().st_size)
            except OSError:
                continue
    return index


def find_emmc_targets(emmc_root: Path) -> list[Path]:
    """Top-level _sub/_subs folders on the EMMC, excluding _trash/scripts.

    The EMMC holds targets in native layout directly under MyWorks, so only the
    immediate children are scanned (the device is never reorganized).
    """
    out = []
    for p in sorted(emmc_root.iterdir()):
        if (p.is_dir()
                and (p.name.endswith("_sub") or p.name.endswith("_subs"))
                and not is_in_excluded(p, emmc_root)):
            out.append(p)
    return out


def eligible_subs(emmc_target: Path,
                  index: dict[str, set[int]]) -> tuple[list[Path], list[Path]]:
    """Split the target's root FITS subs into (to_delete, kept_unmatched).

    A sub is deletable iff a file of the same name AND identical byte size is
    present in *index* (built from the NAS target subtree). Only files directly
    in the target root are considered — that is native Seestar layout. Returns
    sorted lists; pure given the index (apart from reading each sub's size).
    """
    to_delete: list[Path] = []
    kept: list[Path] = []
    for p in sorted(emmc_target.iterdir()):
        if not (p.is_file() and is_fits(p)):
            continue
        try:
            size = p.stat().st_size
        except OSError:
            size = -1
        if size in index.get(p.name, set()):
            to_delete.append(p)
        else:
            kept.append(p)
    return to_delete, kept


def sibling_jpgs(sub_path: Path) -> list[Path]:
    """JPG preview + thumbnail belonging to a sub of stem S, in the same dir.

    Matches S.jpg / S.jpeg (full preview) and S_thn.jpg / S_thn.jpeg (Seestar
    thumbnail), case-insensitively, by exact stem so a preview is only ever
    paired with the specific sub it belongs to. Returns existing files only.
    """
    stem = sub_path.stem            # "Light_a.fit" -> "Light_a"
    wanted_stems = {stem, f"{stem}_thn"}
    out = []
    for p in sub_path.parent.iterdir():
        if (p.is_file()
                and p.suffix.lower() in JPG_SUFFIXES
                and p.stem in wanted_stems):
            out.append(p)
    return sorted(out)


def is_effectively_empty(directory: Path) -> bool:
    """True if the dir holds no real files, ignoring only .DS_Store / ._* noise.

    A kept sub or a stray/orphan JPG is a real file and makes this False, so
    pruning never strands data.
    """
    for p in directory.rglob("*"):
        if p.is_file() and not p.name.startswith(SKIP_PREFIXES):
            return False
    return True


def prune_empty_dir(directory: Path, dry_run: bool) -> bool:
    """Remove *directory* if it is effectively empty. Returns True if removed.

    Clears .DS_Store / ._* noise first, then rmdir. Swallows the EBUSY /
    ENOTEMPTY / EPERM / EACCES errors a network share raises when it still holds
    a handle (macOS SMB leaves .smbdelete* tombstones) — mirrors
    rename_seestar_folders._safe_rmtree_donor so a locked handle reports rather
    than aborting the whole run. No-op in dry-run.
    """
    if not is_effectively_empty(directory):
        return False
    if dry_run:
        return False

    # Clear macOS noise so the directory can rmdir cleanly.
    for p in list(directory.rglob("*")):
        if p.is_file() and p.name.startswith(SKIP_PREFIXES):
            try:
                p.unlink()
            except OSError:
                pass
    # Remove any now-empty noise subdirs, deepest first.
    for p in sorted((d for d in directory.rglob("*") if d.is_dir()),
                    key=lambda d: len(d.parts), reverse=True):
        try:
            p.rmdir()
        except OSError:
            pass

    try:
        directory.rmdir()
    except OSError as e:
        if e.errno in (errno.EBUSY, errno.ENOTEMPTY, errno.EPERM, errno.EACCES):
            leftovers = list(directory.iterdir()) if directory.exists() else []
            ghosts = [p for p in leftovers if p.name.startswith(".smbdelete")]
            if leftovers and len(ghosts) == len(leftovers):
                print(f"    ⚠️  {directory.name}: left {len(ghosts)} SMB "
                      f"tombstone(s) (.smbdelete*) locked by the share. After a "
                      f"NAS remount: rm -rf '{directory}'")
            else:
                print(f"    ⚠️  could not remove {directory.name}: {e}")
            return False
        raise
    return True


def prune(emmc: Path, nas: Path, targets: list[str] | None,
          dry_run: bool) -> dict:
    """Delete EMMC subs (and their JPG previews) confirmed on the NAS.

    For each EMMC target: map its name to the NAS via new_name(); if that NAS
    folder is missing, skip the whole target. Otherwise index the NAS subtree
    and delete each root sub whose name+size matches, plus that sub's sibling
    JPG preview/thumbnail. Prune a target folder once it holds no real files.

    Returns a summary dict with totals. Counts reflect what was (or, in dry-run,
    *would be*) deleted.
    """
    summary = {
        "subs_deleted": 0, "jpgs_deleted": 0, "subs_kept": 0,
        "bytes_freed": 0, "targets_skipped": 0, "dirs_pruned": 0,
    }

    emmc_targets = find_emmc_targets(emmc)
    if targets:
        wanted = set(targets)
        emmc_targets = [t for t in emmc_targets if t.name in wanted]

    verb = "WOULD DELETE" if dry_run else "DELETE"
    for target in emmc_targets:
        canonical = new_name(target.name)
        nas_target = nas / canonical
        print(f"\n── {target.name}  →  NAS/{canonical}")

        if not nas_target.is_dir():
            print(f"   ⏭  SKIP — not on NAS (no {canonical}/); nothing deletable.")
            summary["targets_skipped"] += 1
            continue

        index = index_nas_target(nas_target)
        to_delete, kept = eligible_subs(target, index)

        for sub in to_delete:
            jpgs = sibling_jpgs(sub)
            for f in [sub, *jpgs]:
                try:
                    summary["bytes_freed"] += f.stat().st_size
                except OSError:
                    pass
            print(f"   🗑  {verb}  {sub.name}"
                  + (f"  (+{len(jpgs)} jpg)" if jpgs else ""))
            if not dry_run:
                for f in [sub, *jpgs]:
                    try:
                        f.unlink()
                    except OSError as e:
                        print(f"      ⚠️  could not delete {f.name}: {e}")
            summary["subs_deleted"] += 1
            summary["jpgs_deleted"] += len(jpgs)

        for sub in kept:
            print(f"   ⏸  KEEP  {sub.name} — not confirmed on NAS")
        summary["subs_kept"] += len(kept)

        if prune_empty_dir(target, dry_run):
            print(f"   📁  pruned empty folder {target.name}")
            summary["dirs_pruned"] += 1

    return summary


def _print_summary(summary: dict, dry_run: bool) -> None:
    mb = summary["bytes_freed"] / (1024 * 1024)
    print("\n" + "=" * 50)
    head = "DRY RUN — would delete" if dry_run else "DONE — deleted"
    print(f"  {head}: {summary['subs_deleted']} subs, "
          f"{summary['jpgs_deleted']} jpgs ({mb:.1f} MB)")
    print(f"  kept (not on NAS): {summary['subs_kept']} subs")
    print(f"  targets skipped (no archive): {summary['targets_skipped']}")
    print(f"  folders pruned: {summary['dirs_pruned']}")
    print("=" * 50 + "\n")


def main() -> None:
    if "-h" in sys.argv or "--help" in sys.argv:
        print(__doc__)
        sys.exit(0)

    p = argparse.ArgumentParser(add_help=False)
    p.add_argument("targets", nargs="*",
                   help="EMMC folder name(s) to limit to; default: all _sub dirs")
    p.add_argument("--emmc", default=None, help="Seestar EMMC MyWorks root")
    p.add_argument("--nas", default=None, help="NAS Seestar archive root")
    p.add_argument("--execute", action="store_true",
                   help="actually delete (default is dry-run)")
    p.add_argument("--dry-run", action="store_true",
                   help="preview only (the default; wins over --execute)")
    p.add_argument("-y", "--yes", action="store_true",
                   help="skip the confirmation prompt")
    args = p.parse_args()

    # Dry-run is the default. An explicit --dry-run always wins, so
    # `--execute --dry-run` previews rather than deletes (fail safe).
    dry_run = args.dry_run or not args.execute

    conf = load_conf()
    emmc = Path((args.emmc or conf["SEESTAR_EMMC"])).expanduser()
    nas = Path((args.nas or conf["SEESTAR_NAS"])).expanduser()

    if not emmc.exists():
        print(f"❌  EMMC not found: {emmc}\n    Is the Seestar connected via USB?")
        sys.exit(1)
    if not nas.exists():
        print(f"❌  NAS archive not found: {nas}\n    Is the NAS mounted?")
        sys.exit(1)

    print("=" * 50)
    print("  prune_seestar — delete EMMC subs already on the NAS")
    print("=" * 50)
    print(f"  EMMC (delete from): {emmc}")
    print(f"  NAS  (verify on):   {nas}")
    if args.targets:
        print(f"  targets: {', '.join(args.targets)}")
    if dry_run:
        print("\n  DRY RUN — nothing will be deleted (pass --execute to delete)")
    print()

    if not dry_run and not args.yes:
        if not sys.stdin.isatty():
            print("❌  Refusing to delete unattended without confirmation.")
            print("    Re-run with --yes once you've checked the paths above,")
            print("    or omit --execute to preview.")
            sys.exit(1)
        reply = input("  Delete matched subs from the EMMC above? [y/N] ")
        if reply.strip().lower() not in ("y", "yes"):
            print("  Aborted — nothing was deleted.")
            sys.exit(0)

    summary = prune(emmc, nas, args.targets or None, dry_run)
    _print_summary(summary, dry_run)


if __name__ == "__main__":
    main()
