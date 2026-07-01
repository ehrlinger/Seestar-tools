# prune_seestar.py Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add `prune_seestar.py`, a tool that deletes Seestar EMMC FITS subs (and their matched per-sub JPG previews) once they are confirmed on the NAS, freeing device space safely.

**Architecture:** A single root-level script of small pure functions plus a `prune()` orchestrator and `main()`. Matching is name+size against an index of the archived target's subtree on the NAS, so it is robust to the sync pipeline's folder-rename and `lights/` reorg. Dry-run is the default; deletion requires `--execute`. It reuses `new_name` (rename_seestar_folders) and `is_in_excluded` (seestar_common) and follows `restore_to_seestar.py` for config/CLI.

**Tech Stack:** Python 3.10+, stdlib only (`argparse`, `pathlib`, `shutil`, `errno`), `unittest` for tests (`python3 -m unittest discover -s tests -v`).

---

## File Structure

- Create: `prune_seestar.py` (repo root) — the whole tool.
- Modify: `tests/test_parsing.py` — add `import prune_seestar` and new TestCase classes (the repo keeps all unit tests in this one file).
- Read for reference (do not modify): `restore_to_seestar.py` (conf loader, `is_fits`, CLI/confirm pattern), `rename_seestar_folders.py` (`new_name`, `_safe_rmtree_donor` SMB-swallow pattern), `seestar_common.py` (`is_in_excluded`), `sync_seestar.sh` (unattended `! -t 0` guard).

Public surface of `prune_seestar.py`:

```python
FITS_SUFFIXES = {".fit", ".fits"}
JPG_SUFFIXES  = {".jpg", ".jpeg"}
SKIP_PREFIXES = ("._", ".DS_Store")

def is_fits(path: Path) -> bool: ...
def index_nas_target(nas_target_dir: Path) -> dict[str, set[int]]: ...
def find_emmc_targets(emmc_root: Path) -> list[Path]: ...
def eligible_subs(emmc_target: Path, index: dict[str, set[int]]) -> tuple[list[Path], list[Path]]: ...
def sibling_jpgs(sub_path: Path) -> list[Path]: ...
def is_effectively_empty(directory: Path) -> bool: ...
def prune_empty_dir(directory: Path, dry_run: bool) -> bool: ...
def prune(emmc: Path, nas: Path, targets: list[str] | None, dry_run: bool) -> dict: ...
def main() -> None: ...
```

---

### Task 1: Scaffold the module (docstring, imports, constants, helpers reuse)

**Files:**
- Create: `prune_seestar.py`
- Test: `tests/test_parsing.py` (add import)

- [ ] **Step 1: Write the failing test**

Add to `tests/test_parsing.py`, in the import block (after `import inapp_inventory`):

```python
import prune_seestar
```

And add this TestCase class near the end of the file (before the `if __name__` block):

```python
class PruneImportTests(unittest.TestCase):
    """prune_seestar exposes the expected constants and reuses shared helpers."""

    def test_fits_suffixes(self):
        self.assertEqual(prune_seestar.FITS_SUFFIXES, {".fit", ".fits"})

    def test_jpg_suffixes(self):
        self.assertEqual(prune_seestar.JPG_SUFFIXES, {".jpg", ".jpeg"})

    def test_reuses_new_name(self):
        # forward EMMC->NAS folder transform is the rename script's new_name
        self.assertIs(prune_seestar.new_name, rename_seestar_folders.new_name)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m unittest tests.test_parsing -v 2>&1 | head -30`
Expected: FAIL — `ModuleNotFoundError: No module named 'prune_seestar'`.

- [ ] **Step 3: Create the module skeleton**

Create `prune_seestar.py`:

