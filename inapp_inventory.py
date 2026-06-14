#!/usr/bin/env python3
"""
inapp_inventory.py

Catalog the Seestar's **in-app stacked previews** (the `Stacked_*.fit` / `.jpg`
the Seestar writes into each `<target>/` folder — NOT the `*_sub/` raw subs or
your Siril stacks). Everything is read from the filename, which is fully
self-describing — no FITS headers, no astropy:

    Stacked_<subs>_<target>_<exp>s_<filter>_<YYYYMMDD-HHMMSS>[_thn].<ext>

By default it **prints** a markdown table of the **best (most-subs) in-app stack
per target** — a caption-ready reference ("NGC 6946 — 459×20s, 2h 33m") without
digging through filenames. Pass ``--write`` to save it into your vault (next to
the sub inventory) plus a copy at the archive root.

Usage:
    python3 inapp_inventory.py                      # catalog ./ (best per target)
    python3 inapp_inventory.py /path/to/Seestar     # explicit archive path
    python3 inapp_inventory.py --all                # every stack version, not just best
    python3 inapp_inventory.py --csv                # CSV instead of a table
    python3 inapp_inventory.py /path --write        # write to the vault + a local copy
    python3 inapp_inventory.py /path --write DIR_OR_FILE   # explicit write location
    python3 inapp_inventory.py -h                   # this help

--write resolves the destination like count_subs: --write PATH → SEESTAR_VAULT_INV
(exported from seestar.conf) → built-in vault default, saving to
'Astrophotography/In-App Stacks Inventory.md' plus a copy at the archive root.
"""

import re
import sys
from pathlib import Path

from seestar_common import is_in_excluded

# Stacked_<subs>_<target>_<exp>s_<filter>_<YYYYMMDD-HHMMSS>[_thn].<ext>
_STACKED_RE = re.compile(
    r"^Stacked_(\d+)_(.+?)_(\d+(?:\.\d+)?)s_([^_]+)_(\d{4})(\d{2})(\d{2})-(\d{6})(_thn)?"
    r"\.(fit|fits|jpe?g)$",
    re.IGNORECASE,
)


def parse_stacked_name(name: str) -> dict | None:
    """
    Parse a Seestar in-app stack filename into its fields, or return ``None`` if
    *name* isn't one (a raw ``Light_*`` sub or a Siril ``<target>_NNNxNNsec`` stack
    both return ``None``). Pure — filename only, no I/O.
    """
    m = _STACKED_RE.match(name)
    if not m:
        return None
    subs, target, exp, filt, yyyy, mm, dd, hms, thn, ext = m.groups()
    return {
        "subs": int(subs),
        "target": target,
        "exp": float(exp),
        "filter": filt,
        "date": f"{yyyy}-{mm}-{dd}",
        "datetime": f"{yyyy}{mm}{dd}-{hms}",   # full, lexically sortable timestamp
        "thumb": thn is not None,
        "ext": ext.lower(),
    }


def best_per_target(stacks: list) -> list:
    """
    Given parsed stacks (dicts with ``target``/``subs``/``datetime``), return one
    entry per target: the stack with the most subs, ties broken by the latest full
    timestamp (so same-subs-same-day stacks resolve deterministically). Pure — no I/O.
    """
    best: dict = {}
    for s in stacks:
        cur = best.get(s["target"])
        if cur is None or (s["subs"], s["datetime"]) > (cur["subs"], cur["datetime"]):
            best[s["target"]] = s
    return [best[t] for t in sorted(best)]


_STEM_RE = re.compile(r"(_thn)?\.[^.]+$", re.IGNORECASE)


def find_inapp_stacks(root: Path) -> list:
    """
    Scan the ``<target>/`` folders directly under *root* (skipping ``*_sub``/
    ``*_subs`` and excluded ``_trash``/``scripts`` trees) for Seestar in-app stacks,
    grouping the ``.fit`` / ``.jpg`` / ``_thn.jpg`` of one stack into a single entry
    with ``has_fit`` / ``has_jpg`` flags. Non-stack files (raw ``Light_*`` subs,
    Siril masters) are ignored via :func:`parse_stacked_name`.
    """
    by_stem: dict = {}
    for d in sorted(root.iterdir()):
        if not d.is_dir():
            continue
        if d.name.endswith("_sub") or d.name.endswith("_subs"):
            continue
        if is_in_excluded(d, root):
            continue
        try:
            entries = list(d.iterdir())
        except OSError:
            continue
        for f in entries:
            if not f.is_file():
                continue
            parsed = parse_stacked_name(f.name)
            if parsed is None:
                continue
            stem = _STEM_RE.sub("", f.name)   # stack identity: drop _thn + extension
            s = by_stem.get(stem)
            if s is None:
                s = {k: parsed[k] for k in ("subs", "target", "exp", "filter", "date", "datetime")}
                s.update(folder=d.name, has_fit=False, has_jpg=False)
                by_stem[stem] = s
            if parsed["ext"] in ("fit", "fits"):
                s["has_fit"] = True
            elif not parsed["thumb"]:          # full jpg/jpeg (not the thumbnail)
                s["has_jpg"] = True
    return list(by_stem.values())


