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
