"""
spacefree.py — build the FreeSurfer/CHARM head model in a LOCAL APEX temp folder.

Two problems with running recon-all directly in the lab's shared work folder
(`/Volumes/Lab Data/...`):

  1. SPACES. recon-all breaks on spaces in SUBJECTS_DIR / input paths
     (`pushd: Too many arguments`).
  2. SYMLINKS. recon-all creates internal symlinks (e.g. `lh.white.H ->
     lh.white.preaparc.H`). SMB/CIFS network shares refuse symlink creation, so
     the run dies late with `ln: ... : Permission denied` — even though every
     regular-file write to the share works fine.

Fix: build the whole head model (recon-all + CHARM) in a LOCAL, space-free,
symlink-capable APEX temp folder under the user's home (`~/APEX_local/<subject>`),
then copy the finished `m2m_<subject>` back to the shared work folder
(dereferencing links, so no symlink is ever created on the share). The dose step
and other machines read that m2m from the share as before. An optional cleanup
step (see charm.head_model_shell) can archive the recon-all output to the share
and delete the local temp folder afterward.

The path helpers here are PURE (no side effects) so the GUI can poll build status
without re-creating anything; `headmodel_build_paths()` is the one call that
actually creates the local dirs + space-free input symlinks before a launch.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Tuple
import os
import re


def _slug(s: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", str(s)).strip("_") or "subject"


# --------------------------------------------------------------------------
# pure path helpers (no filesystem side effects) — safe to call every poll
# --------------------------------------------------------------------------

def _link_base_path() -> Path:
    """Space-free LOCAL base dir for the APEX temp build folders + input symlinks
    (home if space-free, else /private/tmp). Pure: does NOT create the dir."""
    home = Path.home()
    if " " not in str(home):
        return home / "APEX_local"
    return Path("/private/tmp/APEX_local")


def local_build_root(subject: str) -> Path:
    """Local, space-free APEX temp folder holding this subject's freesurfer/ +
    simnibs/. Depends only on the subject id, so the GUI poll can recompute it."""
    return _link_base_path() / _slug(subject)


def local_headmodel_dirs(subject: str) -> Tuple[Path, Path]:
    """(subjects_dir, simnibs_parent) in the LOCAL APEX temp folder. Pure."""
    root = local_build_root(subject)
    return root / "freesurfer", root / "simnibs"


def share_headmodel_dirs(work_dir: str | Path) -> Tuple[Path, Path]:
    """(subjects_dir, simnibs_parent) inside the user's shared work folder — the
    copy-back destination the dose step reads from. Pure."""
    work = Path(work_dir)
    return work / "freesurfer", work / "simnibs"


def has_spaces(*paths) -> bool:
    return any(" " in str(p) for p in paths if p)


# --------------------------------------------------------------------------
# symlink helper + the one call that prepares a launch
# --------------------------------------------------------------------------

def link_base() -> Path:
    """Ensure and return the space-free local base dir."""
    base = _link_base_path()
    base.mkdir(parents=True, exist_ok=True)
    return base


def _symlink(target: str | Path, link: Path) -> Path:
    """Point `link` at `target` (absolute). Returns `link`, or the real target if
    a symlink can't be made / something real is already in the way."""
    target = str(Path(target).resolve())
    try:
        if link.is_symlink():
            if os.readlink(link) == target:
                return link
            link.unlink()
        elif link.exists():
            return Path(target)          # something real is in the way; don't touch
        os.symlink(target, str(link))
        return link
    except OSError:
        return Path(target)