def _integration(subs: int, exp: float) -> str:
    total = int(round(subs * exp))
    h, m, sec = total // 3600, (total % 3600) // 60, total % 60
    parts = []
    if h:
        parts.append(f"{h}h")
    if m or h:
        parts.append(f"{m}m")
    parts.append(f"{sec}s")
    return " ".join(parts)


def _exp_label(exp: float) -> str:
    return f"{int(exp)}s" if exp == int(exp) else f"{exp}s"


def render_markdown(rows: list) -> str:
    out = [
        "# In-App Stack Inventory",
        "",
        "Seestar on-board `Stacked_*` previews (one row = the best stack per target).",
        "",
        "| Target | Subs | Exp | Integration | Filter | Date (UTC) | fit | jpg |",
        "|---|---:|---:|---:|---|---|:--:|:--:|",
    ]
    for r in rows:
        out.append(
            f"| {r['target']} | {r['subs']} | {_exp_label(r['exp'])} | "
            f"{_integration(r['subs'], r['exp'])} | {r['filter']} | {r['date']} | "
            f"{'✓' if r['has_fit'] else '—'} | {'✓' if r['has_jpg'] else '—'} |"
        )
    return "\n".join(out) + "\n"


_STD_INAPP_REL = Path("Astrophotography") / "In-App Stacks Inventory.md"
_DEFAULT_VAULT_DIR = Path(
    "~/Library/Mobile Documents/iCloud~md~obsidian/Documents/Obsidian Vault"
).expanduser()


def resolve_inapp_inventory_path(explicit: str | None = None) -> Path:
    """
    Resolve where to WRITE the in-app catalog, highest priority first:
      1. ``--write PATH`` — a directory gets ``Astrophotography/In-App Stacks
         Inventory.md`` appended; a file path is used as-is
      2. ``SEESTAR_VAULT_INV`` (the vault root dir, exported from seestar.conf)
      3. the built-in default vault
    No existence check — the caller creates parent dirs. Pure but for ``os.environ``.
    """
    import os
    if explicit:
        p = Path(explicit).expanduser()
        # A ".md" path is the file itself; anything else is treated as a vault dir
        # (works even if it doesn't exist yet — the caller mkdirs the parent).
        return p if p.suffix.lower() == ".md" else (p / _STD_INAPP_REL)
    env = os.environ.get("SEESTAR_VAULT_INV", "").strip()
    if env:
        cand = Path(env).expanduser() / _STD_INAPP_REL
        # Only trust the env vault if its folder actually exists — guards against a
        # malformed value (e.g. backslash-escaped spaces) creating junk directories.
        if cand.parent.is_dir():
            return cand
    return _DEFAULT_VAULT_DIR / _STD_INAPP_REL


def render_csv(rows: list) -> str:
    import csv
    import io
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["target", "subs", "exp_s", "integration_s", "filter", "date", "has_fit", "has_jpg"])
    for r in rows:
        w.writerow([r["target"], r["subs"], r["exp"], int(round(r["subs"] * r["exp"])),
                    r["filter"], r["date"], int(r["has_fit"]), int(r["has_jpg"])])
    return buf.getvalue()


def main() -> None:
    if "-h" in sys.argv or "--help" in sys.argv:
        print(__doc__)
        return
    flags = [a for a in sys.argv[1:] if a.startswith("-")]
    args = [a for a in sys.argv[1:] if not a.startswith("-")]
    root = Path(args[0] if args else ".").expanduser().resolve()
    if not root.is_dir():
        sys.exit(f"ERROR: not a directory: {root}")

    write = "--write" in flags or "--update-inventory" in flags
    explicit = None
    for i, a in enumerate(sys.argv):
        if a == "--write" and i + 1 < len(sys.argv) and not sys.argv[i + 1].startswith("-"):
            explicit = sys.argv[i + 1]

    stacks = find_inapp_stacks(root)
    rows = stacks if "--all" in flags else best_per_target(stacks)
    rows = sorted(rows, key=lambda r: r["target"])
    if not rows:
        print(f"No in-app Stacked_* files found under {root}")
        return

    text = render_csv(rows) if "--csv" in flags else render_markdown(rows)
    if not write:
        print(text)
        return

    # --write: save the markdown to the vault inventory + a local copy on the NAS root.
    vault = resolve_inapp_inventory_path(explicit)
    local = root / "In-App Stacks Inventory.md"
    md = render_markdown(rows)
    for dest in (vault, local):
        try:
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_text(md, encoding="utf-8")
            print(f"  saved -> {dest}")
        except OSError as e:
            print(f"  WARNING: could not write {dest}: {e}")
    print(f"  ({len(rows)} target{'s' if len(rows) != 1 else ''} catalogued)")


if __name__ == "__main__":
    main()
