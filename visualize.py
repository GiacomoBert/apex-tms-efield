"""
visualize.py — QC visualizations and file-opening helpers.

- Build a spherical mask (NIfTI) at each stim target's ROI/peak location on the
  subject's T1 (from the m2m folder) and open it in FreeSurfer's FreeView, which
  gives a 3D-capable view of the brain with the sphere highlighted.
- open_file(): open the results CSV (or anything) in the OS default app.

The sphere-mask geometry (`sphere_voxel_mask`) is pure-numpy and unit-tested
without nibabel/FreeSurfer.
"""

from __future__ import annotations

from pathlib import Path
from typing import List, Optional, Sequence, Tuple
import os
import re
import subprocess
import sys
import numpy as np


# FreeView colormaps cycled per target so the spheres are visually distinct.
_COLORMAPS = ["heat", "jet", "gecolor", "nih", "pet", "hot"]


# --------------------------------------------------------------------------
# geometry (pure numpy, testable)
# --------------------------------------------------------------------------

def sphere_voxel_mask(affine: np.ndarray, shape: Sequence[int],
                      center_xyz: Sequence[float], radius_mm: float) -> np.ndarray:
    """Boolean volume (shape) with True for voxels whose world coordinate is
    within `radius_mm` of `center_xyz`. Only a bounding box around the center is
    evaluated, so it is fast and memory-light even for 256^3 volumes."""
    affine = np.asarray(affine, dtype=float)
    shape = tuple(int(s) for s in shape[:3])
    center = np.asarray(center_xyz, dtype=float)
    mask = np.zeros(shape, dtype=bool)
    if not np.all(np.isfinite(center)):
        return mask

    inv = np.linalg.inv(affine)
    c_vox = inv @ np.array([center[0], center[1], center[2], 1.0])
    c_ijk = c_vox[:3]

    # voxel sizes (mm) from affine columns -> radius in voxels per axis
    vox_sizes = np.linalg.norm(affine[:3, :3], axis=0)
    vox_sizes[vox_sizes == 0] = 1.0
    r_vox = np.ceil(radius_mm / vox_sizes).astype(int) + 1

    lo = np.maximum(np.floor(c_ijk - r_vox).astype(int), 0)
    hi = np.minimum(np.ceil(c_ijk + r_vox).astype(int), np.array(shape) - 1)
    if np.any(hi < lo):
        return mask

    ii, jj, kk = np.meshgrid(
        np.arange(lo[0], hi[0] + 1),
        np.arange(lo[1], hi[1] + 1),
        np.arange(lo[2], hi[2] + 1),
        indexing="ij")
    vox = np.stack([ii.ravel(), jj.ravel(), kk.ravel(), np.ones(ii.size)], axis=1)
    world = vox @ affine.T          # (N,4)
    d = np.linalg.norm(world[:, :3] - center, axis=1)
    inside = d < radius_mm
    mask[ii.ravel()[inside], jj.ravel()[inside], kk.ravel()[inside]] = True
    return mask


# --------------------------------------------------------------------------
# NIfTI + FreeView
# --------------------------------------------------------------------------

def find_t1(m2m_dir: str | Path) -> Optional[Path]:
    m2m = Path(m2m_dir)
    for name in ("T1.nii.gz", "T1.nii", "T1_conform.nii.gz", "T1fs_conform.nii.gz"):
        if (m2m / name).is_file():
            return m2m / name
    hits = list(m2m.glob("*T1*.nii*"))
    return hits[0] if hits else None


def find_freeview() -> Optional[str]:
    """FreeView, cross-platform (macOS + Linux/WSL). See locate.py."""
    import locate
    return locate.freeview_bin()


def make_sphere_mask_nifti(t1_path: str | Path, center_xyz, radius_mm: float,
                           out_path: str | Path) -> Path:
    import nibabel as nib
    img = nib.load(str(t1_path))
    mask = sphere_voxel_mask(img.affine, img.shape, center_xyz, radius_mm)
    out = nib.Nifti1Image(mask.astype(np.uint8), img.affine, img.header)
    nib.save(out, str(out_path))
    return Path(out_path)


def visualize_spheres_freeview(
    m2m_dir: str | Path,
    spheres: Sequence[Tuple[str, Sequence[float], float]],
    out_dir: str | Path,
) -> Optional[Path]:
    """Build one sphere mask per (label, center_xyz, radius_mm) and open them
    over the T1 in FreeView (non-blocking, 3D-capable). Returns the T1 path used,
    or None if T1/FreeView are unavailable (prints the mask paths instead)."""
    t1 = find_t1(m2m_dir)
    if t1 is None:
        print("  (no T1 found in m2m folder — skipping sphere visualization)")
        return None

    viz_dir = Path(out_dir) / "qc_spheres"
    viz_dir.mkdir(parents=True, exist_ok=True)

    layers: List[str] = []
    for i, (label, center, radius) in enumerate(spheres):
        safe = re.sub(r"[^A-Za-z0-9._-]+", "_", str(label)).strip("_") or f"target{i}"
        mask_path = viz_dir / f"sphere_{safe}.nii.gz"
        try:
            make_sphere_mask_nifti(t1, center, radius, mask_path)
        except Exception as e:
            print(f"  (could not build sphere mask for {label}: {e})")
            continue
        cmap = _COLORMAPS[i % len(_COLORMAPS)]
        layers.append(f"{mask_path}:colormap={cmap}:opacity=0.5")

    fv = find_freeview()
    if fv is None or not layers:
        print("  (freeview not found — open these in any viewer over the T1:)")
        print("   T1:", t1)
        for lyr in layers:
            print("   ", lyr.split(":")[0])
        return t1

    cmd = [fv, "-v", str(t1), *layers]
    try:
        subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception as e:
        print(f"  (could not launch freeview: {e})")
    return t1


# --------------------------------------------------------------------------
# open a file in the OS default app (non-blocking)
# --------------------------------------------------------------------------

def open_file(path: str | Path) -> None:
    p = str(path)
    try:
        if sys.platform == "darwin":
            subprocess.Popen(["open", p])
        elif os.name == "nt":
            os.startfile(p)              # type: ignore[attr-defined]
        else:
            subprocess.Popen(["xdg-open", p])
    except Exception as e:
        print(f"  (could not open {p}: {e})")
