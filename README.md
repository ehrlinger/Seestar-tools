# seestar-tools

Data management pipeline for the ZWO Seestar S50 smart telescope. Syncs FITS files from the scope to a NAS, organises folder structure, batch-stacks with Siril, and keeps an Obsidian inventory up to date.

> **GitHub:** [github.com/ehrlinger/seestar-tools](https://github.com/ehrlinger/seestar-tools)

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

   Edit `seestar.conf` with paths appropriate for your OS:

   **macOS:**
   ```bash
   SEESTAR_EMMC="/Volumes/EMMC Images/MyWorks/"
   SEESTAR_NAS="/Volumes/YourNAS/Seestar/"
   SEESTAR_VAULT_INV="~/path/to/Obsidian/Astrophotography/AstroImages Inventory.md"
   ```

   **Linux:**
   ```bash
   SEESTAR_EMMC="/media/$USER/EMMC Images/MyWorks/"
   SEESTAR_NAS="/mnt/nas/Seestar/"
   SEESTAR_VAULT_INV="$HOME/Obsidian/Astrophotography/AstroImages Inventory.md"
   ```

   **Windows (WSL or git-bash):** use paths as your shell sees them — `/mnt/c/...` under WSL, `/c/...` under git-bash.

2. **Add Siril CLI to your PATH:**

   | OS | Path |
   |----|------|
   | macOS | `/Applications/Siril.app/Contents/MacOS` |
   | Linux | already on PATH if installed via package manager; flatpak users wrap with `flatpak run org.siril.Siril` |
   | Windows | `C:\Program Files\Siril\bin` |

   On macOS, append (don't prepend) so Siril's bundled Python doesn't shadow your system Python.

3. **Python 3.10+** required.
   - macOS: `brew install python` (Siril's bundled Python is killed by macOS when spawned from Terminal — don't use it)
   - Linux: usually preinstalled, or `apt install python3` / `dnf install python3`
   - Windows: from [python.org](https://www.python.org/) or the Microsoft Store

4. **rsync** for the sync step.
   - macOS & Linux: ships with the OS
   - Windows: install via WSL (recommended), git-bash, or [cwRsync](https://itefix.net/cwrsync)

## Windows workflow (WSL recommended)

`sync_seestar.sh` is a bash script and needs a Unix-like shell. The recommended path on Windows is **WSL2** (Windows Subsystem for Linux):

```powershell
# In PowerShell, as admin once:
wsl --install -d Ubuntu
```

Then inside the Ubuntu WSL shell, treat it like any Linux setup (steps 1-4 above). Your Windows drives appear under `/mnt/c`, `/mnt/d`, etc. — point `SEESTAR_EMMC` and `SEESTAR_NAS` at those.

**Alternative shells:**
- **git-bash** runs the .sh script fine, but doesn't ship `rsync` — install it via MSYS2 (`pacman -S rsync`) or use [cwRsync](https://itefix.net/cwrsync).
- **PowerShell / cmd.exe**: the Python scripts (`cleanup_seestar.py`, `count_subs.py`, `rename_seestar_folders.py`, `batch_stack.py`) work fine when invoked directly with `python` — only the orchestrating `sync_seestar.sh` wrapper is bash-bound.

**Caveats on native Windows:**
- Terminal output uses emoji (✅ ❌ ⚠️ 📁). Modern Windows Terminal and PowerShell 7 render them fine; legacy `cmd.exe` may show boxes.
- `siril-cli` is found via `PATH` — make sure `C:\Program Files\Siril\bin` (or wherever you installed it) is on `PATH`.
- The Linux/Windows code paths in `batch_stack.py` are written to spec but **not exercised in CI** — please open an issue if Siril script discovery fails on your install.

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
python3 batch_stack.py /Volumes/YourNAS/Seestar/

# Preview only
python3 batch_stack.py /Volumes/YourNAS/Seestar/ --dry-run

# Single target
python3 batch_stack.py /Volumes/YourNAS/Seestar/ "M 51"
```

**NAS note:** Run scripts from local disk (this repo or your vault), passing the NAS path as an argument. macOS kills Python when the script file itself lives on a network volume.

## Individual scripts

```bash
# Rename folders (remove spaces)
python3 rename_seestar_folders.py /Volumes/YourNAS/Seestar/

# Organise + merge multi-night sessions
python3 cleanup_seestar.py /Volumes/YourNAS/Seestar/ --merge

# Count subs and update inventory
python3 count_subs.py /Volumes/YourNAS/Seestar/ --update-inventory
```

## Tests

The pure parsing/grouping functions have unit tests using the stdlib (no extra install needed):

```bash
python3 -m unittest discover -s tests -v
```

CI runs this suite on every push and PR across **macOS, Ubuntu, Windows × Python 3.10, 3.12, 3.13** ([`.github/workflows/tests.yml`](.github/workflows/tests.yml)). A separate job shellchecks `sync_seestar.sh` on Ubuntu.

## Contributing / branch strategy

This is a personal repo but the workflow is structured so future-me (and any AI agent) has guardrails:

1. **Never commit directly to `main`.** Create a topic branch:
   ```bash
   git checkout -b fix/<short-description>
   ```
2. **Push and open a PR.** The PR template asks for a summary, what testing was done, and rollback notes for risky changes.
3. **Wait for green CI.** All 10 matrix cells (9 unittest + 1 shellcheck) must pass before merge.
4. **Squash-merge** to keep `main` history linear and each PR as one commit.
5. **Add a [CHANGELOG.md](CHANGELOG.md) entry** under `## [Unreleased]` for any user-visible change. One line per change, grouped under Added / Changed / Fixed / Removed.

### Recommended branch protection (one-time setup)

In GitHub: **Settings → Branches → Add branch ruleset** for `main`:

- ✅ Require a pull request before merging
- ✅ Require status checks to pass — select `unittest` and `shellcheck`
- ✅ Require branches to be up to date before merging
- ✅ Require linear history (matches the squash-merge convention)
- ✅ Do not allow bypassing the above settings *(uncheck if you want admin override for personal use)*

For data-touching scripts (`cleanup_seestar.py --merge`, `batch_stack.py`), always run with `--dry-run` against real archives before merging the PR — tests cover parsing, not filesystem semantics.

## Requirements

- macOS, Linux, or Windows (see Setup above for per-OS notes)
- [Siril 1.4+](https://siril.org) with `Seestar_Preprocessing` script enabled
- Python 3.10+
- rsync (preinstalled on macOS/Linux; WSL or cwRsync on Windows)

Tested primarily on macOS Sonoma; Linux/Windows support is path-aware but not as thoroughly exercised — please open an issue if something breaks.

## License

[MIT](LICENSE) — see the LICENSE file for full text.

## Changelog

See [CHANGELOG.md](CHANGELOG.md) for the history of user-visible changes.
