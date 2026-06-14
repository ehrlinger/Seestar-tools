#!/usr/bin/env python3
"""
sort_by_exptime.py  —  Normalise a Seestar target to its canonical layout.

Subs are grouped by MOUNT MODE, not by raw exposure. A Seestar shoots ~10 s
in alt-az (kept short to beat field rotation) and 20 s+ in EQ; alt-az and EQ
frames must be stacked separately (different rotation / quality profile), but
different lengths WITHIN one mode (e.g. 20 s + 30 s EQ) belong in one deeper
stack. So:

    one mount mode   →  <target>_sub/lights/                       (flat)
    both modes        →  <target>_sub/altaz/lights/ + eq/lights/    (split)

Mode is inferred from exposure (< ~15 s ⇒ alt-az, ≥ ~15 s ⇒ EQ — see
MODE_BOUNDARY_SECONDS). A single-mode target stays flat so a hand-run of Siril
finds the subs directly; only a target spanning both modes is split.

The script reads each target as a whole and CONVERGES it to the canonical shape
from any starting state — flat, mode-split, or the legacy per-exposure
(10s/, 20s/, 30s/) layout — and is a no-op once canonical. Mode comes from the
folder name where it already encodes it (altaz/, eq/, or a legacy <exp>s/ label),
or from the FITS header for frames loose in a flat lights/ (no header read for
already-grouped frames).

USAGE
-----
  # Always dry-run first:
  python3 sort_by_exptime.py --dry-run M_51_sub/

  # Normalise a single target folder:
  python3 sort_by_exptime.py M_51_sub/

  # Normalise every *_sub folder under a root:
  python3 sort_by_exptime.py --all /Volumes/NAS/Seestar/

  # Dry-run everything:
  python3 sort_by_exptime.py --dry-run --all /Volumes/NAS/Seestar/

  # Pass multiple explicit folders:
  python3 sort_by_exptime.py M_51_sub/ M_27_sub/ M_57_sub/

AFTER NORMALISING
-----------------
  A single-mode target stays flat:   M_57_sub/lights/         (e.g. 20 s+30 s EQ)
  A both-modes target is split:      M_63_sub/altaz/lights/   (10 s alt-az)
                                     M_63_sub/eq/lights/      (20 s + 30 s EQ)

  batch_stack.py finds and stacks every shape automatically — point it at the
  archive root, a <target>_sub, or an individual altaz/ or eq/ folder:

    python3 batch_stack.py /Volumes/NAS/Seestar/

  Running Siril by hand instead? Seestar_Preprocessing.ssf opens with
  `cd lights`, so set the working directory to whatever folder directly holds
  lights/: the <target>_sub root for a single-mode target, or the altaz/ or eq/
  folder for a both-modes one.

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


# ── exptime-label pattern (matches legacy per-exposure folders "10s", "20.5s") ─

EXPTIME_DIR_RE = re.compile(r'^\d+(\.\d+)?s$')

# ── mount mode ─────────────────────────────────────────────────────────────────
# A Seestar shoots ~10 s subs in alt-az (kept short to beat field rotation) and
# 20 s+ in EQ. Mount mode — not raw exposure — is what should be stacked
# separately: alt-az frames carry field rotation and a different quality profile,
# while EQ frames of different lengths (20 s, 30 s) are the same tracked mode and
# combine into one deeper integration. Exposures below this boundary are treated
# as alt-az, at/above as EQ. Adjust here if your rig uses different lengths.
MODE_BOUNDARY_SECONDS = 15.0
MODE_DIR_NAMES = ("altaz", "eq")


def mount_mode(exptime: float) -> str:
    """Map an exposure (seconds) to its Seestar mount mode: 'altaz' or 'eq'."""
    return "altaz" if exptime < MODE_BOUNDARY_SECONDS else "eq"


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


def plan_layout(
    modes: dict, target_sub: pathlib.Path
) -> dict:
    """
    Decide where each light frame belongs and return ``{src: dest}`` for the
    files that must move to reach the canonical shape, keyed on MOUNT MODE:

      • one mount mode   →  FLAT:  ``<target>_sub/lights/<name>``
                            (even if lengths differ — 20 s + 30 s EQ combine)
      • both modes        →  SPLIT: ``<target>_sub/<mode>/lights/<name>``
                            (``altaz/`` and ``eq/`` stacked separately)

    *modes* maps each light file to its mount-mode label (``"altaz"``/``"eq"``)
    or ``None`` when the exposure couldn't be read. "How many modes" counts only
    the *known* labels: a lone unknown frame never forces a split, and in the
    flat case it lands in ``lights/`` like everything else. In the split case an
    unknown frame can't be placed, so it is left where it is (the caller warns).
    Files already at their destination are omitted, so re-running is a no-op.
    Pure function — no filesystem access.
    """
    known_modes = {m for m in modes.values() if m is not None}
    flat = len(known_modes) <= 1

    moves: dict = {}
    for src, mode in modes.items():
        if flat:
            dest = target_sub / "lights" / src.name
        else:
            if mode is None:
                continue  # unknown mode — can't file it into altaz/ or eq/
            dest = target_sub / mode / "lights" / src.name
        if src != dest:
            moves[src] = dest
    return moves


def find_target_dirs(root: pathlib.Path) -> list[pathlib.Path]:
    """Top-level *_sub / *_subs directories under root (used by --all)."""
    return sorted(
        d for d in root.iterdir()
        if d.is_dir() and (d.name.endswith("_sub") or d.name.endswith("_subs"))
    )


def _exp_label_seconds(label: str) -> float:
    """'20s' → 20.0, '20.5s' → 20.5. Assumes label already matched EXPTIME_DIR_RE."""
    return float(label[:-1])


def gather_lights(target_sub: pathlib.Path) -> dict:
    """
    Collect every raw light frame under *target_sub*, mapped to its mount mode
    (``"altaz"``/``"eq"``) or ``None`` when the exposure couldn't be read. Looks
    only at light-bearing folders — the flat ``lights/``, a canonical mode folder
    ``altaz/lights/`` or ``eq/lights/``, and a legacy per-exposure ``<exp>s/lights/``
    — so darks/, flats/, and Siril working dirs are never touched. Mode comes from
    the folder name where it already encodes it (``altaz/``/``eq/`` directly, an
    ``<exp>s/`` label via :func:`mount_mode`); frames loose in the flat ``lights/``
    have their exposure read from the FITS header and mapped to a mode.
    """
    modes: dict = {}

    flat = target_sub / "lights"
    if flat.is_dir():
        for f in flat.iterdir():
            if f.is_file() and is_fits(f):
                exp = read_exptime(f)
                modes[f] = mount_mode(exp) if exp is not None else None

    for d in sorted(target_sub.iterdir()):
        if not d.is_dir():
            continue
        nested = d / "lights"
        if not nested.is_dir():
            continue
        if d.name in MODE_DIR_NAMES:
            label = d.name                              # already a mode folder
        elif EXPTIME_DIR_RE.match(d.name):
            label = mount_mode(_exp_label_seconds(d.name))  # legacy <exp>s/
        else:
            continue
        for f in nested.iterdir():
            if f.is_file() and is_fits(f):
                modes[f] = label

    return modes


def _plan_dir_removals(
    target_sub: pathlib.Path, entries: dict, moves: dict
) -> list:
    """
    Given the gathered lights and the planned moves, return the ``lights/``
    scaffold directories that will be empty *after* the moves are applied —
    deepest-first (``<group>/lights/`` before its ``<group>/`` parent, top-level
    ``lights/`` last). A "group" folder is a canonical mode folder (``altaz/``,
    ``eq/``) or a legacy per-exposure ``<exp>s/`` left over from the old layout.
    Computed from the post-move file layout (not the current one), so a dry-run
    reports exactly what a real run removes: a folder that *receives* frames is
    never listed, only emptied ones. Never lists darks/ or flats/.
    """
    # Where each gathered frame ends up (moved or staying put), counted per dir.
    files_after: dict = {}
    for src in entries:
        dest_dir = moves.get(src, src).parent
        files_after[dest_dir] = files_after.get(dest_dir, 0) + 1

    removals: list = []
    for d in sorted(target_sub.iterdir()):
        if d.is_dir() and (d.name in MODE_DIR_NAMES or EXPTIME_DIR_RE.match(d.name)):
            nested = d / "lights"
            if nested.is_dir() and files_after.get(nested, 0) == 0:
                removals.append(nested)   # emptied — remove before its parent
                removals.append(d)        # group/ then holds only the empty lights/
    top = target_sub / "lights"
    if top.is_dir() and files_after.get(top, 0) == 0:
        removals.append(top)
    return removals


def normalize_target(target_sub: pathlib.Path, dry_run: bool) -> dict:
    """
    Converge *target_sub* to the canonical on-disk shape and return a summary
    dict ``{"moved": int, "skipped": int, "unreadable": int}``:

      • one mount mode   →  FLAT:  ``<target>_sub/lights/``
      • both modes        →  SPLIT: ``<target>_sub/altaz/lights/`` + ``…/eq/lights/``

    Single-mode targets stay flat so a hand-run Siril (whose
    ``Seestar_Preprocessing.ssf`` opens with ``cd lights``) finds the subs;
    different exposure lengths within one mode (20 s + 30 s EQ) combine into one
    deeper stack. Only a target spanning both mount modes is split, because alt-az
    and EQ frames must be stacked separately. Converges from ANY starting state —
    flat, mode-split, or the legacy per-exposure layout — so re-running is a no-op
    once canonical (idempotent). A destination collision is never overwritten:
    that frame is left in place.
    """
    modes = gather_lights(target_sub)
    moves = plan_layout(modes, target_sub)
    unreadable = [f for f, m in modes.items() if m is None]

    if modes:
        known = sorted({m for m in modes.values() if m is not None})
        if len(known) <= 1:
            shape = f"flat ({known[0] if known else 'unknown'})"
        else:
            shape = f"split ({' + '.join(known)})"
        tag = "[dry-run] " if dry_run else ""
        print(f"  {target_sub.name}: {len(modes)} light(s) → {shape}; {tag}{len(moves)} to move")

    # Which empty lights/ scaffolds the moves will leave behind — computed from
    # the post-move layout so the dry-run report matches a real run exactly.
    removals = _plan_dir_removals(target_sub, modes, moves)

    moved = skipped = 0
    for src, dest in sorted(moves.items()):
        if not dry_run:
            if dest.exists():
                skipped += 1
                continue
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(src), str(dest))
        moved += 1

    if unreadable:
        print(f"    ⚠  {len(unreadable)} unreadable file(s) left in place:")
        for p in unreadable[:5]:
            print(f"       {p.name}")
        if len(unreadable) > 5:
            print(f"       … and {len(unreadable) - 5} more")

    verb = "would remove" if dry_run else "removed"
    for d in removals:
        # .as_posix() so the report reads the same on Windows (\) as elsewhere.
        rel = d.relative_to(target_sub).as_posix()
        try:
            if dry_run:
                print(f"    {verb} empty {rel}/")
            elif d.is_dir() and not any(d.iterdir()):
                d.rmdir()
                print(f"    {verb} empty {rel}/")
        except OSError:
            pass

    return {"moved": moved, "skipped": skipped, "unreadable": len(unreadable)}


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
        targets = find_target_dirs(root)
        if not targets:
            sys.exit(f"No *_sub / *_subs directories found under {root}")
        print(f"Found {len(targets)} target folder(s) under {root}\n")

    for p in args.paths:
        t = p.expanduser().resolve()
        # Tolerate being handed a group subfolder (altaz/, eq/, or a legacy
        # <exp>s/): normalize its parent target instead.
        if t.name in MODE_DIR_NAMES or EXPTIME_DIR_RE.match(t.name):
            t = t.parent
        targets.append(t)

    mode = "DRY RUN — no files will be moved" if args.dry_run else "MOVING FILES"
    print(f"sort_by_exptime  [{mode}]")
    print("─" * 60)

    for target in targets:
        if not target.is_dir():
            print(f"  SKIP (not a directory): {target}")
            continue
        # Converge the whole target to canonical shape: flat lights/ for a single
        # exposure, <exp>s/lights/ only when 2+ exposures are present.
        normalize_target(target, dry_run=args.dry_run)

    print("─" * 60)
    if args.dry_run:
        print("Dry run complete.  Re-run without --dry-run to move files.")
    else:
        print("Done.")


if __name__ == "__main__":
    main()
