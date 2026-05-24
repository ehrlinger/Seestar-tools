#!/usr/bin/env python3
"""
batch_stack.py

Batch-runs Siril's Seestar_Preprocessing script across all _sub/_subs
folders that don't yet have a stacked result (or whose stack is stale).
Assumes you are running from the NAS Seestar archive — path defaults to '.'.

Usage:
    python3 batch_stack.py                          # stack all pending in current dir
    python3 batch_stack.py /path/to/Seestar         # explicit path
    python3 batch_stack.py --dry-run                # preview only
    python3 batch_stack.py "M 51"                   # single target filter
    python3 batch_stack.py --script /path/to.ssf    # explicit Siril script path
    python3 batch_stack.py -h                       # this help

Requirements:
    siril-cli on PATH  (add /Applications/Siril.app/Contents/MacOS to PATH)

How "needs stacking" is determined:
    A folder needs stacking if its lights/ subfolder has FITS files but
    no stacked result file (*x*sec*.fit) exists in the _sub folder itself.
"""

import re
import sys
import time
import threading
import subprocess
from pathlib import Path

SIRIL_CLI     = "siril-cli"
SCRIPT_NAME   = "Seestar_Preprocessing"

FITS_EXTENSIONS = {".fit", ".fits", ".FIT", ".FITS"}

# Pattern matching a stacked output filename: M_51_1175x20sec_T25degC_2026-05-15.fit
STACKED_RE = re.compile(r"\d+x\d+sec", re.IGNORECASE)

# Common locations where Siril installs scripts on macOS
SCRIPT_SEARCH_PATHS = [
    Path.home() / "Library/Application Support/siril/scripts",
    Path.home() / "Library/Application Support/Siril/scripts",
    Path.home() / "Library/Application Support/org.siril.Siril/siril-scripts/preprocessing",
    Path.home() / "Library/Application Support/org.siril.Siril/siril-scripts",
    Path.home() / "siril/scripts",
    Path.home() / ".siril/scripts",
    Path.home() / ".config/siril/scripts",
    Path("/Applications/Siril.app/Contents/Resources/scripts"),
    Path("/Applications/Siril.app/Contents/share/siril/scripts"),
    Path("/Applications/Siril.app/Contents/Resources/share/siril/scripts"),
]


# ---------------------------------------------------------------------------
# NAS keepalive + mount check
# ---------------------------------------------------------------------------

def start_nas_keepalive(root: Path, interval: int = 10) -> threading.Event:
    """
    Start a background thread that reads a few bytes from the NAS every
    `interval` seconds to prevent HDD spindown during long stacks.
    A real read (not just stat) is more reliable at resetting NAS idle timers.
    Returns a stop_event; call stop_event.set() when done.
    """
    stop_event = threading.Event()

    # Find or create a small sentinel file to read from
    sentinel = root / ".seestar_keepalive"

    def _ping() -> None:
        while not stop_event.wait(interval):
            try:
                # Write then read — guarantees the NAS sees actual I/O
                sentinel.write_bytes(b"1")
                sentinel.read_bytes()
            except OSError:
                pass  # NAS offline; the main thread will catch the real failure

    t = threading.Thread(target=_ping, daemon=True)
    t.start()
    return stop_event


def check_mount(path: Path) -> bool:
    """Return True if path is accessible (NAS awake and mounted)."""
    try:
        path.stat()
        return True
    except OSError:
        return False


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def find_script() -> Path | None:
    """Search known locations for Seestar_Preprocessing.ssf."""
    for directory in SCRIPT_SEARCH_PATHS:
        candidate = directory / f"{SCRIPT_NAME}.ssf"
        if candidate.exists():
            return candidate
    return None


def find_sub_folders(root: Path, filter_name: str = "") -> list[Path]:
    folders = sorted(
        p for p in root.rglob("*")
        if p.is_dir() and (p.name.endswith("_sub") or p.name.endswith("_subs"))
    )
    if filter_name:
        folders = [f for f in folders if filter_name.lower() in f.name.lower()]
    return folders


def has_lights(sub_dir: Path) -> bool:
    """Return True if lights/ contains at least one raw FITS file."""
    lights = sub_dir / "lights"
    if not lights.exists():
        return False
    return any(
        f.suffix in FITS_EXTENSIONS
        for f in lights.iterdir()
        if f.is_file()
    )


