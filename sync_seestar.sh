#!/usr/bin/env bash
# sync_seestar.sh
#
# Full Seestar sync pipeline (run from anywhere):
#   Step 1 — rsync FITS files from Seestar EMMC → NAS (skips JPGs)
#   Step 2 — rename _sub/_subs folders (remove spaces)
#   Step 3 — organize: move Lights_*.fits into lights/ subdirs
#   Step 4 — count subs + update AstroImages Inventory.md (vault + local copy)
#
# Usage:
#   ./sync_seestar.sh              # full pipeline
#   ./sync_seestar.sh --dry-run    # preview only, no changes
#   ./sync_seestar.sh --no-cleanup # sync only, skip steps 2-4
#   ./sync_seestar.sh -h           # this help
#
# Paths are loaded from seestar.conf (see seestar.conf.example).
# These are only used as a last-resort fallback if the conf is missing.
SRC=""
DST=""

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CLEANUP="$SCRIPT_DIR/cleanup_seestar.py"
RENAME="$SCRIPT_DIR/rename_seestar_folders.py"
COUNT="$SCRIPT_DIR/count_subs.py"
CONF="$SCRIPT_DIR/seestar.conf"

# Load seestar.conf if present, then map its keys to SRC/DST
if [[ -f "$CONF" ]]; then
  # shellcheck disable=SC1090
  source "$CONF"
  SRC="${SEESTAR_EMMC:-$SRC}"
  DST="${SEESTAR_NAS:-$DST}"
fi

# Platform-aware fallbacks if conf didn't set them
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
  echo "    Copy seestar.conf.example to seestar.conf and set SEESTAR_EMMC + SEESTAR_NAS."
  exit 1
fi

# ─────────────────────────────────────────────────────────
# Help
# ─────────────────────────────────────────────────────────
if [[ "$1" == "-h" || "$1" == "--help" ]]; then
  sed -n '2,12p' "$0" | sed 's/^# \{0,1\}//'
  exit 0
fi

# ─────────────────────────────────────────────────────────
# Parse flags
# ─────────────────────────────────────────────────────────
DRY_RUN=0
NO_CLEANUP=0
for arg in "$@"; do
  case "$arg" in
    --dry-run)    DRY_RUN=1 ;;
    --no-cleanup) NO_CLEANUP=1 ;;
  esac
done

RSYNC_FLAGS=(-av --progress --stats)
CLEANUP_FLAGS=""
if [[ $DRY_RUN -eq 1 ]]; then
  RSYNC_FLAGS+=( --dry-run)
  CLEANUP_FLAGS="--dry-run"
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
  echo "    Is the NAS mounted?"
  exit 1
fi

# ─────────────────────────────────────────────────────────
# Step 1: Rsync FITS only (exclude all JPGs)
# ─────────────────────────────────────────────────────────
echo "══════════════════════════════════════════════════"
echo "  STEP 1 — Sync FITS files to NAS (skip JPGs)"
echo "══════════════════════════════════════════════════"
echo "  SRC: $SRC"
echo "  DST: $DST"
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

# ─────────────────────────────────────────────────────────
# Step 2: Rename folders (remove spaces)
# ─────────────────────────────────────────────────────────
if [[ $NO_CLEANUP -eq 0 ]]; then
  if [[ -f "$RENAME" ]]; then
    echo ""
    echo "══════════════════════════════════════════════════"
    echo "  STEP 2 — Rename: remove spaces from folder names"
    echo "══════════════════════════════════════════════════"
    python3 "$RENAME" "$DST" $CLEANUP_FLAGS
  else
    echo "⚠️   rename_seestar_folders.py not found at $RENAME — skipping rename step"
  fi

# ─────────────────────────────────────────────────────────
# Step 3: Organize (move Lights_*.fits → lights/)
# ─────────────────────────────────────────────────────────
  if [[ -f "$CLEANUP" ]]; then
    echo ""
    echo "══════════════════════════════════════════════════"
    echo "  STEP 3 — Organize: move subs into lights/"
    echo "══════════════════════════════════════════════════"
    python3 "$CLEANUP" "$DST" $CLEANUP_FLAGS
  else
    echo "⚠️   cleanup_seestar.py not found at $CLEANUP — skipping organize step"
  fi

# ─────────────────────────────────────────────────────────
# Step 4: Count subs + update inventory
# ─────────────────────────────────────────────────────────
  if [[ -f "$COUNT" ]]; then
    echo ""
    echo "══════════════════════════════════════════════════"
    echo "  STEP 4 — Sub counts + inventory update"
    echo "══════════════════════════════════════════════════"
    python3 "$COUNT" "$DST" --update-inventory $CLEANUP_FLAGS
  else
    echo "⚠️   count_subs.py not found at $COUNT — skipping count step"
  fi
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
