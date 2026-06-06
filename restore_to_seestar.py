#!/usr/bin/env python3
"""
restore_to_seestar.py  —  Put one archived target back onto the Seestar EMMC
                          in native layout so the app can re-process it.

This is the *inverse* of the archive pipeline (cleanup_seestar.py +
rename_seestar_folders.py + sort_by_exptime.py).  Those scripts, in order:

    "M 51_sub"           → "M_51_sub"          (spaces → underscores)
    M_51_sub/*.fit       → M_51_sub/lights/    (raw subs into lights/)
    M_51_sub/lights/     → M_51_sub/20s/lights (exposure-sorted, optional)

To re-stack / re-edit in the Seestar app you have to undo all three: native
folder name (with the space), raw subs back at the folder root, no lights/ or
exposure subdirs.  This script copies a target from the NAS archive to
SEESTAR_EMMC/<native name>/ with the subs flattened to the root.

    M_51_sub/**/lights/*.fit   →   /Volumes/EMMC Images/MyWorks/M 51_sub/*.fit

Raw subs are identified by living inside a  lights/  directory (where the
archive pipeline always puts them).  That cleanly skips the stacked master,
Siril working dirs (process/registered/stack), and calibration frames.

⚠️  IMPORTANT CAVEATS (read before relying on the app)
    • The Seestar app's stacking is primarily a *capture-time* function.
      Whether it will re-stack subs you copy back is firmware-dependent
      (some app versions expose a "Restack" option on a gallery item, some
      do not).  Native layout is the *precondition* either way — but it is
      not a guarantee the app will re-stack imported frames.
    • The archive rsync excludes *.jpg, so the Seestar's stacked master
      preview was never copied to the NAS.  AI Denoise + the in-app editor
      operate on that master, not on raw subs — if the original target was
      deleted from the device, that master is gone and the app's
      denoise/edit route is unavailable for it.  Siril + GraXpert standalone
      is the reliable fallback (see Processing Workflow.md, step 10).

USAGE
-----
    # Always dry-run first:
    python3 restore_to_seestar.py --dry-run M_51_sub

    # Restore one target (copies to the EMMC):
    python3 restore_to_seestar.py M_51_sub

    # Override the native folder name if the heuristic guesses wrong:
    python3 restore_to_seestar.py M_81_mosaic_sub --name "M 81 mosaic_sub"

    # Point at an explicit NAS path or EMMC mount:
    python3 restore_to_seestar.py M_57_sub \\
        --nas "/Volumes/personal_folder/Seestar/" \\
        --emmc "/Volumes/EMMC Images/MyWorks/"

NOTES
-----
    • Copies, never moves — your NAS archive is left untouched.
    • Won't overwrite: name clashes get a _dupN suffix (matches
      cleanup_seestar.py's safe_dest convention).
    • Paths default from  seestar.conf  (same file the other tools read).
    • Run from local disk, not a network share — macOS kills Python
      processes spawned from NFS/SMB volumes.
    • macOS ._  resource-fork files are skipped automatically.
"""

import argparse
import re
import shutil
import sys
from pathlib import Path

# ── constants ─────────────────────────────────────────────────────────────────

FITS_SUFFIXES = {".fit", ".fits"}
SKIP_PREFIXES = ("._", ".DS_Store")     # macOS resource-fork noise
LIGHTS_DIRNAME = "lights"

DEFAULT_CONF_KEYS = {
    "SEESTAR_EMMC": "/Volumes/EMMC Images/MyWorks/",
    "SEESTAR_NAS":  "/Volumes/personal_folder/Seestar/",
}

# ── config ────────────────────────────────────────────────────────────────────

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

# ── helpers ───────────────────────────────────────────────────────────────────

def is_fits(path: Path) -> bool:
    return path.suffix.lower() in FITS_SUFFIXES and not path.name.startswith(SKIP_PREFIXES)


def native_name(archived: str) -> str:
    """
    Reverse the rename step's space→underscore squash.

        "M_51_sub"          → "M 51_sub"
        "M_81_mosaic_sub"   → "M 81 mosaic_sub"
        "NGC_6946_sub"      → "NGC 6946_sub"

    Only the trailing _sub / _subs suffix is preserved as an underscore; every
    other underscore is assumed to have been a space.  Use --name to override
    when that assumption is wrong.
    """
    name = archived.rstrip("/")
    for suffix in ("_subs", "_sub"):
        if name.endswith(suffix):
            stem = name[: -len(suffix)]
            return stem.replace("_", " ") + suffix
    return name.replace("_", " ")


