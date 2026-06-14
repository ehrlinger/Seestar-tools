#!/usr/bin/env bash
# sync_seestar.sh
#
# Full Seestar sync pipeline (run from anywhere):
#   Step 1  — rsync FITS files from Seestar EMMC → NAS (skips JPGs)
#   Step 2  — rename _sub/_subs folders (remove spaces)
#   Step 3  — organize: move Light_*.fits into lights/ subdirs
#   Step 3b — normalise by mount mode: flat lights/ for a single mode,
#             altaz/lights/ + eq/lights/ when both are present (Siril-ready)
#   Step 4  — count subs + update AstroImages Inventory.md (vault + local copy)
#
# Usage:
#   ./sync_seestar.sh                         # full pipeline (paths from seestar.conf)
#   ./sync_seestar.sh --dst PATH              # override destination (NAS Seestar folder)
#   ./sync_seestar.sh --src PATH              # override source (Seestar EMMC)
#   ./sync_seestar.sh --src P1 --dst P2       # override both
#   ./sync_seestar.sh --dry-run               # preview only, no changes
#   ./sync_seestar.sh --no-cleanup            # sync only, skip steps 2-4
#   ./sync_seestar.sh --yes                   # skip the destination confirmation prompt
#   ./sync_seestar.sh -h                      # this help
#
# Path resolution (highest priority first):
#   1. --src / --dst command-line arguments
#   2. SEESTAR_EMMC / SEESTAR_NAS in seestar.conf
#   3. platform fallback defaults (uname -s)
#
# A real (non-dry-run) sync prints the resolved SRC → DST and asks for
# confirmation before touching anything, so you can't sync to the wrong folder
# by accident. Pass --yes to skip the prompt in scripted runs.

set -uo pipefail

SRC=""
DST=""
CLI_SRC=""
CLI_DST=""
DRY_RUN=0
NO_CLEANUP=0
ASSUME_YES=0

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RENAME="$SCRIPT_DIR/rename_seestar_folders.py"
ORGANIZE="$SCRIPT_DIR/organize_subs.py"
SORT="$SCRIPT_DIR/sort_by_exptime.py"
COUNT="$SCRIPT_DIR/count_subs.py"
CONF="$SCRIPT_DIR/seestar.conf"

print_help() {
  cat <<'EOF'
sync_seestar.sh — full Seestar sync pipeline

  Step 1  — rsync FITS files from Seestar EMMC → NAS (skips JPGs)
  Step 2  — rename _sub/_subs folders (remove spaces)
  Step 3  — organize: move Light_*.fits into lights/ subdirs
  Step 3b — normalise by mount mode (flat lights/ if one mode,
            altaz/lights/ + eq/lights/ if both) — Siril-ready
  Step 4  — count subs + update AstroImages Inventory.md

Usage:
  ./sync_seestar.sh                     # full pipeline (paths from seestar.conf)
  ./sync_seestar.sh --dst PATH          # override destination (NAS Seestar folder)
  ./sync_seestar.sh --src PATH          # override source (Seestar EMMC)
  ./sync_seestar.sh --src P1 --dst P2   # override both
  ./sync_seestar.sh --dry-run           # preview only, no changes
  ./sync_seestar.sh --no-cleanup        # sync only, skip steps 2-4
  ./sync_seestar.sh --yes               # skip the destination confirmation prompt
  ./sync_seestar.sh -h                  # this help

Path resolution (highest priority first):
  1. --src / --dst command-line arguments
  2. SEESTAR_EMMC / SEESTAR_NAS in seestar.conf
  3. platform fallback defaults
EOF
}

# ─────────────────────────────────────────────────────────
# Parse arguments (before path resolution so --src/--dst win)
# ─────────────────────────────────────────────────────────
while [[ $# -gt 0 ]]; do
  case "$1" in
    -h|--help)         print_help; exit 0 ;;
    --dry-run)         DRY_RUN=1; shift ;;
    --no-cleanup)      NO_CLEANUP=1; shift ;;
    -y|--yes)          ASSUME_YES=1; shift ;;
    --src)
      [[ $# -ge 2 && -n "$2" ]] || { echo "❌  --src requires a non-empty path value"; exit 1; }
      CLI_SRC="$2"; shift 2 ;;
    --src=*)
      CLI_SRC="${1#*=}"
      [[ -n "$CLI_SRC" ]] || { echo "❌  --src requires a non-empty path value"; exit 1; }
      shift ;;
    --dst|--dest)
      [[ $# -ge 2 && -n "$2" ]] || { echo "❌  --dst requires a non-empty path value"; exit 1; }
      CLI_DST="$2"; shift 2 ;;
    --dst=*|--dest=*)
      CLI_DST="${1#*=}"
      [[ -n "$CLI_DST" ]] || { echo "❌  --dst requires a non-empty path value"; exit 1; }
      shift ;;
    *)
      echo "❌  Unknown argument: $1"
      echo "    Run './sync_seestar.sh -h' for usage."
      exit 1
      ;;
  esac
