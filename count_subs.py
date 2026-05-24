#!/usr/bin/env python3
"""
count_subs.py

Counts subs in each _sub/_subs folder, reading exposure time from filenames.
Assumes you are running from the NAS Seestar archive — path defaults to '.'.

Usage:
    python3 count_subs.py                          # count all targets in current dir
    python3 count_subs.py /path/to/Seestar         # explicit path
    python3 count_subs.py "M 51"                   # single target filter
    python3 count_subs.py --csv                    # export CSV
    python3 count_subs.py --update-inventory       # sync counts → vault + local copy
    python3 count_subs.py --update-inventory --dry-run  # preview without writing
    python3 count_subs.py -h                       # this help

Inventory update writes to:
    1. ~/Library/Mobile Documents/iCloud~md~obsidian/.../AstroImages Inventory.md  (vault)
    2. ./AstroImages Inventory.md  (local copy in Seestar folder)
"""

import re
import sys
import csv
from datetime import date
from pathlib import Path
from collections import defaultdict

try:
    from astropy.io import fits as astropy_fits
    HAS_ASTROPY = True
except ImportError:
    HAS_ASTROPY = False

FITS_EXTENSIONS = {".fit", ".fits", ".FIT", ".FITS"}

# Exposure times to infer if FITS header is unreadable (seconds)
FALLBACK_EXPTIME = None   # None = report as "unknown"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def find_sub_folders(root: Path, filter_name: str = "") -> list[Path]:
    """Find all *_sub and *_subs directories, optionally filtered by name."""
    folders = sorted(
        p for p in root.rglob("*")
        if p.is_dir() and (p.name.endswith("_sub") or p.name.endswith("_subs"))
    )
    if filter_name:
        folders = [f for f in folders if filter_name.lower() in f.name.lower()]
    return folders


# Matches Seestar filename pattern: Light_M_63_10.0s_IRCUT_20260509-013344.fit
_EXPTIME_RE = re.compile(r"_(\d+(?:\.\d+)?)s_", re.IGNORECASE)


def get_exptime(fits_path: Path) -> float | None:
    """
    Extract exposure time (seconds) from the filename first — fast, no I/O.
    Falls back to reading the FITS header if the filename pattern doesn't match.
    """
    m = _EXPTIME_RE.search(fits_path.name)
    if m:
        return float(m.group(1))

    # Fallback: read FITS header
    if not HAS_ASTROPY:
        return FALLBACK_EXPTIME
    try:
        with astropy_fits.open(fits_path, memmap=True, ignore_missing_end=True) as hdul:
            header = hdul[0].header
            for key in ("EXPTIME", "EXPOSURE", "EXP_TIME"):
                if key in header:
                    return float(header[key])
    except Exception:
        pass
    return None


def collect_fits_files(sub_dir: Path) -> list[Path]:
    """
    Return all FITS files in a _sub/_subs folder:
      - First look in lights/ subdir (organised)
      - Also include any loose FITS at the root (not yet moved)
    Excludes processed/stacked files by name heuristic.
    """
    SKIP_PREFIXES = ("starless_", "starmask_", "stack_", "r_pp_", "result")
    SKIP_SUBSTRINGS = ("_processed.", "_GraXpert.", "_pp.")

    def is_raw(f: Path) -> bool:
        n = f.name
        if any(n.startswith(p) for p in SKIP_PREFIXES):
            return False
        if any(s in n for s in SKIP_SUBSTRINGS):
            return False
        # Stacked filename pattern: target_NNNxNNsec_...
        if re.search(r"\d+x\d+sec", n):
            return False
        return True

    files: list[Path] = []

    # lights/ subdir
    lights = sub_dir / "lights"
    if lights.exists():
        files += [f for f in lights.iterdir()
                  if f.is_file() and f.suffix in FITS_EXTENSIONS and is_raw(f)]

    # loose files at root
    files += [f for f in sub_dir.iterdir()
              if f.is_file() and f.suffix in FITS_EXTENSIONS and is_raw(f)]

    return files