```python
#!/usr/bin/env python3
"""
prune_seestar.py  —  Delete Seestar EMMC subs already archived on the NAS.

The inverse-safety companion to sync_seestar.sh. It deletes FITS subs *from the
device*, so it errs hard toward caution: a sub is removed only when an identical
copy (same filename AND byte size) is confirmed under its archived target folder
on the NAS. Each deleted sub's per-sub JPG preview (and *_thn.jpg thumbnail) is
removed alongside it — those JPGs are never on the NAS (the sync excludes them),
so they ride along with the data they preview, not a NAS match.

Matching is name+size, not same-path, because the sync pipeline reorganizes
what comes off the scope:
    "M 51_sub"  (EMMC, spaces, subs at folder root)
        -> "M_51_sub/20s/lights/Light_*.fit"  (NAS, renamed + sorted)
The EMMC folder maps to the NAS folder via rename_seestar_folders.new_name();
the sub is matched by name+size anywhere under that NAS subtree.

USAGE
-----
    # Dry-run is the DEFAULT — preview only, nothing deleted:
    python3 prune_seestar.py

    # Actually delete (asks for confirmation first):
    python3 prune_seestar.py --execute

    # Limit to specific target folder(s) on the EMMC:
    python3 prune_seestar.py --execute "M 51_sub" "NGC 7000_sub"

    # Override paths (else from seestar.conf):
    python3 prune_seestar.py --emmc "/Volumes/EMMC Images/MyWorks/" \\
                             --nas  "/Volumes/personal_folder/Seestar/"

    # Skip the confirmation prompt (scripted runs):
    python3 prune_seestar.py --execute --yes

NOTES
-----
    • Dry-run by default; --execute required to delete anything.
    • Never touches the NAS (read-only there).
    • Per-sub JPG removed only when its sibling .fit is removed.
    • Empty target folders are pruned; a kept sub or stray JPG blocks pruning.
    • macOS ._ / .DS_Store files are ignored throughout.
"""

import argparse
import errno
import sys
from pathlib import Path

from rename_seestar_folders import new_name
from seestar_common import is_in_excluded

# ── constants ───────────────────────────────────────────────────────────────
FITS_SUFFIXES = {".fit", ".fits"}
JPG_SUFFIXES = {".jpg", ".jpeg"}
SKIP_PREFIXES = ("._", ".DS_Store")     # macOS resource-fork noise

DEFAULT_CONF_KEYS = {
    "SEESTAR_EMMC": "/Volumes/EMMC Images/MyWorks/",
    "SEESTAR_NAS":  "/Volumes/personal_folder/Seestar/",
}


def load_conf() -> dict:
    """Parse KEY="value" lines from seestar.conf next to this script (if present)."""
    conf = dict(DEFAULT_CONF_KEYS)
    conf_path = Path(__file__).resolve().parent / "seestar.conf"
    if conf_path.exists():
        for line in conf_path.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, val = line.partition("=")
            conf[key.strip()] = val.strip().strip('"').strip("'")
    return conf
```

