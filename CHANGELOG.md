# Changelog

All notable changes to this project are documented here.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project aspires to [Semantic Versioning](https://semver.org/spec/v2.0.0.html)
once it reaches a tagged release.

## [Unreleased]

### Added
- **Safe destination control in `sync_seestar.sh`.** New `--src` / `--dst` (alias `--dest`) command-line overrides take precedence over `seestar.conf`. A real (non-dry-run) sync now prints the resolved `SRC → DST` and asks for confirmation before touching anything; `--yes` / `-y` skips the prompt for scripted runs, and an unattended run without `--yes` refuses rather than guessing. This closes the "wrong-folder sync" trap where a stale/duplicate config silently pointed at the wrong NAS path.
- **Exposure-sort wired into the pipeline.** `sync_seestar.sh` gained **Step 3b**: after organising into `lights/`, it runs `sort_by_exptime.py --all` so subs land in `<target>_sub/<exptime>s/lights/` — 10 s alt-az and 20 s/30 s EQ separated and Siril-ready — on every sync.
- **`seestar.conf.example`** — the config template the README and `.gitignore` referenced but that was missing from the repo. Copy it to the gitignored `seestar.conf`.
- **Config-driven inventory path.** `count_subs.py` gained `--inventory PATH`; the inventory location resolves `--inventory` → `SEESTAR_VAULT_INV` (exported from `seestar.conf` by `sync_seestar.sh`) → built-in default, instead of being hardcoded.
- **`purge_siril_cruft.py`** — reclaims archive space by deleting three disposable categories from `*_sub/` folders: Siril `process/` working dirs (regenerated on every re-stack), `Copy #N of …` Finder duplicates, and double-processed `starless_starless_*` / `starmask_starless_*` files. Dry-run by default (`--apply` to delete); idempotent; NAS keepalive. A `Copy #N of X` is removed only when its original `X` is present in the same folder, so orphaned copies are never the sole casualty. After deletion it hands surviving `lights/` folders to `sort_by_exptime.py`. First archive-wide dry-run reported ~769 GB / ~53,400 files reclaimable, ~99.99% of it `process/` scratch.
- **Cross-platform support.** Linux and Windows install/path notes throughout the README. Recommended Windows workflow is WSL2; native PowerShell users can invoke the Python scripts directly, only `sync_seestar.sh` is bash-bound.
- **Platform-aware Siril script discovery** in `batch_stack.py` — `SCRIPT_SEARCH_PATHS` now builds per-platform (macOS, Linux including flatpak, Windows incl. `%APPDATA%` and Program Files).
- **`seestar.conf` loading in `sync_seestar.sh`.** The script's comment previously claimed it loaded the conf but the code didn't actually source it — only the macOS `/Volumes` defaults were used. Now it sources `seestar.conf` if present, then falls back to platform-aware defaults via `uname -s`.
- **Unit test suite** (`tests/test_parsing.py`, 69 tests using stdlib `unittest` — no install required). Covers `canonical_target_name`, `is_processed`, `safe_dest`, `group_by_target`, `_EXPTIME_RE`, `format_duration`, `normalize`, `canonical_folder_name`, `deduplicate`, `new_name`, `merge_into_existing`, `STACKED_RE`, plus `sort_by_exptime.exptime_label` / `EXPTIME_DIR_RE` / `is_already_sorted` and `count_subs.resolve_inventory_path`.
- **GitHub Actions CI** (`.github/workflows/tests.yml`): matrix of macOS / Ubuntu / Windows × Python 3.10 / 3.12 / 3.13, plus a shellcheck job for `sync_seestar.sh`.
- **PR template** (`.github/pull_request_template.md`) and a Contributing / branch-strategy section in the README documenting topic-branch + squash-merge workflow and the recommended branch-protection toggles.
- **LICENSE** (MIT).
- **CHANGELOG.md** (this file).

### Fixed
- **Incremental syncs no longer strand new subs in space-named folders.** `rename_seestar_folders.py` previously *skipped* a rename when the canonical destination already existed (`M 51_sub` → `M_51_sub` where `M_51_sub` is already present), leaving the freshly-synced subs in the space-named folder; the later organize/sort steps never consolidated them, so each incremental sync grew a parallel `M 51_sub` / `M 57_sub` / `NGC 6946_sub` instead of merging into the canonical `<target>_sub/<exptime>s/lights/` tree. The rename step now **merges** the donor folder into the existing destination: every file is moved into the matching relative location under the destination (raw root subs land at the destination root, where Step 3/3b file them by exposure), deduped by filename against everything already in the destination so re-delivered subs don't double-count, and the emptied donor folder is removed. Dry-run-by-default and idempotent; the already-sorted `<exptime>s/lights/` subs are never touched. New `merge_into_existing()` helper, covered by 4 unit tests.
- **`canonical_target_name` no longer strips target catalog numbers.** The previous `_SESSION_SUFFIX_RE` had a bare `\d+` branch that matched the target's own catalog number once `_sub` was stripped, so `M 51_sub` returned `"M"` instead of `"M 51"`, collapsing every `M N` and `NGC N` folder into just its prefix letter. This broke the `--merge` feature in `organize_subs.py` (then named `cleanup_seestar.py`), which would incorrectly group multi-target archives together. Fix: removed the bare-digit branch, required a separator before remaining session markers, and added explicit handling for middle `_sub_N` / `_subs_N` suffixes so `M 51_sub_2 → M 51` works.

  **Impact:** if you had previously run `--merge` on an archive with multiple Messier or NGC targets, those merges may have moved subs into the wrong target folder. Recommend reviewing archives before re-running.
- **CSV writer encoding** in `count_subs.py` now explicitly sets `encoding="utf-8"`. Previously it defaulted to the platform locale (`cp1252` on Windows), which would mangle non-ASCII target names.

### Changed
- **Renamed two scripts for clarity** (they read as two "clean*" tools doing opposite things):
  `cleanup_seestar.py` → **`organize_subs.py`** (it *organises* subs into `lights/` and merges nights — it never deleted data), and
  `clean_seestar_archive.py` → **`purge_siril_cruft.py`** (it *purges* regenerable Siril `process/` scratch + duplicates).
- **`sort_by_exptime.py` now always sorts into `<exptime>s/lights/`**, even when a target has only one exposure length. Previously single-exposure targets were left in a flat `lights/`, producing an inconsistent on-disk schema (some targets sorted, some not). Re-runs stay idempotent via an explicit "already-sorted" guard (no `…/10s/10s/…` re-nesting), the now-empty source `lights/` is removed, and `astropy` is imported lazily so the module imports — and its pure helpers unit-test — without it.
- README restructured for cross-platform: per-OS config examples, per-OS Siril and Python install notes, rsync-on-Windows guidance, new Windows workflow section, Tests section, Contributing section.

## [0.1.0] — 2026-05-24

### Added
- Initial commit: full Seestar S50 sync pipeline.
- `sync_seestar.sh` — orchestrates rsync → rename → organise → count + inventory.
- `rename_seestar_folders.py` — removes spaces from `_sub`/`_subs` folder names.
- `cleanup_seestar.py` — moves raw FITS into `lights/`, merges multi-night sessions, removes empty folders.
- `count_subs.py` — counts subs per target, updates Obsidian inventory markdown.
- `batch_stack.py` — batch-runs Siril's `Seestar_Preprocessing` script across pending folders.
- `seestar.conf.example` config template, gitignored personal `seestar.conf`.

[Unreleased]: https://github.com/ehrlinger/seestar-tools/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/ehrlinger/seestar-tools/releases/tag/v0.1.0