done

# ─────────────────────────────────────────────────────────
# Resolve SRC/DST (precedence, highest first: CLI override > conf > platform fallback)
# ─────────────────────────────────────────────────────────
if [[ -f "$CONF" ]]; then
  # shellcheck disable=SC1090
  source "$CONF"
  SRC="${SEESTAR_EMMC:-$SRC}"
  DST="${SEESTAR_NAS:-$DST}"
  # Make the inventory path from the conf visible to count_subs.py (Step 4)
  if [[ -n "${SEESTAR_VAULT_INV:-}" ]]; then
    export SEESTAR_VAULT_INV
  fi
fi

# Command-line arguments take precedence over the conf
[[ -n "$CLI_SRC" ]] && SRC="$CLI_SRC"
[[ -n "$CLI_DST" ]] && DST="$CLI_DST"

# Platform-aware fallbacks if still unset
if [[ -z "$SRC" || -z "$DST" ]]; then
  case "$(uname -s)" in
    Darwin)
      SRC="${SRC:-/Volumes/EMMC Images/MyWorks/}"
      DST="${DST:-/Volumes/YourNAS/Seestar/}"
      ;;
    Linux)
      SRC="${SRC:-/media/$USER/EMMC Images/MyWorks/}"
      DST="${DST:-/mnt/nas/Seestar/}"
      ;;
    *)
      # Windows (git-bash / WSL) and others: no sensible default
      ;;
  esac
fi

if [[ -z "$SRC" || -z "$DST" ]]; then
  echo "❌  SRC or DST not configured."
  echo "    Pass --src/--dst, or copy seestar.conf.example → seestar.conf and set"
  echo "    SEESTAR_EMMC + SEESTAR_NAS."
  exit 1
fi

# ─────────────────────────────────────────────────────────
# Dry-run banner
# ─────────────────────────────────────────────────────────
RSYNC_FLAGS=(-av --progress --stats)
STEP_FLAGS=""
if [[ $DRY_RUN -eq 1 ]]; then
  RSYNC_FLAGS+=(--dry-run)
  STEP_FLAGS="--dry-run"
  echo ""
  echo "══════════════════════════════════════════════════"
  echo "  DRY RUN — no files will be copied or moved"
  echo "══════════════════════════════════════════════════"
fi

# ─────────────────────────────────────────────────────────
# Check mounts
# ─────────────────────────────────────────────────────────
echo ""
if [[ ! -d "$SRC" ]]; then
  echo "❌  Source not found: $SRC"
  echo "    Is the Seestar connected and mounted?"
  exit 1
fi
if [[ ! -d "$DST" ]]; then
  echo "❌  Destination not found: $DST"
  echo "    Is the NAS mounted? (create the folder first, or fix --dst/seestar.conf)"
  exit 1
fi

# ─────────────────────────────────────────────────────────
# Show resolved paths and confirm the destination
# ─────────────────────────────────────────────────────────
echo "══════════════════════════════════════════════════"
echo "  Seestar sync — resolved paths"
echo "══════════════════════════════════════════════════"
echo "  SRC (Seestar): $SRC"
echo "  DST (NAS):     $DST"
echo ""

if [[ $DRY_RUN -eq 0 && $ASSUME_YES -eq 0 ]]; then
  if [[ ! -t 0 ]]; then
    echo "❌  Refusing to run unattended without confirmation."
    echo "    Re-run with --yes once you've confirmed the destination above,"
    echo "    or use --dry-run to preview."
    exit 1
  fi
  read -r -p "  Sync to the destination above? [y/N] " reply
  case "$reply" in
    [yY]|[yY][eE][sS]) ;;
    *) echo "  Aborted — nothing was changed."; exit 0 ;;
  esac
  echo ""
fi

