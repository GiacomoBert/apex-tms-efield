"""
simulate.py — Run SimNIBS TMS E-field simulations from Brainsight coil matrices.

Port of the run half of simNIBS_BF_final.m, using the SimNIBS 4 Python API
(verified against the official "Scripting Simulations" docs):

    from simnibs import sim_struct, run_simnibs
    s = sim_struct.SESSION(); s.subpath = m2m; s.pathfem = out
    tl = s.add_tmslist(); tl.fnamecoil = coil
    p  = tl.add_position(); p.matsimnibs = mat; p.didt = didt_A_per_s
    run_simnibs(s)

Key details
-----------
* Positions come straight from the Brainsight `matsimnibs` (see brainsight.py) —
  when matsimnibs is set, SimNIBS applies no projection and ignores `distance`.
* dI/dt: the RA reads it off the MagVenture screen in **A/µs**; SimNIBS wants
  **A/s**, so we multiply by 1e6. For the APEX ratio the value cancels (M1 and
  target share the same dI/dt), but it sets the absolute V/m.
* Position order is the source of truth: position k (1-based) -> conditions[k].
  We also write mapping.json + completed_conditions.txt for resume + extraction.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Sequence
import json
import os
import shutil
import subprocess
import numpy as np


DIDT_US_TO_S = 1.0e6  # A/µs (machine screen) -> A/s (SimNIBS)

# Marker file written into folders WE created, so we never delete anything else.
SENTINEL = ".recare_sim_output"
# Names that signal a folder holds real data and must never be deleted.
_DATA_DIR_NAMES = {"dicom", "freesurfer", "surf", "mri", "label", "t1raw"}
_DATA_SUFFIXES = (".nii", ".nii.gz", ".mgz", ".dcm", ".ima")


def _is_our_output(out_dir: Path) -> bool:
    return (Path(out_dir) / SENTINEL).is_file()


def _looks_like_data_folder(out_dir: Path) -> bool:
    """True if out_dir contains an m2m folder, a known data subfolder, or image
    files — i.e. something we must never blow away."""
    try:
        for child in Path(out_dir).iterdir():
            n = child.name.lower()
            if child.is_dir() and (n.startswith("m2m") or n in _DATA_DIR_NAMES):
                return True
            if n.endswith(_DATA_SUFFIXES):
                return True
    except OSError:
        pass
    return False


def _guard_out_dir(out_dir: Path, m2m_dir: Path) -> None:
    """Abort before any deletion if out_dir is the m2m folder or a parent of it."""
    out = Path(out_dir).resolve()
    m2m = Path(m2m_dir).resolve()
    if out == m2m:
        raise RuntimeError(f"out_dir must not be the m2m folder itself:\n  {out}")
    if out in m2m.parents:
        raise RuntimeError(
            f"out_dir is a PARENT of the m2m folder — running would risk deleting "
            f"your data.\n  out_dir: {out}\n  m2m:     {m2m}\n"
            f"Use a dedicated subfolder instead, e.g. {out / 'sim_output'}")


def _nonhidden_children(out_dir: Path) -> List[Path]:
    """Children ignoring hidden dotfiles (e.g. macOS .DS_Store), so a folder made
    in Finder — which quietly gets a .DS_Store — still counts as 'empty'."""
    try:
        return [c for c in Path(out_dir).iterdir() if not c.name.startswith(".")]
    except OSError:
        return []


def dedicated_output_dir(parent: str | Path, subject: str) -> Path:
    """A dedicated sim-output folder INSIDE `parent`, named apex_<subject>.
    Whatever the user picks is treated as a parent; we make our own subfolder so
    we never touch their data. Reuses our own previous folder; if a non-ours
    folder of that name exists, adds a numeric suffix. (Creation is left to
    run_simulations.)"""
    parent = Path(parent)
    subj = (subject or "subject").strip() or "subject"
    base = parent / f"apex_{subj}"
    if not base.exists() or _is_our_output(base):
        return base
    i = 2
    while True:
        cand = parent / f"apex_{subj}_{i}"
        if not cand.exists() or _is_our_output(cand):
            return cand
        i += 1


@dataclass
class Position:
    name: str                 # condition label, e.g. "M1_rMT" or "IPL_120rMT"
    matsimnibs: np.ndarray     # 4x4
    didt_A_per_us: float       # as read from the MagVenture screen


@dataclass
class RunResult:
    pathfem: Path
    coil: str
    mapping: Dict[int, str]    # {1: "M1_rMT", 2: "IPL_rMT", ...}
    reused: bool               # True if an existing complete run was reused


def _desired_conditions(positions: Sequence[Position]) -> List[str]:
    return [p.name for p in positions]


def _read_log(pathfem: Path) -> Optional[List[str]]:
    log = pathfem / "completed_conditions.txt"
    if not log.is_file():
        return None
    return [ln.strip() for ln in log.read_text().splitlines() if ln.strip()]


def run_simulations(
    m2m_dir: str | Path,
    coil_file: str | Path,
    positions: Sequence[Position],
    out_dir: str | Path,
    fields: str = "eE",
    map_to_vol: bool = False,
    map_to_mni: bool = False,
    open_in_gmsh: bool = False,
    overwrite: bool = False,
) -> RunResult:
    """Run all `positions` in a single TMSLIST. Resumable: an existing folder
    whose completed_conditions.txt matches is reused unless `overwrite`."""
    m2m_dir = Path(m2m_dir)
    coil_file = str(coil_file)
    out_dir = Path(out_dir)
    desired = _desired_conditions(positions)

    if not m2m_dir.is_dir():
        raise FileNotFoundError(f"m2m folder not found: {m2m_dir}")
    # Validity of an m2m == the head mesh is present (not just the folder). A
    # charm run that failed leaves a folder with no <id>.msh; reject it here with a
    # clear message rather than letting SimNIBS fail cryptically later.
    from charm import is_m2m_valid
    if not is_m2m_valid(m2m_dir):
        raise FileNotFoundError(
            f"m2m folder has no head mesh (<id>.msh) — it is not a completed CHARM "
            f"model:\n  {m2m_dir}\nPoint at a finished m2m_<id> folder (the one that "
            f"contains <id>.msh).")
    if not Path(coil_file).is_file():
        raise FileNotFoundError(f"coil file not found: {coil_file}")

    _guard_out_dir(out_dir, m2m_dir)

    # ---- resume / overwrite logic (SAFE: never delete non-output data) ----
    if out_dir.exists() and _nonhidden_children(out_dir):
        ours = _is_our_output(out_dir)
        if ours and not overwrite and _read_log(out_dir) == desired:
            mapping = {i + 1: n for i, n in enumerate(desired)}
            return RunResult(out_dir, coil_file, mapping, reused=True)
        if not ours:
            raise RuntimeError(
                f"Refusing to write into a non-empty folder I did not create:\n"
                f"  {out_dir}\n"
                f"It has no '{SENTINEL}' marker and may hold your data "
                f"(m2m / DICOM / NIfTI). Point out_dir at a new or empty folder, "
                f"e.g. {out_dir / 'simtest'} or {out_dir.parent / 'sim_output'}.")
        if _looks_like_data_folder(out_dir):
            raise RuntimeError(
                f"Refusing to delete {out_dir}: it looks like it contains data "
                f"(m2m/DICOM/NIfTI). Choose a dedicated output folder.")
        shutil.rmtree(out_dir)   # safe: our own marked, non-data output

    # ---- build the SESSION ---------------------------------------------
    from simnibs import sim_struct, run_simnibs  # lazy import
    s = sim_struct.SESSION()
    s.subpath = str(m2m_dir)
    s.pathfem = str(out_dir)
    s.fields = fields
    s.map_to_vol = map_to_vol
    s.map_to_mni = map_to_mni
    s.open_in_gmsh = open_in_gmsh

    tl = s.add_tmslist()
    tl.fnamecoil = coil_file

    mapping: Dict[int, str] = {}
    for i, p in enumerate(positions, start=1):
        pos = tl.add_position()
        pos.matsimnibs = np.asarray(p.matsimnibs, dtype=float)
        pos.didt = float(p.didt_A_per_us) * DIDT_US_TO_S
        try:
            pos.name = p.name           # not all versions expose this; harmless
        except Exception:
            pass
        mapping[i] = p.name

    run_simnibs(s)

    # ---- write logs / mapping / marker ---------------------------------
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / SENTINEL).write_text("APEX pipeline simulation output — safe to delete.\n")
    (out_dir / "completed_conditions.txt").write_text("\n".join(desired) + "\n")
    (out_dir / "mapping.json").write_text(json.dumps(mapping, indent=2))

    return RunResult(out_dir, coil_file, mapping, reused=False)


def _find_gmsh(explicit: Optional[str] = None) -> Optional[str]:
    """Gmsh viewer, cross-platform (macOS + Linux/WSL). See locate.py."""
    import locate
    return locate.gmsh_bin(explicit)


def open_results_in_gmsh(mesh_paths: Sequence[str | Path],
                         gmsh_bin: Optional[str] = None) -> None:
    """Open the given result meshes in Gmsh for visual QC — NON-BLOCKING.

    Launches Gmsh as a background process (one window per mesh) so the script
    does NOT wait for you to close each window. Pass the exact result files you
    want (e.g. the `*_scalar.msh` files from the sim folder).
    """
    paths = [str(p) for p in mesh_paths]
    exe = _find_gmsh(gmsh_bin)
    if exe is None:
        print("  (gmsh not found — open these manually to QC:)")
        for p in paths:
            print("   ", p)
        return
    for p in paths:
        try:
            subprocess.Popen([exe, p],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except Exception as e:
            print(f"  (could not launch gmsh for {p}: {e})")