def safe_dest(dest: Path) -> Path:
    """Return a non-clobbering destination path (_dup1, _dup2, … on collision)."""
    if not dest.exists():
        return dest
    stem, suffix, parent = dest.stem, dest.suffix, dest.parent
    n = 1
    while True:
        candidate = parent / f"{stem}_dup{n}{suffix}"
        if not candidate.exists():
            return candidate
        n += 1


def collect_raw_subs(target_dir: Path) -> list[Path]:
    """Every FITS file living inside a  lights/  directory under target_dir."""
    out = []
    for f in target_dir.rglob("*"):
        if f.is_file() and is_fits(f) and f.parent.name.lower() == LIGHTS_DIRNAME:
            out.append(f)
    return sorted(out)

# ── core ──────────────────────────────────────────────────────────────────────

def restore(target_arg: str, nas: Path, emmc: Path, name_override: str | None,
            dry_run: bool) -> int:
    # Resolve the source folder on the NAS (accept a bare name or a path).
    src = Path(target_arg).expanduser()
    if not src.is_absolute() and not src.exists():
        src = nas / target_arg
    src = src.resolve() if src.exists() else (nas / Path(target_arg).name)

    if not src.is_dir():
        print(f"❌  Target not found on NAS: {src}")
        return 1

    nat = name_override or native_name(src.name)
    dst = emmc / nat

    subs = collect_raw_subs(src)
    if not subs:
        print(f"⚠️   No raw subs found under {src}/**/lights/ — nothing to restore.")
        print(f"     (Were they sorted somewhere else, or already flattened?)")
        return 1

    print(f"\n  SRC : {src}")
    print(f"  DST : {dst}")
    print(f"  Native name : {nat}")
    print(f"  Raw subs    : {len(subs)}")

    if dry_run:
        print("\n  DRY RUN — no files copied. First few would be:")
        for f in subs[:5]:
            print(f"    {f.name}  →  {dst.name}/{f.name}")
        if len(subs) > 5:
            print(f"    … and {len(subs) - 5} more")
        return 0

    dst.mkdir(parents=True, exist_ok=True)
    copied = 0
    for f in subs:
        out = safe_dest(dst / f.name)
        shutil.copy2(str(f), str(out))
        copied += 1
    print(f"\n✅  Copied {copied} subs → {dst}")
    print("    Disconnect cleanly, then open the app and check the gallery for this target.")
    return 0


def main() -> None:
    if "-h" in sys.argv or "--help" in sys.argv:
        print(__doc__)
        sys.exit(0)

    p = argparse.ArgumentParser(add_help=False)
    p.add_argument("targets", nargs="+", help="archived target folder name(s) or path(s)")
    p.add_argument("--name", default=None, help="override the native folder name (single target only)")
    p.add_argument("--nas", default=None, help="NAS Seestar archive root")
    p.add_argument("--emmc", default=None, help="Seestar EMMC MyWorks root")
    p.add_argument("--dry-run", action="store_true", help="preview only")
    args = p.parse_args()

    conf = load_conf()
    nas = Path((args.nas or conf["SEESTAR_NAS"])).expanduser()
    emmc = Path((args.emmc or conf["SEESTAR_EMMC"])).expanduser()

    if not emmc.exists():
        print(f"❌  EMMC not found: {emmc}\n    Is the Seestar connected via USB and mounted?")
        sys.exit(1)
    if not nas.exists():
        print(f"❌  NAS archive not found: {nas}\n    Is the NAS mounted?")
        sys.exit(1)

    if args.name and len(args.targets) > 1:
        print("❌  --name can only be used with a single target.")
        sys.exit(1)

    if args.dry_run:
        print("=" * 60)
        print("  DRY RUN — no files will be copied")
        print("=" * 60)

    rc = 0
    for t in args.targets:
        rc |= restore(t, nas, emmc, args.name, args.dry_run)
    sys.exit(rc)


if __name__ == "__main__":
    main()
