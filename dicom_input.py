"""
dicom_input.py — Resolve a DICOM input that may be a folder OR a compressed
archive (.tbz/.tar.bz2/.tgz/.tar.gz/.tar/.tar.xz/.zip).

Motivation: MRI DICOMs often arrive as a `.tbz` (e.g. from the scanner export).
Manually this was: `tar -xjf study.tbz -C dest/`. Here, if the operator selects a
`.tbz` where a DICOM folder is expected, we extract it (safely) and find the DICOMs.

Public API:
    is_archive(path)                 -> bool
    resolve_dicom_input(path, work)  -> Path to a folder that contains DICOMs
    count_dicoms(folder)             -> int  (files with the DICM magic)
"""

from __future__ import annotations

from pathlib import Path
from typing import List
import os
import tarfile
import zipfile


_ARCHIVE_SUFFIXES = (
    ".tbz", ".tbz2", ".tar.bz2", ".tgz", ".tar.gz",
    ".txz", ".tar.xz", ".tar", ".zip",
)


def is_archive(path: str | Path) -> bool:
    name = str(path).lower()
    return name.endswith(_ARCHIVE_SUFFIXES)


def _looks_like_dicom(fp: Path) -> bool:
    """True if the file has the DICOM magic 'DICM' at byte offset 128.
    Catches extensionless DICOMs (common) as well as .dcm/.ima."""
    try:
        with open(fp, "rb") as fh:
            fh.seek(128)
            return fh.read(4) == b"DICM"
    except (OSError, ValueError):
        return False


def count_dicoms(folder: str | Path) -> int:
    folder = Path(folder)
    n = 0
    for root, _dirs, files in os.walk(folder):
        for f in files:
            if _looks_like_dicom(Path(root) / f):
                n += 1
    return n


def find_dicom_root(folder: str | Path) -> Path:
    """Return the shallowest directory under `folder` that contains DICOMs.
    dcm2niix/mri_convert can recurse, so returning the top is usually fine; we
    validate DICOMs exist somewhere beneath."""
    folder = Path(folder)
    if count_dicoms(folder) == 0:
        raise FileNotFoundError(f"No DICOM files (DICM magic) found under {folder}")
    return folder


# ---- safe extraction (guards against path traversal / Zip-Slip) ----------

def _within(base: Path, target: Path) -> bool:
    base = base.resolve()
    try:
        target = target.resolve()
    except OSError:
        return False
    return base == target or base in target.parents


def _safe_tar_members(tf: tarfile.TarFile, dest: Path) -> List[tarfile.TarInfo]:
    safe = []
    for m in tf.getmembers():
        # reject absolute paths, .., and symlinks/hardlinks escaping dest
        target = dest / m.name
        if not _within(dest, target):
            raise ValueError(f"Unsafe path in archive: {m.name!r}")
        if m.issym() or m.islnk():
            link_target = dest / m.name
            if not _within(dest, (link_target.parent / m.linkname)):
                raise ValueError(f"Unsafe link in archive: {m.name!r}")
        safe.append(m)
    return safe


def _safe_zip_names(zf: zipfile.ZipFile, dest: Path) -> List[str]:
    safe = []
    for name in zf.namelist():
        if not _within(dest, dest / name):
            raise ValueError(f"Unsafe path in archive: {name!r}")
        safe.append(name)
    return safe


def extract_archive(archive: str | Path, dest_dir: str | Path) -> Path:
    """Extract `archive` into dest_dir (created if needed). Auto-detects
    gzip/bzip2/xz for tar, and handles zip. Returns dest_dir."""
    archive = Path(archive)
    dest = Path(dest_dir)
    dest.mkdir(parents=True, exist_ok=True)

    if archive.name.lower().endswith(".zip"):
        with zipfile.ZipFile(archive) as zf:
            zf.extractall(dest, members=_safe_zip_names(zf, dest))
    else:
        # 'r:*' transparently handles .tar, .tar.gz/.tgz, .tar.bz2/.tbz, .tar.xz
        with tarfile.open(archive, "r:*") as tf:
            tf.extractall(dest, members=_safe_tar_members(tf, dest))
    return dest


def resolve_dicom_input(path: str | Path, work_dir: str | Path) -> Path:
    """Return a folder containing DICOMs, given either a folder or an archive.

    If `path` is an archive, extract it into work_dir/<archive-stem>/ and return
    the DICOM root within. If it's a folder, validate and return it.
    """
    path = Path(path)
    if is_archive(path):
        stem = path.name
        for suf in sorted(_ARCHIVE_SUFFIXES, key=len, reverse=True):
            if stem.lower().endswith(suf):
                stem = stem[: -len(suf)]
                break
        out = Path(work_dir) / stem
        extract_archive(path, out)
        return find_dicom_root(out)
    if path.is_dir():
        return find_dicom_root(path)
    raise FileNotFoundError(
        f"{path} is neither a DICOM folder nor a supported archive "
        f"({', '.join(_ARCHIVE_SUFFIXES)})")
