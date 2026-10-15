"""
brainsight.py — Parse Brainsight target-export .txt files into SimNIBS 4x4
coil-position matrices (matsimnibs).

Port of the MATLAB `parse_brainsight_txt` helper from simNIBS_BF_final.m.

Brainsight export (NIfTI:Scanner coords) gives, per target, a location and
three orientation vectors read from the ACTIVE side of the coil:
    m0 = x-axis  (points LEFT from coil center)
    m1 = y-axis  (points away from the handle)
    m2 = z-axis  (points INTO the coil, i.e. away from the head)

SimNIBS matsimnibs columns:
    col1 (x) = points RIGHT from center  => -m0
    col2 (y) = points away from handle   =>  m1
    col3 (z) = points toward the head    => -m2
    col4     = position (mm)

So:  matsimnibs = [ -m0 | m1 | -m2 | loc ]
                  [  0    0    0     1  ]
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Tuple
import numpy as np


# The target-block header has these 13 tab-delimited columns:
#   Target Name, Loc.X, Loc.Y, Loc.Z, m0n0, m0n1, m0n2, m1n0, m1n1, m1n2, m2n0, m2n1, m2n2
_N_TARGET_COLS = 13


@dataclass
class CoilTarget:
    name: str
    loc: np.ndarray          # (3,) position in mm (NIfTI:Scanner)
    matsimnibs: np.ndarray   # (4,4) SimNIBS coil position matrix


def _to_matsimnibs(loc: np.ndarray, m0: np.ndarray, m1: np.ndarray,
                   m2: np.ndarray) -> np.ndarray:
    mat = np.eye(4)
    mat[:3, 0] = -m0
    mat[:3, 1] = m1
    mat[:3, 2] = -m2
    mat[:3, 3] = loc
    return mat


def parse_brainsight_txt(path: str | Path) -> Dict[str, CoilTarget]:
    """Parse a Brainsight .txt export.

    Returns an ordered dict {target_name: CoilTarget}. Only the "Target"
    block is read; the "# Sample Name" recording block is ignored (its rows
    have a non-numeric 2nd field -> filtered out, exactly like the MATLAB).
    """
    path = Path(path)
    targets: Dict[str, CoilTarget] = {}

    # The file has a "Target" block (the planned targets) followed by a
    # "Sample" block (recorded pulses + tracker junk). We only read the
    # Target block: start after the "# Target Name" header, stop at the
    # "# Sample Name" header. This avoids phantom rows like a stray
    # "Session 1  ... 205.7" line that lives in the sample region and would
    # otherwise parse as a bogus target.
    in_target_block = False

    with open(path, "r", encoding="utf-8-sig", errors="replace") as fh:
        for raw in fh:
            line = raw.rstrip("\n").rstrip("\r")
            s = line.strip()
            if not s:
                continue
            if s.startswith("#"):
                low = s.lower()
                if "target name" in low:
                    in_target_block = True
                elif "sample name" in low:
                    break  # end of target block
                continue
            if not in_target_block:
                continue

            parts = line.split("\t")
            if len(parts) < _N_TARGET_COLS:
                continue

            name = parts[0].strip()
            try:
                vals = [float(x) for x in parts[1:_N_TARGET_COLS]]
            except ValueError:
                # Sample rows: parts[1] is e.g. "Session 2" -> not a float
                continue
            if any(np.isnan(vals)):
                continue

            vals = np.asarray(vals, dtype=float)
            loc = vals[0:3]
            m0 = vals[3:6]
            m1 = vals[6:9]
            m2 = vals[9:12]

            mat = _to_matsimnibs(loc, m0, m1, m2)
            # Last write wins if a name is duplicated (matches find() semantics
            # where a later manual target would shadow; we keep first instead
            # to be safe -> only add if unseen).
            if name not in targets:
                targets[name] = CoilTarget(name=name, loc=loc, matsimnibs=mat)

    return targets


def load_subject_targets(
    bs_dir: str | Path, sub_id: str,
    m1_name: str | None = None, other_names: List[str] | None = None,
) -> Dict[str, CoilTarget]:
    """Resolve split (_M1/_IPL) vs single Brainsight file for a subject,
    mirroring simNIBS_BF_final.m's file-resolution logic. Returns the merged
    target dict. Caller selects target names from the returned keys.
    """
    bs_dir = Path(bs_dir)
    single = bs_dir / f"{sub_id}.txt"
    f_m1 = bs_dir / f"{sub_id}_M1.txt"
    f_ipl = bs_dir / f"{sub_id}_IPL.txt"

    merged: Dict[str, CoilTarget] = {}
    if f_m1.is_file() and f_ipl.is_file():
        merged.update(parse_brainsight_txt(f_m1))
        merged.update(parse_brainsight_txt(f_ipl))
    elif single.is_file():
        merged.update(parse_brainsight_txt(single))
    else:
        raise FileNotFoundError(f"No Brainsight file for {sub_id} in {bs_dir}")
    return merged


# ---- validation helpers -------------------------------------------------

def check_matsimnibs(mat: np.ndarray, tol: float = 1e-2) -> Tuple[bool, dict]:
    """Sanity-check the rotation part: columns unit-length, orthogonal,
    right-handed (det ~ +1). Returns (ok, diagnostics)."""
    R = mat[:3, :3]
    norms = np.linalg.norm(R, axis=0)
    det = float(np.linalg.det(R))
    # orthogonality: R^T R should be identity
    ortho_err = float(np.max(np.abs(R.T @ R - np.eye(3))))
    ok = (np.all(np.abs(norms - 1.0) < tol)
          and abs(det - 1.0) < 5e-2
          and ortho_err < 5e-2)
    return ok, {"col_norms": norms.tolist(), "det": det,
                "ortho_err": ortho_err}
