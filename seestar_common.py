#!/usr/bin/env python3
"""
seestar_common.py — tiny shared helpers for the Seestar tools.

Kept deliberately dependency-free so every script can `import seestar_common`
when run standalone (the script's own directory is on sys.path).
"""

from pathlib import Path

# Directory names the pipeline must never descend into when discovering targets.
#   _trash   — folders you've set aside / deleted; not live data
#   scripts  — copies of these tools kept beside the archive
EXCLUDED_DIR_NAMES = frozenset({"_trash", "scripts"})


def is_in_excluded(path: Path, root: Path) -> bool:
    """
    True if *path* lies under (or is) an excluded directory relative to *root*,
    e.g. ``<root>/_trash/strays/M 51_sub`` or ``<root>/scripts/...``.

    Pure function — no filesystem access. Paths not under root are not excluded.
    """
    try:
        rel = path.relative_to(root)
    except ValueError:
        return False
    return bool(EXCLUDED_DIR_NAMES.intersection(rel.parts))