PROCESSED_PREFIXES = ("starless_", "starmask_", "r_pp_", "pp_", "stack_")

def has_stack(sub_dir: Path) -> Path | None:
    """
    Return the primary stacked result file if one exists, else None.
    Skips post-processed derivatives (starless_, starmask_, r_pp_, pp_, stack_)
    so that a previously-processed file doesn't masquerade as the raw stack.
    """
    for f in sub_dir.iterdir():
        if (f.is_file()
                and f.suffix in FITS_EXTENSIONS
                and STACKED_RE.search(f.name)
                and not any(f.name.startswith(p) for p in PROCESSED_PREFIXES)):
            return f
    return None


def count_lights(sub_dir: Path) -> int:
    """Count raw FITS files in lights/."""
    lights = sub_dir / "lights"
    if not lights.exists():
        return 0
    return sum(1 for f in lights.iterdir() if f.is_file() and f.suffix in FITS_EXTENSIONS)


def stacked_count(stack_file: Path) -> int | None:
    """Extract the sub count from a stacked filename e.g. M_51_1175x20sec → 1175."""
    m = re.search(r"(\d+)x\d+sec", stack_file.name, re.IGNORECASE)
    return int(m.group(1)) if m else None


def needs_stacking(sub_dir: Path) -> tuple[bool, str]:
    """
    Returns (should_stack, reason).
    Stacks if:
      - No stack exists yet, OR
      - lights/ has more subs than the existing stack
    """
    if not has_lights(sub_dir):
        return False, "no lights"

    stack = has_stack(sub_dir)
    if stack is None:
        return True, "not yet stacked"

    current = count_lights(sub_dir)
    prev    = stacked_count(stack)

    if prev is None:
        return False, f"stacked but count unparseable — skipping ({stack.name})"

    if current > prev:
        return True, f"stale — {prev} stacked, {current} lights now"

    return False, f"up to date ({prev} subs)"


# ---------------------------------------------------------------------------
# Stacking
# ---------------------------------------------------------------------------