> Note: `load_conf` is duplicated from `restore_to_seestar.py` deliberately —
> that file's copy is identical and importing it would run an unrelated CLI
> module. Keeping a local copy matches how the repo's other scripts each carry
> their own small conf loader.

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m unittest tests.test_parsing.PruneImportTests -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add prune_seestar.py tests/test_parsing.py
git commit -m "feat: scaffold prune_seestar.py (constants, conf, shared-helper reuse)"
```

---

### Task 2: `is_fits` and `index_nas_target`

**Files:**
- Modify: `prune_seestar.py`
- Test: `tests/test_parsing.py`

- [ ] **Step 1: Write the failing test**

Add this TestCase to `tests/test_parsing.py`:

```python
class PruneIndexTests(unittest.TestCase):
    """is_fits + index_nas_target build the name->sizes match index."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _touch(self, rel, size=10):
        p = self.tmp / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"x" * size)
        return p

    def test_is_fits_true_for_fit_and_fits(self):
        self.assertTrue(prune_seestar.is_fits(Path("Light_M51.fit")))
        self.assertTrue(prune_seestar.is_fits(Path("Light_M51.FITS")))

    def test_is_fits_false_for_jpg_and_noise(self):
        self.assertFalse(prune_seestar.is_fits(Path("Light_M51.jpg")))
        self.assertFalse(prune_seestar.is_fits(Path("._Light_M51.fit")))

    def test_index_collapses_reorg_subtree(self):
        self._touch("M_51_sub/20s/lights/Light_a.fit", size=100)
        self._touch("M_51_sub/lights/Light_b.fit", size=200)
        self._touch("M_51_sub/Stacked_master.jpg", size=999)  # not FITS
        idx = prune_seestar.index_nas_target(self.tmp / "M_51_sub")
        self.assertEqual(idx, {"Light_a.fit": {100}, "Light_b.fit": {200}})

    def test_index_records_multiple_sizes_for_same_name(self):
        self._touch("T_sub/lights/Light_a.fit", size=100)
        self._touch("T_sub/20s/lights/Light_a.fit", size=101)
        idx = prune_seestar.index_nas_target(self.tmp / "T_sub")
        self.assertEqual(idx, {"Light_a.fit": {100, 101}})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m unittest tests.test_parsing.PruneIndexTests -v`
Expected: FAIL — `AttributeError: module 'prune_seestar' has no attribute 'is_fits'`.

- [ ] **Step 3: Implement**

Append to `prune_seestar.py`:

```python
def is_fits(path: Path) -> bool:
    """True for a .fit/.fits file that is not macOS resource-fork noise."""
    return (path.suffix.lower() in FITS_SUFFIXES
            and not path.name.startswith(SKIP_PREFIXES))


def index_nas_target(nas_target_dir: Path) -> dict[str, set[int]]:
    """Map FITS basename -> set of byte sizes seen anywhere under the target.

    Collapses the archive's lights/ and <exp>s/lights/ reorg so an EMMC sub at
    the folder root can be matched wherever its NAS copy was filed. Same key as
    rename_seestar_folders.merge_into_existing's dedupe.
    """
    index: dict[str, set[int]] = {}
    for p in nas_target_dir.rglob("*"):
        if p.is_file() and is_fits(p):
            try:
                index.setdefault(p.name, set()).add(p.stat().st_size)
            except OSError:
                continue
    return index
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m unittest tests.test_parsing.PruneIndexTests -v`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add prune_seestar.py tests/test_parsing.py
git commit -m "feat: is_fits + index_nas_target (name+size match index)"
```

---

### Task 3: `find_emmc_targets`

**Files:**
- Modify: `prune_seestar.py`
- Test: `tests/test_parsing.py`

- [ ] **Step 1: Write the failing test**

Add this TestCase:

```python
class PruneFindTargetsTests(unittest.TestCase):
    """find_emmc_targets returns top-level _sub/_subs dirs, minus excluded."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _mkdir(self, rel):
        d = self.tmp / rel
        d.mkdir(parents=True, exist_ok=True)
        return d

    def test_returns_sub_and_subs_dirs(self):
        self._mkdir("M 51_sub")
        self._mkdir("NGC 7000_subs")
        names = sorted(p.name for p in prune_seestar.find_emmc_targets(self.tmp))
        self.assertEqual(names, ["M 51_sub", "NGC 7000_subs"])

    def test_ignores_non_sub_dirs_and_files(self):
        self._mkdir("random_folder")
        (self.tmp / "loose.fit").write_bytes(b"x")
        self.assertEqual(prune_seestar.find_emmc_targets(self.tmp), [])

    def test_excludes_trash_and_scripts(self):
        self._mkdir("_trash/M 51_sub")
        self._mkdir("scripts/Foo_sub")
        self.assertEqual(prune_seestar.find_emmc_targets(self.tmp), [])
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m unittest tests.test_parsing.PruneFindTargetsTests -v`
Expected: FAIL — `AttributeError: ... has no attribute 'find_emmc_targets'`.

- [ ] **Step 3: Implement**

Append to `prune_seestar.py`:

```python
def find_emmc_targets(emmc_root: Path) -> list[Path]:
    """Top-level _sub/_subs folders on the EMMC, excluding _trash/scripts.

    The EMMC holds targets in native layout directly under MyWorks, so only the
    immediate children are scanned (the device is never reorganized).
    """
    out = []
    for p in sorted(emmc_root.iterdir()):
        if (p.is_dir()
                and (p.name.endswith("_sub") or p.name.endswith("_subs"))
                and not is_in_excluded(p, emmc_root)):
            out.append(p)
    return out
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m unittest tests.test_parsing.PruneFindTargetsTests -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add prune_seestar.py tests/test_parsing.py
git commit -m "feat: find_emmc_targets (top-level _sub dirs, excludes _trash/scripts)"
```

---

### Task 4: `eligible_subs` (the name+size match decision)

**Files:**
- Modify: `prune_seestar.py`
- Test: `tests/test_parsing.py`

- [ ] **Step 1: Write the failing test**

Add this TestCase:

```python
class PruneEligibleSubsTests(unittest.TestCase):
    """eligible_subs splits root subs into (to_delete, kept_unmatched)."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.target = self.tmp / "M 51_sub"
        self.target.mkdir(parents=True)

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _sub(self, name, size=10):
        p = self.target / name
        p.write_bytes(b"x" * size)
        return p

    def test_name_and_size_match_is_deletable(self):
        self._sub("Light_a.fit", size=100)
        index = {"Light_a.fit": {100}}
        to_delete, kept = prune_seestar.eligible_subs(self.target, index)
        self.assertEqual([p.name for p in to_delete], ["Light_a.fit"])
        self.assertEqual(kept, [])

    def test_size_mismatch_is_kept(self):
        self._sub("Light_a.fit", size=100)
        index = {"Light_a.fit": {999}}  # different size
        to_delete, kept = prune_seestar.eligible_subs(self.target, index)
        self.assertEqual(to_delete, [])
        self.assertEqual([p.name for p in kept], ["Light_a.fit"])

    def test_name_absent_is_kept(self):
        self._sub("Light_b.fit", size=100)
        to_delete, kept = prune_seestar.eligible_subs(self.target, {})
        self.assertEqual(to_delete, [])
        self.assertEqual([p.name for p in kept], ["Light_b.fit"])

    def test_only_root_fits_considered_not_jpgs_or_noise(self):
        self._sub("Light_a.fit", size=100)
        self._sub("Light_a.jpg", size=100)        # preview, not a candidate
        self._sub("._Light_a.fit", size=100)      # macOS noise
        index = {"Light_a.fit": {100}, "Light_a.jpg": {100}}
        to_delete, kept = prune_seestar.eligible_subs(self.target, index)
        self.assertEqual([p.name for p in to_delete], ["Light_a.fit"])
        self.assertEqual(kept, [])
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m unittest tests.test_parsing.PruneEligibleSubsTests -v`
Expected: FAIL — `AttributeError: ... has no attribute 'eligible_subs'`.

- [ ] **Step 3: Implement**

Append to `prune_seestar.py`:

```python
def eligible_subs(emmc_target: Path,
                  index: dict[str, set[int]]) -> tuple[list[Path], list[Path]]:
    """Split the target's root FITS subs into (to_delete, kept_unmatched).

    A sub is deletable iff a file of the same name AND identical byte size is
    present in *index* (built from the NAS target subtree). Only files directly
    in the target root are considered — that is native Seestar layout. Returns
    sorted lists; pure given the index (apart from reading each sub's size).
    """
    to_delete: list[Path] = []
    kept: list[Path] = []
    for p in sorted(emmc_target.iterdir()):
        if not (p.is_file() and is_fits(p)):
            continue
        try:
            size = p.stat().st_size
        except OSError:
            size = -1
        if size in index.get(p.name, set()):
            to_delete.append(p)
        else:
            kept.append(p)
    return to_delete, kept
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m unittest tests.test_parsing.PruneEligibleSubsTests -v`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add prune_seestar.py tests/test_parsing.py
git commit -m "feat: eligible_subs (name+size match -> to_delete/kept)"
```

---

### Task 5: `sibling_jpgs` (previews that ride along)

**Files:**
- Modify: `prune_seestar.py`
- Test: `tests/test_parsing.py`

- [ ] **Step 1: Write the failing test**

Add this TestCase:

```python
class PruneSiblingJpgsTests(unittest.TestCase):
    """sibling_jpgs finds the preview + thumbnail for a given sub."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _touch(self, name):
        p = self.tmp / name
        p.write_bytes(b"x")
        return p

    def test_finds_preview_and_thumbnail(self):
        sub = self._touch("Light_a.fit")
        self._touch("Light_a.jpg")
        self._touch("Light_a_thn.jpg")
        got = sorted(p.name for p in prune_seestar.sibling_jpgs(sub))
        self.assertEqual(got, ["Light_a.jpg", "Light_a_thn.jpg"])

    def test_case_insensitive_suffix(self):
        sub = self._touch("Light_b.fit")
        self._touch("Light_b.JPG")
        got = [p.name for p in prune_seestar.sibling_jpgs(sub)]
        self.assertEqual(got, ["Light_b.JPG"])

    def test_no_jpgs_returns_empty(self):
        sub = self._touch("Light_c.fit")
        self.assertEqual(prune_seestar.sibling_jpgs(sub), [])

    def test_does_not_match_other_subs_jpg(self):
        sub = self._touch("Light_a.fit")
        self._touch("Light_aa.jpg")   # different stem
        self.assertEqual(prune_seestar.sibling_jpgs(sub), [])
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m unittest tests.test_parsing.PruneSiblingJpgsTests -v`
Expected: FAIL — `AttributeError: ... has no attribute 'sibling_jpgs'`.

- [ ] **Step 3: Implement**

Append to `prune_seestar.py`:

```python
def sibling_jpgs(sub_path: Path) -> list[Path]:
    """JPG preview + thumbnail belonging to a sub of stem S, in the same dir.

    Matches S.jpg / S.jpeg (full preview) and S_thn.jpg / S_thn.jpeg (Seestar
    thumbnail), case-insensitively, by exact stem so a preview is only ever
    paired with the specific sub it belongs to. Returns existing files only.
    """
    stem = sub_path.stem            # "Light_a.fit" -> "Light_a"
    wanted_stems = {stem, f"{stem}_thn"}
    out = []
    for p in sub_path.parent.iterdir():
        if (p.is_file()
                and p.suffix.lower() in JPG_SUFFIXES
                and p.stem in wanted_stems):
            out.append(p)
    return sorted(out)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m unittest tests.test_parsing.PruneSiblingJpgsTests -v`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add prune_seestar.py tests/test_parsing.py
git commit -m "feat: sibling_jpgs (per-sub preview + thumbnail by stem)"
```

---

### Task 6: `is_effectively_empty` and `prune_empty_dir`

**Files:**
- Modify: `prune_seestar.py`
- Test: `tests/test_parsing.py`

- [ ] **Step 1: Write the failing test**

Add this TestCase:

```python
class PruneEmptyDirTests(unittest.TestCase):
    """is_effectively_empty + prune_empty_dir handle noise and dry-run."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _dir(self, name):
        d = self.tmp / name
        d.mkdir(parents=True, exist_ok=True)
        return d

    def test_empty_dir_is_effectively_empty(self):
        d = self._dir("a")
        self.assertTrue(prune_seestar.is_effectively_empty(d))

    def test_only_noise_is_effectively_empty(self):
        d = self._dir("b")
        (d / ".DS_Store").write_bytes(b"x")
        (d / "._ghost").write_bytes(b"x")
        self.assertTrue(prune_seestar.is_effectively_empty(d))

    def test_real_file_blocks_empty(self):
        d = self._dir("c")
        (d / "Light_kept.jpg").write_bytes(b"x")
        self.assertFalse(prune_seestar.is_effectively_empty(d))

    def test_prune_empty_dir_dry_run_keeps_dir(self):
        d = self._dir("d")
        self.assertFalse(prune_seestar.prune_empty_dir(d, dry_run=True))
        self.assertTrue(d.exists())

    def test_prune_empty_dir_executes(self):
        d = self._dir("e")
        (d / ".DS_Store").write_bytes(b"x")   # noise is cleared first
        self.assertTrue(prune_seestar.prune_empty_dir(d, dry_run=False))
        self.assertFalse(d.exists())

    def test_prune_empty_dir_refuses_nonempty(self):
        d = self._dir("f")
        (d / "real.fit").write_bytes(b"x")
        self.assertFalse(prune_seestar.prune_empty_dir(d, dry_run=False))
        self.assertTrue(d.exists())
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m unittest tests.test_parsing.PruneEmptyDirTests -v`
Expected: FAIL — `AttributeError: ... has no attribute 'is_effectively_empty'`.

- [ ] **Step 3: Implement**

Append to `prune_seestar.py`:

```python
def is_effectively_empty(directory: Path) -> bool:
    """True if the dir holds no real files, ignoring only .DS_Store / ._* noise.

    A kept sub or a stray/orphan JPG is a real file and makes this False, so
    pruning never strands data.
    """
    for p in directory.rglob("*"):
        if p.is_file() and not p.name.startswith(SKIP_PREFIXES):
            return False
    return True


def prune_empty_dir(directory: Path, dry_run: bool) -> bool:
    """Remove *directory* if it is effectively empty. Returns True if removed.

    Clears .DS_Store / ._* noise first, then rmdir. Swallows the EBUSY /
    ENOTEMPTY / EPERM / EACCES errors a network share raises when it still holds
    a handle (macOS SMB leaves .smbdelete* tombstones) — mirrors
    rename_seestar_folders._safe_rmtree_donor so a locked handle reports rather
    than aborting the whole run. No-op in dry-run.
    """
    if not is_effectively_empty(directory):
        return False
    if dry_run:
        return False

    # Clear macOS noise so the directory can rmdir cleanly.
    for p in list(directory.rglob("*")):
        if p.is_file() and p.name.startswith(SKIP_PREFIXES):
            try:
                p.unlink()
            except OSError:
                pass
    # Remove any now-empty noise subdirs, deepest first.
    for p in sorted((d for d in directory.rglob("*") if d.is_dir()),
                    key=lambda d: len(d.parts), reverse=True):
        try:
            p.rmdir()
        except OSError:
            pass

    try:
        directory.rmdir()
    except OSError as e:
        if e.errno in (errno.EBUSY, errno.ENOTEMPTY, errno.EPERM, errno.EACCES):
            leftovers = list(directory.iterdir()) if directory.exists() else []
            ghosts = [p for p in leftovers if p.name.startswith(".smbdelete")]
            if leftovers and len(ghosts) == len(leftovers):
                print(f"    ⚠️  {directory.name}: left {len(ghosts)} SMB "
                      f"tombstone(s) (.smbdelete*) locked by the share. After a "
                      f"NAS remount: rm -rf '{directory}'")
            else:
                print(f"    ⚠️  could not remove {directory.name}: {e}")
            return False
        raise
    return True
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m unittest tests.test_parsing.PruneEmptyDirTests -v`
Expected: PASS (6 tests).

- [ ] **Step 5: Commit**

```bash
git add prune_seestar.py tests/test_parsing.py
git commit -m "feat: is_effectively_empty + prune_empty_dir (SMB-safe rmdir)"
```

---

### Task 7: `prune` orchestrator

**Files:**
- Modify: `prune_seestar.py`
- Test: `tests/test_parsing.py`

- [ ] **Step 1: Write the failing test**

Add this TestCase:

```python
class PruneOrchestratorTests(unittest.TestCase):
    """prune() ties matching, JPG ride-along, and dir pruning together."""

    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.emmc = self.root / "emmc"
        self.nas = self.root / "nas"
        self.emmc.mkdir()
        self.nas.mkdir()

    def tearDown(self):
        import shutil
        shutil.rmtree(self.root, ignore_errors=True)

    def _f(self, base, rel, size=10):
        p = base / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"x" * size)
        return p

    def _quiet(self, **kw):
        # prune() prints a report; silence it for assertions.
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            return prune_seestar.prune(**kw)

    def test_matched_sub_and_jpgs_deleted_folder_pruned(self):
        # EMMC: native layout "M 51_sub" with one sub + its previews
        self._f(self.emmc, "M 51_sub/Light_a.fit", size=100)
        self._f(self.emmc, "M 51_sub/Light_a.jpg", size=50)
        self._f(self.emmc, "M 51_sub/Light_a_thn.jpg", size=5)
        # NAS: archived + reorged under the renamed folder
        self._f(self.nas, "M_51_sub/20s/lights/Light_a.fit", size=100)

        summary = self._quiet(emmc=self.emmc, nas=self.nas,
                              targets=None, dry_run=False)
        self.assertFalse((self.emmc / "M 51_sub").exists())   # pruned
        self.assertEqual(summary["subs_deleted"], 1)
        self.assertEqual(summary["jpgs_deleted"], 2)
        self.assertEqual(summary["targets_skipped"], 0)

    def test_dry_run_deletes_nothing(self):
        self._f(self.emmc, "M 51_sub/Light_a.fit", size=100)
        self._f(self.nas, "M_51_sub/lights/Light_a.fit", size=100)
        summary = self._quiet(emmc=self.emmc, nas=self.nas,
                              targets=None, dry_run=True)
        self.assertTrue((self.emmc / "M 51_sub/Light_a.fit").exists())
        self.assertEqual(summary["subs_deleted"], 1)  # counts "would delete"

    def test_missing_nas_target_is_skipped(self):
        self._f(self.emmc, "M 51_sub/Light_a.fit", size=100)
        # no M_51_sub on the NAS at all
        summary = self._quiet(emmc=self.emmc, nas=self.nas,
                              targets=None, dry_run=False)
        self.assertTrue((self.emmc / "M 51_sub/Light_a.fit").exists())
        self.assertEqual(summary["subs_deleted"], 0)
        self.assertEqual(summary["targets_skipped"], 1)

    def test_unmatched_sub_kept_with_its_jpg(self):
        self._f(self.emmc, "M 51_sub/Light_a.fit", size=100)
        self._f(self.emmc, "M 51_sub/Light_a.jpg", size=50)
        self._f(self.nas, "M_51_sub/lights/Light_a.fit", size=999)  # size differs
        summary = self._quiet(emmc=self.emmc, nas=self.nas,
                              targets=None, dry_run=False)
        self.assertTrue((self.emmc / "M 51_sub/Light_a.fit").exists())
        self.assertTrue((self.emmc / "M 51_sub/Light_a.jpg").exists())
        self.assertEqual(summary["subs_kept"], 1)

    def test_targets_filter_limits_scope(self):
        self._f(self.emmc, "M 51_sub/Light_a.fit", size=100)
        self._f(self.emmc, "NGC 7000_sub/Light_b.fit", size=100)
        self._f(self.nas, "M_51_sub/lights/Light_a.fit", size=100)
        self._f(self.nas, "NGC_7000_sub/lights/Light_b.fit", size=100)
        summary = self._quiet(emmc=self.emmc, nas=self.nas,
                              targets=["M 51_sub"], dry_run=False)
        self.assertFalse((self.emmc / "M 51_sub").exists())
        self.assertTrue((self.emmc / "NGC 7000_sub/Light_b.fit").exists())
        self.assertEqual(summary["subs_deleted"], 1)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m unittest tests.test_parsing.PruneOrchestratorTests -v`
Expected: FAIL — `AttributeError: ... has no attribute 'prune'`.

- [ ] **Step 3: Implement**

Append to `prune_seestar.py`:

```python
def prune(emmc: Path, nas: Path, targets: list[str] | None,
          dry_run: bool) -> dict:
    """Delete EMMC subs (and their JPG previews) confirmed on the NAS.

    For each EMMC target: map its name to the NAS via new_name(); if that NAS
    folder is missing, skip the whole target. Otherwise index the NAS subtree
    and delete each root sub whose name+size matches, plus that sub's sibling
    JPG preview/thumbnail. Prune a target folder once it holds no real files.

    Returns a summary dict with totals. Counts reflect what was (or, in dry-run,
    *would be*) deleted.
    """
    summary = {
        "subs_deleted": 0, "jpgs_deleted": 0, "subs_kept": 0,
        "bytes_freed": 0, "targets_skipped": 0, "dirs_pruned": 0,
    }

    emmc_targets = find_emmc_targets(emmc)
    if targets:
        wanted = set(targets)
        emmc_targets = [t for t in emmc_targets if t.name in wanted]

    verb = "WOULD DELETE" if dry_run else "DELETE"
    for target in emmc_targets:
        canonical = new_name(target.name)
        nas_target = nas / canonical
        print(f"\n── {target.name}  →  NAS/{canonical}")

        if not nas_target.is_dir():
            print(f"   ⏭  SKIP — not on NAS (no {canonical}/); nothing deletable.")
            summary["targets_skipped"] += 1
            continue

        index = index_nas_target(nas_target)
        to_delete, kept = eligible_subs(target, index)

        for sub in to_delete:
            jpgs = sibling_jpgs(sub)
            for f in [sub, *jpgs]:
                try:
                    summary["bytes_freed"] += f.stat().st_size
                except OSError:
                    pass
            print(f"   🗑  {verb}  {sub.name}"
                  + (f"  (+{len(jpgs)} jpg)" if jpgs else ""))
            if not dry_run:
                for f in [sub, *jpgs]:
                    try:
                        f.unlink()
                    except OSError as e:
                        print(f"      ⚠️  could not delete {f.name}: {e}")
            summary["subs_deleted"] += 1
            summary["jpgs_deleted"] += len(jpgs)

        for sub in kept:
            print(f"   ⏸  KEEP  {sub.name} — not confirmed on NAS")
        summary["subs_kept"] += len(kept)

        if prune_empty_dir(target, dry_run):
            print(f"   📁  pruned empty folder {target.name}")
            summary["dirs_pruned"] += 1

    return summary
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m unittest tests.test_parsing.PruneOrchestratorTests -v`
Expected: PASS (5 tests).

- [ ] **Step 5: Commit**

```bash
git add prune_seestar.py tests/test_parsing.py
git commit -m "feat: prune() orchestrator (match, JPG ride-along, prune dirs)"
```

---

### Task 8: `main()` — CLI, conf, confirmation guard

**Files:**
- Modify: `prune_seestar.py`
- Test: manual smoke (CLI behavior; pure logic already covered)

- [ ] **Step 1: Implement `main()` and the entry point**

Append to `prune_seestar.py`:

```python
def _print_summary(summary: dict, dry_run: bool) -> None:
    mb = summary["bytes_freed"] / (1024 * 1024)
    print("\n" + "=" * 50)
    head = "DRY RUN — would delete" if dry_run else "DONE — deleted"
    print(f"  {head}: {summary['subs_deleted']} subs, "
          f"{summary['jpgs_deleted']} jpgs ({mb:.1f} MB)")
    print(f"  kept (not on NAS): {summary['subs_kept']} subs")
    print(f"  targets skipped (no archive): {summary['targets_skipped']}")
    print(f"  folders pruned: {summary['dirs_pruned']}")
    print("=" * 50 + "\n")


def main() -> None:
    if "-h" in sys.argv or "--help" in sys.argv:
        print(__doc__)
        sys.exit(0)

    p = argparse.ArgumentParser(add_help=False)
    p.add_argument("targets", nargs="*",
                   help="EMMC folder name(s) to limit to; default: all _sub dirs")
    p.add_argument("--emmc", default=None, help="Seestar EMMC MyWorks root")
    p.add_argument("--nas", default=None, help="NAS Seestar archive root")
    p.add_argument("--execute", action="store_true",
                   help="actually delete (default is dry-run)")
    p.add_argument("--dry-run", action="store_true",
                   help="preview only (the default; explicit for clarity)")
    p.add_argument("-y", "--yes", action="store_true",
                   help="skip the confirmation prompt")
    args = p.parse_args()

    dry_run = not args.execute        # dry-run unless --execute given

    conf = load_conf()
    emmc = Path((args.emmc or conf["SEESTAR_EMMC"])).expanduser()
    nas = Path((args.nas or conf["SEESTAR_NAS"])).expanduser()

    if not emmc.exists():
        print(f"❌  EMMC not found: {emmc}\n    Is the Seestar connected via USB?")
        sys.exit(1)
    if not nas.exists():
        print(f"❌  NAS archive not found: {nas}\n    Is the NAS mounted?")
        sys.exit(1)

    print("=" * 50)
    print("  prune_seestar — delete EMMC subs already on the NAS")
    print("=" * 50)
    print(f"  EMMC (delete from): {emmc}")
    print(f"  NAS  (verify on):   {nas}")
    if args.targets:
        print(f"  targets: {', '.join(args.targets)}")
    if dry_run:
        print("\n  DRY RUN — nothing will be deleted (pass --execute to delete)")
    print()

    if not dry_run and not args.yes:
        if not sys.stdin.isatty():
            print("❌  Refusing to delete unattended without confirmation.")
            print("    Re-run with --yes once you've checked the paths above,")
            print("    or omit --execute to preview.")
            sys.exit(1)
        reply = input("  Delete matched subs from the EMMC above? [y/N] ")
        if reply.strip().lower() not in ("y", "yes"):
            print("  Aborted — nothing was deleted.")
            sys.exit(0)

    summary = prune(emmc, nas, args.targets or None, dry_run)
    _print_summary(summary, dry_run)


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Make the script executable**

Run: `chmod +x prune_seestar.py`

- [ ] **Step 3: Smoke-test the help and dry-run guard**

Run: `python3 prune_seestar.py -h | head -5`
Expected: prints the module docstring header.

Run: `python3 prune_seestar.py --emmc /nonexistent --nas /nonexistent`
Expected: `❌  EMMC not found: /nonexistent` and exit 1.

- [ ] **Step 4: Smoke-test against temp dirs (real dry-run path)**

Run:
```bash
TMP=$(mktemp -d)
mkdir -p "$TMP/emmc/M 51_sub" "$TMP/nas/M_51_sub/lights"
printf 'xxxxx' > "$TMP/emmc/M 51_sub/Light_a.fit"
printf 'xxxxx' > "$TMP/nas/M_51_sub/lights/Light_a.fit"
python3 prune_seestar.py --emmc "$TMP/emmc/" --nas "$TMP/nas/"
ls "$TMP/emmc/M 51_sub/"   # Light_a.fit must still be present (dry-run)
rm -rf "$TMP"
```
Expected: report shows `WOULD DELETE Light_a.fit`; the file still exists after.

- [ ] **Step 5: Commit**

```bash
git add prune_seestar.py
git commit -m "feat: prune_seestar main() — CLI, conf, dry-run default + confirm guard"
```

---

### Task 9: Full suite + docs

**Files:**
- Modify: `README.md`
- Test: full `unittest` run

- [ ] **Step 1: Run the whole test suite**

Run: `python3 -m unittest discover -s tests -v 2>&1 | tail -20`
Expected: all tests pass (existing + the new Prune* classes), `OK`.

- [ ] **Step 2: Add prune_seestar to the README script table**

In `README.md`, in the `## Scripts` table, add this row after the
`restore_to_seestar.py` / `purge_siril_cruft.py` rows (keep alignment with the
existing table):

```markdown
| `prune_seestar.py` | Deletes EMMC subs already on the NAS (name+size match) plus their per-sub JPG previews; prunes emptied folders. Dry-run by default — pass `--execute` |
```

- [ ] **Step 3: Add a one-line mention to the pipeline-phases sentence**

In `README.md`, the sentence describing phases mentions "ingest / organise /
cleanup". Append to the cleanup description (same paragraph) the clause:

```markdown
 `prune_seestar.py` then reclaims space on the *scope* by deleting subs already safe on the NAS.
```

- [ ] **Step 4: Commit**

```bash
git add README.md
git commit -m "docs: document prune_seestar.py in README"
```

---

## Self-Review Notes

- **Spec coverage:** name+size match (Task 4), missing-NAS skip (Task 7), reorg-subtree index (Task 2), JPG ride-along by stem (Task 5/7), empty-dir prune with SMB-swallow (Task 6), dry-run default + `--execute` + unattended guard (Task 8), `_trash`/`scripts` exclusion (Task 3), reporting of kept/skipped (Task 7/8), tests for every case (Tasks 2-7). All spec sections map to a task.
- **Reuse:** `new_name` and `is_in_excluded` imported, not reimplemented; `load_conf`/`is_fits` deliberately kept local (matches per-script convention in the repo; documented in Task 1).
- **Naming consistency:** `index_nas_target`, `eligible_subs`, `sibling_jpgs`, `is_effectively_empty`, `prune_empty_dir`, `prune` used identically across tasks and the summary keys (`subs_deleted`, `jpgs_deleted`, `subs_kept`, `bytes_freed`, `targets_skipped`, `dirs_pruned`) match between Task 7 and Task 8.