# ─────────────────────────────────────────────────────────
# Step 1: Rsync FITS only (exclude all JPGs)
# ─────────────────────────────────────────────────────────
echo "══════════════════════════════════════════════════"
echo "  STEP 1 — Sync FITS files to NAS (skip JPGs)"
echo "══════════════════════════════════════════════════"
echo ""

rsync "${RSYNC_FLAGS[@]}" \
  --exclude="*.jpg"   \
  --exclude="*.jpeg"  \
  --exclude="*.JPG"   \
  --exclude="*.JPEG"  \
  --exclude=".DS_Store" \
  --exclude="._*"     \
  "$SRC" "$DST"

RSYNC_EXIT=$?
if [[ $RSYNC_EXIT -ne 0 ]]; then
  echo ""
  echo "❌  rsync exited with code $RSYNC_EXIT — check output above."
  exit $RSYNC_EXIT
fi

echo ""
echo "✅  Sync complete."

if [[ $NO_CLEANUP -eq 1 ]]; then
  echo ""
  echo "  --no-cleanup set — skipping organise/sort/count steps."
  echo "══════════════════════════════════════════════════"
  echo "  ALL DONE"
  echo "══════════════════════════════════════════════════"
  exit 0
fi

# ─────────────────────────────────────────────────────────
# Step 2: Rename folders (remove spaces)
# ─────────────────────────────────────────────────────────
if [[ -f "$RENAME" ]]; then
  echo ""
  echo "══════════════════════════════════════════════════"
  echo "  STEP 2 — Rename: remove spaces from folder names"
  echo "══════════════════════════════════════════════════"
  # shellcheck disable=SC2086  # STEP_FLAGS is intentionally split (empty or --dry-run)
  python3 "$RENAME" "$DST" $STEP_FLAGS
else
  echo "⚠️   rename_seestar_folders.py not found at $RENAME — skipping rename step"
fi

# ─────────────────────────────────────────────────────────
# Step 3: Organize (move Light_*.fits → lights/)
# ─────────────────────────────────────────────────────────
if [[ -f "$ORGANIZE" ]]; then
  echo ""
  echo "══════════════════════════════════════════════════"
  echo "  STEP 3 — Organize: move subs into lights/"
  echo "══════════════════════════════════════════════════"
  # shellcheck disable=SC2086  # STEP_FLAGS is intentionally split (empty or --dry-run)
  python3 "$ORGANIZE" "$DST" $STEP_FLAGS
else
  echo "⚠️   organize_subs.py not found at $ORGANIZE — skipping organize step"
fi

# ─────────────────────────────────────────────────────────
# Step 3b: Normalise by mount mode (flat lights/, or altaz/lights/ + eq/lights/)
# ─────────────────────────────────────────────────────────
if [[ -f "$SORT" ]]; then
  echo ""
  echo "══════════════════════════════════════════════════"
  echo "  STEP 3b — Sort subs by exposure (Siril-ready)"
  echo "══════════════════════════════════════════════════"
  # shellcheck disable=SC2086  # STEP_FLAGS is intentionally split (empty or --dry-run)
  python3 "$SORT" --all "$DST" $STEP_FLAGS
  SORT_EXIT=$?
  if [[ $SORT_EXIT -ne 0 ]]; then
    echo "⚠️   exposure-sort step failed (exit $SORT_EXIT)."
    echo "    Subs were synced but not split by exposure. Often this is a missing"
    echo "    dependency: pip install astropy --break-system-packages"
  fi
else
  echo "⚠️   sort_by_exptime.py not found at $SORT — skipping exposure-sort step"
fi

# ─────────────────────────────────────────────────────────
# Step 4: Count subs + update inventory
# ─────────────────────────────────────────────────────────
if [[ -f "$COUNT" ]]; then
  echo ""
  echo "══════════════════════════════════════════════════"
  echo "  STEP 4 — Sub counts + inventory update"
  echo "══════════════════════════════════════════════════"
  # shellcheck disable=SC2086  # STEP_FLAGS is intentionally split (empty or --dry-run)
  python3 "$COUNT" "$DST" --update-inventory $STEP_FLAGS
else
  echo "⚠️   count_subs.py not found at $COUNT — skipping count step"
fi

echo ""
echo "══════════════════════════════════════════════════"
if [[ $DRY_RUN -eq 1 ]]; then
  echo "  DRY RUN COMPLETE — nothing was changed"
else
  echo "  ALL DONE"
fi
echo "══════════════════════════════════════════════════"
echo ""
