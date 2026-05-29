# Changelog

All notable changes to this project are documented here.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project aspires to [Semantic Versioning](https://semver.org/spec/v2.0.0.html)
once it reaches a tagged release.

## [Unreleased]

### Added
- **Cross-platform support.** Linux and Windows install/path notes throughout the README. Recommended Windows workflow is WSL2; native PowerShell users can invoke the Python scripts directly, only `sync_seestar.sh` is bash-bound.
- **Platform-aware Siril script discovery** in `batch_stack.py` — `SCRIPT_SEARCH_PATHS` now builds per-platform (macOS, Linux including flatpak, Windows incl. `%APPDATA%` and Program Files).
- **`seestar.conf` loading in `sync_seestar.sh`.** The script's comment previously claimed it loaded the conf but the code didn't actually source it — only the macOS `/Volumes` defaults were used. Now it sources `seestar.conf` if present, then falls back to platform-aware defaults via `uname -s`.
- **Unit test suite** (`tests/test_parsing.py`, 49 tests using stdlib `unittest` — no install required). Covers `canonical_target_name`, `is_processed`, `safe_dest`, `group_by_target`, `_EXPTIME_RE`, `format_duration`, `normalize`, `canonical_folder_name`, `deduplicate`, `new_name`, `STACKED_RE`.
- **GitHub Actions CI** (`.github/workflows/tests.yml`): matrix of macOS / Ubuntu / Windows × Python 3.10 / 3.12 / 3.13, plus a shellcheck job for `sync_seestar.sh`.
- **PR template** (`.github/pull_request_template.md`) and a Contributing / branch-strategy section in the README documenting topic-branch + squash-merge workflow and the recommended branch-protection toggles.
- **LICENSE** (MIT).
- **CHANGELOG.md** (this file).

### Fixed
- **`canonical_target_name` no longer strips target catalog numbers.** The previous `_SESSION_SUFFIX_RE` had a bare `\d+` branch that matched the target's own catalog number once `_sub` was stripped, so `M 51_sub` returned `"M"` instead of `"M 51"`, collapsing every `M N` and `NGC N` folder into just its prefix letter. This broke the `--merge` feature in `cleanup_seestar.py`, which would incorrectly group multi-target archives together. Fix: removed the bare-digit branch, required a separator before remaining session markers, and added explicit handling for middle `_sub_N` / `_subs_N` suffixes so `M 51_sub_2 → M 51` works.

  **Impact:** if you had previously run `cleanup_seestar.py --merge` on an archive with multiple Messier or NGC targets, those merges may have moved subs into the wrong target folder. Recommend reviewing archives before re-running.
- **CSV writer encoding** in `count_subs.py` now explicitly sets `encoding="utf-8"`. Previously it defaulted to the platform locale (`cp1252` on Windows), which would mangle non-ASCII target names.

### Changed
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
