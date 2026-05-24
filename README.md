# seestar-tools

Data management pipeline for the ZWO Seestar S50 smart telescope. Syncs FITS files from the scope to a NAS, organises folder structure, batch-stacks with Siril, and keeps an Obsidian inventory up to date.

## Scripts

| Script | What it does |
|--------|-------------|
| `sync_seestar.sh` | Full pipeline: rsync → rename → organise → count + inventory update |
| `rename_seestar_folders.py` | Removes spaces from `_sub`/`_subs` folder names |
| `cleanup_seestar.py` | Moves raw FITS into `lights/`, merges multi-night sessions, removes empties |
| `count_subs.py` | Counts subs per target, updates Obsidian inventory markdown |
| `batch_stack.py` | Batch-runs Siril's `Seestar_Preprocessing` script across all pending folders |

## Setup

1. **Copy the config template and fill in your paths:**

   ```bash
   cp seestar.conf.example seestar.conf
   ```

   Edit `seestar.conf`:
   ```bash
   SEESTAR_EMMC="/Volumes/EMMC Images/MyWorks/"
   SEESTAR_NAS="/Volumes/personal_folder/Seestar/"
   SEESTAR_VAULT_INV="~/path/to/your/Obsidian/AstroImages Inventory.md"
   ```

2. **Add Siril CLI to your PATH** (at the end so it doesn't shadow system Python):

   In `~/.zshrc`:
   ```bash
   export PATH="$PATH:/Applications/Siril.app/Contents/MacOS"
   ```

3. **Use Homebrew Python**, not Siril's bundled Python (macOS will kill the latter when spawned from Terminal):

   ```bash
   brew install python
   ```

## Full pipeline

```bash
# Run everything: sync → rename → organise → count + inventory
./sync_seestar.sh

# Preview without making changes
./sync_seestar.sh --dry-run

# Sync only, skip organise/count steps
./sync_seestar.sh --no-cleanup
```

## Batch stacking

Requires `siril-cli` on PATH. Skips folders that already have a current stack, restacks folders where new subs have been added.

```bash
# Stack all pending folders
python3 batch_stack.py /Volumes/personal_folder/Seestar/

# Preview only
python3 batch_stack.py /Volumes/personal_folder/Seestar/ --dry-run

# Single target
python3 batch_stack.py /Volumes/personal_folder/Seestar/ "M 51"
```

**NAS note:** Run scripts from local disk (this repo or your vault), passing the NAS path as an argument. macOS kills Python when the script file itself lives on a network volume.

## Individual scripts

```bash
# Rename folders (remove spaces)
python3 rename_seestar_folders.py /Volumes/personal_folder/Seestar/

# Organise + merge multi-night sessions
python3 cleanup_seestar.py /Volumes/personal_folder/Seestar/ --merge

# Count subs and update inventory
python3 count_subs.py /Volumes/personal_folder/Seestar/ --update-inventory
```

## Requirements

- macOS (tested on macOS Sonoma)
- [Siril 1.4+](https://siril.org) with `Seestar_Preprocessing` script enabled
- Python 3.10+ (Homebrew: `brew install python`)
- rsync (ships with macOS)
