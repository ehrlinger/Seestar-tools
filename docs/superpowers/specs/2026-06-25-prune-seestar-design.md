# prune_seestar.py — delete Seestar subs already archived on the NAS

**Date:** 2026-06-25
**Status:** Approved (design)

## Purpose

Free space on the Seestar S50 EMMC by deleting FITS subs that are safely
archived on the NAS, along with each deleted sub's per-sub JPG preview. This is
the inverse-safety companion to `sync_seestar.sh`: it deletes *from* the device,
so it errs hard toward caution. A sub is removed only when an identical copy
(name **and** byte size) is confirmed under its archived target folder on the
NAS. A JPG preview is removed only when its sibling `.fit` sub is removed.

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

## Per-sub JPG previews (ride along with their data)

Per-sub JPGs are *never* on the NAS — the sync explicitly excludes
`*_sub/**.jpg`. They therefore cannot be matched against the NAS the way FITS
are. Instead, a JPG is deleted **only when its sibling `.fit` sub is deleted**
(i.e. that sub was confirmed on the NAS). Previews thus leave the device exactly
as their data does; a sub still waiting to sync keeps its preview.

For each deleted sub with stem `S` (filename minus the FITS suffix), delete,
within the same target folder:

- `S.jpg` / `S.jpeg` (case-insensitive) — the full-size preview, and
- `S_thn.jpg` / `S_thn.jpeg` — the Seestar thumbnail, if present.

Match by stem so a preview is only ever removed alongside the specific sub it
belongs to. JPGs whose `.fit` was kept (not on NAS, or size mismatch) are left
untouched. `.DS_Store` / `._*` are never JPG candidates.

## Deletion and empty-dir pruning

- In dry-run, print what *would* be deleted (subs and their JPGs); touch nothing.
- In `--execute`, `unlink()` each eligible sub and its matched JPG previews.
- After processing a target, if it holds **no real files at all** — ignoring
  only `.DS_Store` / `._*` macOS noise — remove the folder. A kept sub, or a
  stray/orphan JPG, leaves a real file behind and blocks pruning. Reuse the
  swallow-`EBUSY`/`ENOTEMPTY`/
  `EPERM`/`EACCES` pattern from `rename_seestar_folders._safe_rmtree_donor`
  (with the `.smbdelete*` tombstone report) so a locked NAS/SMB handle reports
  rather than aborting the run.

## Reporting

Per target:
- canonical NAS name and whether it was found / skipped,
- count deleted (or would-delete) subs and JPG previews, count kept-unmatched,
  bytes freed (subs + JPGs combined),
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
- `sibling_jpgs(sub_path) -> list[Path]` — `S.jpg`/`S.jpeg`/`S_thn.jpg`/
  `S_thn.jpeg` that exist for a sub of stem `S`. Pure given the filesystem.
- `is_effectively_empty(dir) -> bool` — no real files, ignoring only
  `.DS_Store` / `._*`.
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
- deleting a matched sub also deletes its `S.jpg` and `S_thn.jpg` siblings.
- a kept (unmatched) sub keeps its JPG preview.
- a folder with a leftover/orphan JPG is NOT pruned (real file remains).
- empty-dir pruning removes a folder once all real files are gone.
- a folder containing only `.DS_Store`/`._*` is treated as empty.
- `_trash`/`scripts` targets are excluded.

## Out of scope (YAGNI)

- Content-hash verification (name+size chosen as the safety/speed balance).
- Deleting unmatched JPGs (a JPG only goes when its sub does) or whole-folder
  blow-away.
- Touching the NAS side in any way (read-only there).
