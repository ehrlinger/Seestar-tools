# prune_seestar.py — delete Seestar subs already archived on the NAS

**Date:** 2026-06-25
**Status:** Approved (design)

## Purpose

Free space on the Seestar S50 EMMC by deleting FITS subs that are safely
archived on the NAS. This is the inverse-safety companion to `sync_seestar.sh`:
it deletes *from* the device, so it errs hard toward caution. A sub is removed
only when an identical copy (name **and** byte size) is confirmed under its
archived target folder on the NAS.

## Why name+size matching (not same-path)

The sync pipeline reorganizes what comes off the Seestar, so a same-path check
is wrong. Two transforms apply between EMMC and NAS:

1. **Folder rename** — `rename_seestar_folders.new_name()` squashes spaces:
   `M 51_sub` → `M_51_sub`. (The docstring examples in that file are stale; the
   code is `folder_name.strip().replace(" ", "_")` and is the source of truth.)
2. **Sub reorg** — `organize_subs.py` + `sort_by_exptime.py` move raw subs from
   the folder root into `lights/`, then optionally into `20s/lights/`,
   `altaz/lights/`, etc.

So a sub at `M 51_sub/Light_….fit` on the EMMC may live at
`M_51_sub/20s/lights/Light_….fit` on the NAS. Matching by **filename + byte
size anywhere under the archived target subtree** is robust to both transforms.
This is the same dedupe key `rename_seestar_folders.merge_into_existing()`
already relies on: Seestar filenames embed a second-resolution timestamp, so a
name+size match is the same frame re-delivered.

## CLI and path resolution

Mirrors `restore_to_seestar.py`:

- Reads `SEESTAR_EMMC` / `SEESTAR_NAS` from `seestar.conf` (same loader pattern).
- `--emmc PATH` / `--nas PATH` override the config.
- Optional positional `targets` — one or more EMMC folder names to limit the
  run. Default: all `_sub`/`_subs` folders found on the EMMC.
- `--dry-run` is the **default**. Nothing is deleted unless `--execute` is given.
- `--execute` performs the deletions.
- On a real (`--execute`) run, print the resolved EMMC/NAS paths and ask for
  confirmation. `--yes` skips the prompt. Refuse to run unattended (stdin not a
  TTY) without `--yes` — same guard as `sync_seestar.sh`.
- `-h` / `--help` prints the module docstring.

## Matching algorithm

For each EMMC target folder (top-level `_sub`/`_subs` dir, excluding
`_trash`/`scripts` via `seestar_common.is_in_excluded`):

1. Compute the canonical archived name via
   `rename_seestar_folders.new_name(folder.name)`.
2. Locate `NAS/<canonical>`. **If it does not exist, skip the entire target** —
   no archive means nothing is safe to delete. Report it as skipped.
3. Build an index over the NAS target's subtree:
   `{filename: set(sizes)}` for every FITS file (`rglob`). This collapses the
   `lights/` / `<exp>s/lights/` reorg.
4. For each FITS sub directly in the EMMC target folder root:
   - If its `(name, size)` is present in the index → **eligible for deletion**.
   - Otherwise → **kept**, and reported as not-yet-on-NAS.

Notes:
- FITS = `.fit` / `.fits` (case-insensitive), excluding `._*` / `.DS_Store`,
  reusing the `is_fits` helper convention from `restore_to_seestar.py`.
- Only subs at the **EMMC folder root** are candidates — that is native Seestar
  layout. (The EMMC is never reorganized by these tools.)
- Per-sub JPG previews are out of scope and never touched.

## Deletion and empty-dir pruning

- In dry-run, print what *would* be deleted; touch nothing.
- In `--execute`, `unlink()` each eligible sub.
- After processing a target, if it holds **no FITS** (ignoring `.DS_Store` /
  `._*` noise), remove the folder. Reuse the swallow-`EBUSY`/`ENOTEMPTY`/
  `EPERM`/`EACCES` pattern from `rename_seestar_folders._safe_rmtree_donor`
  (with the `.smbdelete*` tombstone report) so a locked NAS/SMB handle reports
  rather than aborting the run.

## Reporting

Per target:
- canonical NAS name and whether it was found / skipped,
- count deleted (or would-delete), count kept-unmatched, bytes freed,
- an explicit list of subs present on the EMMC but **not** confirmed on the NAS
  (these are never deleted — they flag what still needs a sync).

Final summary across all targets: totals for deleted, kept, skipped targets,
and total bytes freed. Dry-run banner clearly distinguishes preview from real.

## Module structure

Single script `prune_seestar.py` at the repo root, alongside the other tools.
Pure-function core for testability:

- `load_conf()` — reuse the `restore_to_seestar.py` pattern (or import it).
- `is_fits(path)` — FITS suffix + skip-prefix check.
- `index_nas_target(nas_target_dir) -> dict[str, set[int]]` — name→sizes index.
- `find_emmc_targets(emmc_root) -> list[Path]` — top-level `_sub`/`_subs` dirs,
  excluding `_trash`/`scripts`.
- `eligible_subs(emmc_target, index) -> tuple[list[Path], list[Path]]` —
  returns `(to_delete, kept_unmatched)`. Pure given the index.
- `is_effectively_empty(dir) -> bool` — no FITS ignoring noise files.
- `prune(...)` — orchestration, dry-run aware.
- `main()` — arg parsing, conf, confirmation prompt.

Reuse, do not duplicate: import `new_name` from `rename_seestar_folders` and
`is_in_excluded` from `seestar_common`. Factor the SMB-safe rmtree helper so it
is shared rather than copied (extract to `seestar_common` if cleanest).

## Testing

Add `tests/` coverage in the repo's pytest style (tmp_path fixtures):

- name+size match → deleted; name match but **size differs** → kept.
- name not on NAS → kept and reported.
- missing NAS target folder → whole target skipped, nothing deleted.
- reorg case: sub at EMMC root, copy under NAS `<exp>s/lights/` → matched.
- dry-run (default) deletes nothing even when matches exist.
- empty-dir pruning removes a folder once its FITS are gone.
- a folder containing only `.DS_Store`/`._*` is treated as empty.
- `_trash`/`scripts` targets are excluded.

## Out of scope (YAGNI)

- Content-hash verification (name+size chosen as the safety/speed balance).
- Deleting per-sub JPGs or whole-folder blow-away.
- Touching the NAS side in any way (read-only there).
