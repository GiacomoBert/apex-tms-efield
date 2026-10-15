"""
extract.py — Peak cortical |E| extraction from a SimNIBS result mesh.

Port of the extraction logic in Extract_Efield.m / simNIBS_BF_final.m, using the
SimNIBS 4 Python API (verified against the official "Analyzing Simulations" docs):

    m  = simnibs.read_msh(result_msh)
    gm = m.crop_mesh(2)                       # 2 = gray matter volume
    ctr = gm.elements_baricenters()[:]
    vol = gm.elements_volumes_and_areas()[:]
    E   = gm.field["magnE"][:]                # element-wise |E| (V/m)

The value fed to APEX is a robust peak of |E| (default: volume-weighted 99.9th
percentile) inside a spherical ROI. The ROI is derived from the coil matrix, so
no separate target coordinate is needed.

Design note: the geometry/statistics helpers (weighted_percentile, project_*,
find_result_mesh) are pure-numpy and are unit-tested WITHOUT SimNIBS. Only
`extract_peak` / `load_gm_field` import simnibs (lazily).
"""

from __future__ import annotations

from dataclasses import dataclass, field as dc_field
from pathlib import Path
from typing import Dict, List, Optional, Sequence
import glob
import os
import numpy as np


GM_TAG = 2  # gray-matter volume tag in SimNIBS meshes (version-agnostic integer)
_FIELD_KEYS = ("magnE", "E_magn", "normE")  # naming varies across versions/mesh types


# ---------------------------------------------------------------------------
# Pure-numpy helpers (no SimNIBS needed — unit-tested in the sandbox)
# ---------------------------------------------------------------------------

def weighted_percentile(values: Sequence[float], weights: Sequence[float],
                        pct: float) -> float:
    """Volume-weighted percentile (matches SimNIBS's element-volume weighting).

    The p-th percentile is the value at which the cumulative weight reaches
    p% of the total. Linear interpolation between the two bracketing samples.
    """
    v = np.asarray(values, dtype=float)
    w = np.asarray(weights, dtype=float)
    if v.size == 0:
        return float("nan")
    if not (0.0 <= pct <= 100.0):
        raise ValueError(f"pct must be in [0,100] (got {pct})")
    order = np.argsort(v)
    v, w = v[order], w[order]
    cw = np.cumsum(w)
    total = cw[-1]
    if total <= 0:
        return float(v[-1])
    # cumulative weight at the *center* of each sample's mass -> unbiased
    cw_mid = (cw - 0.5 * w) / total
    target = pct / 100.0
    return float(np.interp(target, cw_mid, v))


def project_coil_to_cortex(
    matsimnibs: np.ndarray,
    elm_centers: np.ndarray,
    mode: str = "coil_nearest",
    max_depth_mm: float = 60.0,
) -> np.ndarray:
    """Return the ROI-center coordinate on the cortex under the coil.

    matsimnibs : 4x4 SimNIBS coil matrix (col4 = coil center, col3 = axis toward head).
    elm_centers: (N,3) gray-matter element centers.
    mode:
      "coil_nearest" (default, sign-robust): GM element nearest the coil center.
      "coil_axis": GM element nearest the coil-axis line, within max_depth of the
                   coil center along the axis (more principled; needs correct axis sign).
    """
    M = np.asarray(matsimnibs, dtype=float)
    c = M[:3, 3]
    d = elm_centers - c                    # (N,3) vectors coil-center -> elements

    if mode == "coil_nearest":
        return elm_centers[np.argmin(np.einsum("ij,ij->i", d, d))]

    if mode == "coil_axis":
        n = M[:3, 2]
        n = n / np.linalg.norm(n)
        t = d @ n                          # signed distance along axis
        perp = np.linalg.norm(d - np.outer(t, n), axis=1)
        mask = np.abs(t) < max_depth_mm    # sign-robust: near the coil either way
        if not mask.any():
            return elm_centers[np.argmin(np.einsum("ij,ij->i", d, d))]
        cand = np.where(mask)[0]
        return elm_centers[cand[np.argmin(perp[mask])]]

    raise ValueError(f"unknown mode {mode!r}")


def sphere_mask(elm_centers: np.ndarray, center: np.ndarray,
                radius_mm: float) -> np.ndarray:
    """Boolean mask of elements whose center is within radius of `center`."""
    return np.linalg.norm(elm_centers - np.asarray(center, float), axis=1) < radius_mm


def find_result_mesh(pathfem: str | Path, pos_index: int,
                     poslist_index: int = 1) -> Path:
    """Locate the per-position result mesh (the volume mesh containing |E|).

    SimNIBS names outputs like `<sub>_TMS_<poslist>-<pos:04d>_<coil>.msh`
    (and sometimes a `..._scalar.msh`). We glob by the zero-padded position
    index and prefer a top-level mesh that isn't an overlay/MNI/central export.
    """
    pathfem = Path(pathfem)
    pat = f"*_TMS_{poslist_index}-{pos_index:04d}_*.msh"
    cands = [Path(p) for p in glob.glob(str(pathfem / pat))]
    # exclude surface/volume-mapped exports; keep the main head mesh
    bad = ("central", "fsavg", "_MNI", "subject_overlays", "fsavg_overlays")
    main = [p for p in cands if not any(b in p.name for b in bad)]
    if not main:
        main = cands
    if not main:
        raise FileNotFoundError(
            f"No result mesh for position {pos_index:04d} in {pathfem} "
            f"(pattern {pat!r})")
    # prefer a '_scalar.msh' if present (smaller, |E| only), else first
    scalar = [p for p in main if p.name.endswith("_scalar.msh")]
    return (scalar or main)[0]