def format_duration(total_seconds: float) -> str:
    """Format seconds as h m s string."""
    h = int(total_seconds // 3600)
    m = int((total_seconds % 3600) // 60)
    s = int(total_seconds % 60)
    parts = []
    if h:
        parts.append(f"{h}h")
    if m or h:
        parts.append(f"{m}m")
    parts.append(f"{s}s")
    return " ".join(parts)


# ---------------------------------------------------------------------------
# Main analysis
# ---------------------------------------------------------------------------

def analyse_folder(sub_dir: Path) -> dict:
    """
    Returns a result dict:
      {
        "name": str,
        "path": Path,
        "total_files": int,
        "by_exptime": {10.0: N, 20.0: N, ...},   # exptime_sec -> count
        "unknown_exptime": int,
        "total_integration_sec": float,
      }
    """
    files = collect_fits_files(sub_dir)

    by_exptime: dict[float, int] = defaultdict(int)
    unknown = 0
    total_sec = 0.0

    for f in files:
        exp = get_exptime(f)
        if exp is None:
            unknown += 1
        else:
            by_exptime[exp] += 1
            total_sec += exp

    return {
        "name": sub_dir.name,
        "path": sub_dir,
        "total_files": len(files),
        "by_exptime": dict(sorted(by_exptime.items())),
        "unknown_exptime": unknown,
        "total_integration_sec": total_sec,
    }


def print_results(results: list[dict]) -> None:
    print()
    print(f"{'TARGET':<35} {'SUBS':>6}  {'BREAKDOWN':<30}  {'INTEGRATION':>12}")
    print("─" * 90)

    for r in results:
        name = r["name"].removesuffix("_subs").removesuffix("_sub").strip()
        total = r["total_files"]
        integ = format_duration(r["total_integration_sec"]) if r["total_integration_sec"] else "—"

        # Build breakdown string: "634×20s  12×10s"
        parts = []
        for exp_sec, count in r["by_exptime"].items():
            exp_label = f"{int(exp_sec)}s" if exp_sec == int(exp_sec) else f"{exp_sec}s"
            parts.append(f"{count}×{exp_label}")
        if r["unknown_exptime"]:
            parts.append(f"{r['unknown_exptime']}×?s")
        breakdown = "  ".join(parts) if parts else "—"

        print(f"{name:<35} {total:>6}  {breakdown:<30}  {integ:>12}")

    print("─" * 90)

    # Totals row
    grand_total_files = sum(r["total_files"] for r in results)
    grand_total_sec = sum(r["total_integration_sec"] for r in results)

    # Aggregate by exptime across all targets
    grand_by_exp: dict[float, int] = defaultdict(int)
    for r in results:
        for exp, cnt in r["by_exptime"].items():
            grand_by_exp[exp] += cnt
    grand_parts = [f"{cnt}×{int(exp) if exp == int(exp) else exp}s"
                   for exp, cnt in sorted(grand_by_exp.items())]
    grand_unknown = sum(r["unknown_exptime"] for r in results)
    if grand_unknown:
        grand_parts.append(f"{grand_unknown}×?s")

    print(f"{'TOTAL':<35} {grand_total_files:>6}  {'  '.join(grand_parts):<30}  "
          f"{format_duration(grand_total_sec):>12}")
    print()

    unknown_total = sum(r["unknown_exptime"] for r in results)
    if unknown_total:
        print(f"⚠️  {unknown_total} file(s) had no exposure time in filename — counts still correct.\n")


def write_csv(results: list[dict], path: Path) -> None:
    # Collect all unique exptimes across results
    all_exps = sorted({exp for r in results for exp in r["by_exptime"]})

    with open(path, "w", newline="") as f:
        writer = csv.writer(f)
        header = ["Target", "Total Subs"] + [f"{int(e) if e == int(e) else e}s subs" for e in all_exps] + ["Unknown exp subs", "Total integration (s)", "Total integration"]
        writer.writerow(header)

        for r in results:
            name = r["name"].removesuffix("_subs").removesuffix("_sub").strip()
            row = [name, r["total_files"]]
            for exp in all_exps:
                row.append(r["by_exptime"].get(exp, 0))
            row.append(r["unknown_exptime"])
            row.append(round(r["total_integration_sec"], 1))
            row.append(format_duration(r["total_integration_sec"]))
            writer.writerow(row)

    print(f"CSV saved to: {path}")


# ---------------------------------------------------------------------------
# Deduplication — merge results that share the same canonical target name
# (handles multiple _sub/_subs folders for the same object)
# ---------------------------------------------------------------------------

def normalize(name: str) -> str:
    """Lowercase, strip spaces and underscores for fuzzy matching."""
    return re.sub(r"[\s_]+", "", name).lower()


def canonical_folder_name(folder_name: str) -> str:
    """Strip _sub/_subs suffix and return normalized target name."""
    name = folder_name
    for suffix in ("_subs", "_sub"):
        if name.endswith(suffix):
            name = name[:-len(suffix)]
            break
    return name.strip()


def deduplicate(results: list[dict]) -> list[dict]:
    """
    Merge results whose canonical folder names normalize to the same string.
    Preserves the display name from whichever folder had the most subs.
    """
    merged: dict[str, dict] = {}
    for r in results:
        key = normalize(canonical_folder_name(r["name"]))
        if key not in merged:
            merged[key] = {
                "name": r["name"],
                "total_files": r["total_files"],
                "by_exptime": defaultdict(int, r["by_exptime"]),
                "unknown_exptime": r["unknown_exptime"],
                "total_integration_sec": r["total_integration_sec"],
            }
        else:
            m = merged[key]
            # Keep display name of whichever had more subs
            if r["total_files"] > m["total_files"]:
                m["name"] = r["name"]
            m["total_files"]          += r["total_files"]
            m["unknown_exptime"]      += r["unknown_exptime"]
            m["total_integration_sec"]+= r["total_integration_sec"]
            for exp, cnt in r["by_exptime"].items():
                m["by_exptime"][exp] += cnt

    # Convert defaultdicts back and sort by_exptime
    out = []
    for m in merged.values():
        m["by_exptime"] = dict(sorted(m["by_exptime"].items()))
        out.append(m)
    return out


# ---------------------------------------------------------------------------
# Inventory update — write sub counts back into AstroImages Inventory.md
# ---------------------------------------------------------------------------

# Hardcoded vault path (canonical source of truth)
_VAULT_INV = Path(
    "~/Library/Mobile Documents/iCloud~md~obsidian/Documents"
    "/Obsidian Vault/Astrophotography/AstroImages Inventory.md"
).expanduser()


def prompt_for_inventory() -> Path | None:
    """Ask the user where the inventory is if it can't be found automatically."""
    print(f"⚠️  Inventory not found at expected vault location:")
    print(f"   {_VAULT_INV}")
    try:
        response = input("Enter path to inventory file (or press Enter to skip): ").strip()
    except (EOFError, KeyboardInterrupt):
        return None
    if not response:
        print("Skipping inventory update.")
        return None
    p = Path(response).expanduser().resolve()
    if not p.exists():
        print(f"⚠️  Not found: {p} — skipping.")
        return None
    return p


def _write_inventory(content: str, path: Path, label: str) -> None:
    """Write inventory content to a path, creating parent dirs if needed."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    print(f"   💾 Saved → {label}")


def update_inventory(results: list[dict], root: Path, dry_run: bool = False) -> None:
    """
    Parse AstroImages Inventory.md and update the Subs column for each
    target row that matches a folder in results. Leaves all other columns
    (Object, Notes, Status) untouched.

    Writes to:
      1. The vault copy (canonical)
      2. A local copy in root/ for quick reference
    """
    inventory_path = _VAULT_INV if _VAULT_INV.exists() else prompt_for_inventory()
    if inventory_path is None:
        return

    # Build lookup: normalized_name → sub count
    counts: dict[str, int] = {}
    for r in results:
        canon = canonical_folder_name(r["name"])
        key   = normalize(canon)
        counts[key] = counts.get(key, 0) + r["total_files"]

    text  = inventory_path.read_text(encoding="utf-8")
    lines = text.splitlines()
    updated_lines = []
    updates = 0
    unmatched = []

    # Match markdown table rows: | Target | ... | Subs | ... |
    # We identify the Subs column by position from the header row.
    header_idx    = None
    subs_col_idx  = None

    for i, line in enumerate(lines):
        # Detect table header rows containing "Subs"
        if re.match(r"^\|.*\bSubs\b.*\|", line):
            cols = [c.strip() for c in line.split("|")]
            if "Subs" in cols:
                subs_col_idx = cols.index("Subs")
                header_idx   = i
            updated_lines.append(line)
            continue

        # Skip separator rows
        if re.match(r"^\|[-| :]+\|", line):
            updated_lines.append(line)
            continue

        # Data rows
        if subs_col_idx is not None and line.startswith("|"):
            cols = [c.strip() for c in line.split("|")]
            if len(cols) > subs_col_idx:
                target_raw = cols[1]  # column 0 is empty (before first |)
                key = normalize(target_raw)
                if key in counts:
                    old_val = cols[subs_col_idx]
                    new_val = f"{counts[key]:,}"  # comma-formatted: 1,188
                    if old_val != new_val:
                        cells = cols[1:-1]  # strip leading/trailing empties
                        cells[subs_col_idx - 1] = new_val
                        line = "| " + " | ".join(cells) + " |"
                        updates += 1
                        print(f"  ✏️  {target_raw:<25} {old_val:>6} → {new_val}")
                else:
                    if target_raw and not target_raw.startswith("-"):
                        unmatched.append(target_raw)

        updated_lines.append(line)

    # Stamp the "Sub counts updated" line with today's date
    today = date.today().strftime("%Y-%m-%d")
    stamped = []
    stamp_found = False
    for line in updated_lines:
        if line.startswith("*Sub counts updated:"):
            line = f"*Sub counts updated: {today} (auto)*"
            stamp_found = True
        stamped.append(line)
    if not stamp_found:
        print("⚠️  No '*Sub counts updated:' marker found in inventory — datestamp not applied.")

    final_text = "\n".join(stamped) + "\n"

    print(f"\n✅ Updated {updates} row(s)  |  Sub counts updated: {today}")
    if dry_run:
        print("   DRY RUN — inventory not written.")
        return
    _write_inventory(final_text, inventory_path, f"vault: {inventory_path}")
    _write_inventory(final_text, root / "AstroImages Inventory.md", "local: ./AstroImages Inventory.md")

    if unmatched:
        print(f"⚠️  No folder match for: {', '.join(sorted(set(unmatched)))}")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    if "-h" in sys.argv or "--help" in sys.argv:
        print(__doc__)
        sys.exit(0)

    args  = [a for a in sys.argv[1:] if not a.startswith("-")]
    flags = [a for a in sys.argv[1:] if a.startswith("-")]

    do_csv        = "--csv"              in flags
    do_update_inv = "--update-inventory" in flags
    dry_run       = "--dry-run"          in flags

    # First positional arg that looks like a path is root; second is filter
    root_arg    = args[0] if args else "."
    filter_name = args[1] if len(args) > 1 else ""

    root = Path(root_arg).expanduser().resolve()
    if not root.exists():
        print(f"Error: path does not exist: {root}")
        sys.exit(1)

    print(f"Scanning: {root}")
    if filter_name:
        print(f"Filter:   '{filter_name}'")
    print("Counting subs (reading exposure time from filenames)...")

    folders = find_sub_folders(root, filter_name)
    if not folders:
        print("No _sub / _subs folders found.")
        sys.exit(0)

    results = []
    for folder in folders:
        sys.stdout.write(f"\r  Analysing {folder.name:<50}")
        sys.stdout.flush()
        results.append(analyse_folder(folder))
    sys.stdout.write("\r" + " " * 60 + "\r")

    deduped = deduplicate(results)
    print_results(deduped)

    if do_csv:
        write_csv(deduped, root / "subs_inventory.csv")

    if do_update_inv:
        update_inventory(deduped, root, dry_run=dry_run)


if __name__ == "__main__":
    main()