def work_supports_direct_build(work_dir: str | Path) -> bool:
    """True if recon-all/CHARM can build DIRECTLY in the work folder — no local
    temp copy needed. Requires BOTH: no spaces in the path (recon-all breaks on
    them) AND the filesystem allows creating a symlink (SMB/CIFS network shares
    don't, and recon-all makes symlinks). Probed by actually creating one."""
    import uuid
    work = Path(work_dir)
    if " " in str(work):
        return False
    try:
        if not work.is_dir():
            return False
    except OSError:
        return False
    tgt = work / f".recare_symtest_{uuid.uuid4().hex}"
    lnk = work / f".recare_symlink_{uuid.uuid4().hex}"
    ok = False
    try:
        tgt.write_text("x")
        os.symlink(tgt.name, str(lnk))     # relative symlink inside the work folder
        ok = lnk.is_symlink()
    except OSError:
        ok = False
    finally:
        for p in (lnk, tgt):
            try:
                p.unlink()
            except OSError:
                pass
    return ok


@dataclass
class HeadModelPaths:
    local_root: Path            # the build root: <work> (direct) or ~/APEX_local/<subject> (temp)
    subjects_dir: Path          # recon-all SUBJECTS_DIR (<build_root>/freesurfer)
    simnibs_parent: Path        # charm cwd -> writes m2m_<subject> here
    t1: Path                    # space-free input (symlink only if the input path had spaces)
    t2: Optional[Path]
    share_subjects_dir: Path    # <work>/freesurfer  (recon archive dest, on cleanup)
    share_simnibs_parent: Path  # <work>/simnibs     (m2m copy dest; dose reads here)
    direct: bool                # True if building straight in the work folder (no temp copy)


def headmodel_build_paths(
    work_dir: str | Path, subject: str,
    t1: str | Path, t2: Optional[str | Path] = None,
) -> HeadModelPaths:
    """Return every path the head-model shell needs, choosing where to build:
    DIRECTLY in the work folder when it's local + symlink-capable + space-free
    (no temp, no copy), otherwise in a local APEX temp folder that gets copied
    back to the work folder. Creates the build dirs + any needed input symlinks."""
    share_fs, share_sp = share_headmodel_dirs(work_dir)
    direct = work_supports_direct_build(work_dir)
    if direct:
        root = Path(work_dir)
        subjects_dir, simnibs_parent = share_fs, share_sp   # build == work folder
    else:
        link_base()
        root = local_build_root(subject)
        if root.is_symlink():                # replace a stale symlink from an older scheme
            try:
                root.unlink()
            except OSError:
                pass
        subjects_dir = root / "freesurfer"
        simnibs_parent = root / "simnibs"
    subjects_dir.mkdir(parents=True, exist_ok=True)
    simnibs_parent.mkdir(parents=True, exist_ok=True)

    def suf(p):
        s = "".join(Path(p).suffixes)
        return s if s else ".nii.gz"

    def inp(src, label):
        # recon-all needs a space-free input path; only symlink when the real path
        # has spaces (the build root is symlink-capable in both modes).
        if src is None:
            return None
        if " " not in str(Path(src).resolve()):
            return Path(src)
        return _symlink(src, root / f"{label}_in{suf(src)}")
    t1p = inp(t1, "T1")
    t2p = inp(t2, "T2")

    return HeadModelPaths(
        local_root=root, subjects_dir=subjects_dir, simnibs_parent=simnibs_parent,
        t1=t1p, t2=t2p,
        share_subjects_dir=share_fs, share_simnibs_parent=share_sp, direct=direct)


# --------------------------------------------------------------------------
# backward-compatible shim (old callers) — now returns LOCAL build paths
# --------------------------------------------------------------------------

def spacefree_headmodel_paths(
    work_dir: str | Path, subject: str,
    t1: str | Path, t2: Optional[str | Path] = None,
) -> Tuple[Path, Path, Path, Optional[Path]]:
    """Deprecated: kept for old callers. Returns (subjects_dir, simnibs_parent,
    t1, t2) as LOCAL space-free build paths."""
    hp = headmodel_build_paths(work_dir, subject, t1, t2)
    return hp.subjects_dir, hp.simnibs_parent, hp.t1, hp.t2