# ---------------------------------------------------------------------------
# SimNIBS-dependent extraction
# ---------------------------------------------------------------------------

@dataclass
class PeakResult:
    mesh: str
    mode: str
    pct: float
    radius_mm: float
    value: float                 # the |E| value fed to APEX (V/m)
    roi_center: List[float]
    n_elements_in_roi: int
    mean_in_roi: float
    whole_gm_pct: float          # same percentile over all GM (cross-check)
    peak_xyz: List[float] = dc_field(default_factory=lambda: [float("nan")] * 3)
    coil_cortex_xyz: List[float] = dc_field(default_factory=lambda: [float("nan")] * 3)
    extras: Dict[str, float] = dc_field(default_factory=dict)


def _get_magnE(gm) -> np.ndarray:
    """Return element-wise |E| for a cropped GM mesh, tolerant to field naming."""
    for k in _FIELD_KEYS:
        try:
            return np.asarray(gm.field[k][:], dtype=float)
        except Exception:
            continue
    # fall back to the vector field's norm
    for k in ("E", "e"):
        try:
            v = np.asarray(gm.field[k][:], dtype=float)
            return np.linalg.norm(v, axis=1)
        except Exception:
            continue
    raise KeyError(f"No |E| field found; available: {list(gm.field.keys())}")


def load_gm_field(result_msh: str | Path):
    """Load a result mesh, crop to GM, return (centers, volumes, magnE)."""
    import simnibs  # lazy: only needed on a machine with SimNIBS
    m = simnibs.read_msh(str(result_msh))
    gm = m.crop_mesh(GM_TAG)
    centers = np.asarray(gm.elements_baricenters()[:], dtype=float)
    vols = np.asarray(gm.elements_volumes_and_areas()[:], dtype=float)
    magnE = _get_magnE(gm)
    return centers, vols, magnE


def extract_peak(
    result_msh: str | Path,
    matsimnibs: Optional[np.ndarray] = None,
    mode: str = "coil_nearest",
    radius_mm: float = 10.0,
    pct: float = 99.9,
    coord: Optional[Sequence[float]] = None,
) -> PeakResult:
    """Extract the peak cortical |E| for APEX.

    mode:
      "coil_nearest"/"coil_axis": ROI center from the coil matrix (needs matsimnibs).
      "coord": ROI center at an explicit subject-space coordinate (needs `coord`).
      "whole_gm": percentile over the entire gray matter (no ROI).
    """
    centers, vols, magnE = load_gm_field(result_msh)

    whole = weighted_percentile(magnE, vols, pct)

    if mode == "whole_gm":
        roi_center = np.array([np.nan, np.nan, np.nan])
        roi = np.ones(magnE.shape, dtype=bool)
    else:
        if mode == "coord":
            if coord is None:
                raise ValueError("mode 'coord' requires `coord`")
            roi_center = np.asarray(coord, dtype=float)
        else:
            if matsimnibs is None:
                raise ValueError(f"mode {mode!r} requires `matsimnibs`")
            roi_center = project_coil_to_cortex(matsimnibs, centers, mode=mode)
        roi = sphere_mask(centers, roi_center, radius_mm)
        if not roi.any():  # widen once if the sphere caught nothing
            roi = sphere_mask(centers, roi_center, radius_mm * 3)

    val = weighted_percentile(magnE[roi], vols[roi], pct)
    mean_roi = float(np.average(magnE[roi], weights=vols[roi])) if roi.any() else float("nan")

    # location of the peak |E| element inside the ROI (informational)
    if roi.any():
        roi_local = np.where(roi)[0]
        peak_local = int(roi_local[int(np.argmax(magnE[roi]))])
        peak_xyz = [float(x) for x in centers[peak_local]]
    else:
        peak_xyz = [float("nan")] * 3

    # cortical point under the coil — deterministic from the coil matrix, used to
    # place the QC sphere so each target's sphere sits under its own coil (stable
    # even when the global-max element is noisy / far-from-cortex).
    if matsimnibs is not None:
        cc = project_coil_to_cortex(np.asarray(matsimnibs, dtype=float),
                                    centers, mode="coil_nearest")
        coil_cortex_xyz = [float(x) for x in cc]
    else:
        coil_cortex_xyz = [float("nan")] * 3

    return PeakResult(
        mesh=str(result_msh), mode=mode, pct=pct, radius_mm=radius_mm,
        value=val, roi_center=[float(x) for x in roi_center],
        n_elements_in_roi=int(roi.sum()), mean_in_roi=mean_roi,
        whole_gm_pct=whole, peak_xyz=peak_xyz, coil_cortex_xyz=coil_cortex_xyz,
    )