def stack_folder(sub_dir: Path, script_path: Path, dry_run: bool) -> bool | None:
    """
    Run siril-cli on a single folder.
    Returns True on success, False on siril failure, None if NAS is offline
    (caller should stop the batch rather than logging it as a normal failure).
    """
    # Guard: check that the NAS is still accessible before launching siril-cli.
    # If it went to sleep the check wakes it; if it's gone we stop cleanly.
    if not dry_run and not check_mount(sub_dir):
        print(f"  ⚠️   Path not accessible: {sub_dir}")
        print(f"       NAS may have gone to sleep. Remount and rerun to continue.")
        return None

    cmd = [SIRIL_CLI, "-d", str(sub_dir), "-s", str(script_path)]
    print(f"  ▶  {' '.join(cmd)}")

    if dry_run:
        return True

    try:
        t0 = time.monotonic()
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=3600,  # 1 hour max per target
        )
        elapsed = time.monotonic() - t0

        if result.returncode == 0:
            # Find the output file
            stack = has_stack(sub_dir)
            print(f"  ✅ Done → {stack.name if stack else '(result not found)'}")
            return True
        else:
            # Fast exit (< 5s) almost always means the NAS went offline mid-run
            nas_hint = " — NAS may be offline" if elapsed < 5.0 else ""
            print(f"  ❌ siril-cli exited {result.returncode} ({elapsed:.1f}s){nas_hint}")

            # siril-cli writes most output to stdout, not stderr — show both
            combined = []
            if result.stdout.strip():
                combined.extend(result.stdout.strip().splitlines())
            if result.stderr.strip():
                combined.extend(result.stderr.strip().splitlines())
            # Filter out the harmless locale warning to reduce noise
            combined = [l for l in combined if "Locale directory" not in l]
            for line in combined[-20:]:
                print(f"     {line}")

            # If it died fast, confirm NAS state and signal caller to stop
            if elapsed < 5.0 and not check_mount(sub_dir.parent):
                print(f"\n  🛑  NAS is no longer accessible. Stopping batch.")
                return None

            return False
    except subprocess.TimeoutExpired:
        print(f"  ❌ Timed out after 1 hour")
        return False
    except FileNotFoundError:
        print(f"  ❌ siril-cli not found — is it on your PATH?")
        sys.exit(1)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    if "-h" in sys.argv or "--help" in sys.argv:
        print(__doc__)
        sys.exit(0)

    argv  = sys.argv[1:]
    flags = [a for a in argv if a.startswith("-")]
    args  = [a for a in argv if not a.startswith("-")]

    dry_run    = "--dry-run" in flags
    script_arg = None
    for i, f in enumerate(argv):
        if f == "--script" and i + 1 < len(argv):
            script_arg = argv[i + 1]
            break

    # First non-flag arg that looks like a path is root; second is a target filter
    path_args = [a for a in args if a != script_arg]
    root_arg  = path_args[0] if path_args else "."
    filter_name = path_args[1] if len(path_args) > 1 else ""

    root = Path(root_arg).expanduser().resolve()
    if not root.exists():
        print(f"Error: path not found: {root}")
        sys.exit(1)

    # Find Siril script
    if script_arg:
        script_path = Path(script_arg).expanduser().resolve()
    else:
        script_path = find_script()

    if not script_path or not script_path.exists():
        print("❌  Seestar_Preprocessing.ssf not found.")
        print("   Searched:")
        for p in SCRIPT_SEARCH_PATHS:
            print(f"     {p}")
        print("\n   Options:")
        print("   1. In Siril: Preferences → Scripts → enable Seestar_Preprocessing → Apply")
        print("   2. Pass the path explicitly: --script /path/to/Seestar_Preprocessing.ssf")
        sys.exit(1)

    print(f"\nSiril script : {script_path}")
    print(f"Seestar root : {root}")
    if filter_name:
        print(f"Filter       : '{filter_name}'")
    if dry_run:
        print(f"\n{'='*60}")
        print("  DRY RUN — siril-cli will not be called")
        print(f"{'='*60}")
    print()

    # Find folders
    all_folders = find_sub_folders(root, filter_name)
    assessed = [(f, *needs_stacking(f)) for f in all_folders]
    pending  = [(f, reason) for f, should, reason in assessed if should]
    skipped  = [(f, reason) for f, should, reason in assessed if not should]

    print(f"Found {len(all_folders)} _sub/_subs folder(s):")
    print(f"  Pending (needs stacking) : {len(pending)}")
    print(f"  Skipped                  : {len(skipped)}")
    if skipped:
        for f, reason in skipped:
            print(f"    • {f.name}  [{reason}]")
    print()

    if not pending:
        print("✅ Nothing to stack.")
        return

    succeeded = []
    failed    = []
    nas_stopped = False

    # Keep NAS awake by pinging it every 20 seconds in the background.
    # Most NAS drives spin down after 5–30 min of idle; M101 with 2000+ subs
    # takes far longer than that to stack.
    keepalive = start_nas_keepalive(root, interval=20)

    try:
        for i, (sub_dir, reason) in enumerate(pending, 1):
            print(f"{'─'*60}")
            print(f"[{i}/{len(pending)}] {sub_dir.name}  [{reason}]")
            print(f"{'─'*60}")
            ok = stack_folder(sub_dir, script_path, dry_run)
            if ok is None:
                # NAS offline — stop cleanly so the user can remount and retry
                nas_stopped = True
                break
            (succeeded if ok else failed).append(sub_dir.name)
            print()
    finally:
        keepalive.set()  # always stop the background thread

    # Summary
    print(f"{'='*60}")
    if dry_run:
        print(f"DRY RUN COMPLETE — would have processed {len(pending)} folder(s)")
    else:
        print(f"BATCH STACK COMPLETE")
        print(f"  Succeeded : {len(succeeded)}")
        if failed:
            print(f"  Failed    : {len(failed)}")
            for name in failed:
                print(f"    ✗ {name}")
        if nas_stopped:
            remaining = len(pending) - len(succeeded) - len(failed)
            print(f"\n  ⚠️  Stopped early — NAS went offline.")
            print(f"     {remaining} folder(s) not attempted.")
            print(f"     Remount the NAS and rerun — completed folders will be skipped.")
    print(f"{'='*60}\n")


if __name__ == "__main__":
    main()
