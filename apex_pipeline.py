"""
apex_pipeline.py — End-to-end APEX dose for one subject (the "Dose Tool" core).

Chain:  Brainsight .txt  ->  SimNIBS sims (M1 + targets at one dI/dt)
        ->  peak cortical |E| per site  ->  APEX %MSO  ->  CSV + QC meshes.

Run under SimNIBS python, e.g.:
    /Applications/SimNIBS-4.1/bin/simnibs_python apex_pipeline.py config.json

Use dry_run to validate the Brainsight targets and preview the plan WITHOUT
launching SimNIBS (no simnibs import) — good for RA pre-flight.

`targets` is an OPEN dict {label: brainsight_target_name}: add as many sites as you
like (IPL, DLPFC, right DLPFC, occipital, ...). Each becomes one simulation and
gets its own APEX dose relative to M1. `out_dir` must be a NEW/empty folder (never
the folder that holds your m2m/DICOM) — the tool refuses to overwrite data folders.

`roi_mode`: "whole_gm" (default; matches simNIBS_BF_final.m + SimNIBS's own summary),
or "coil_nearest"/"coil_axis" to restrict to a sphere under the coil, or "coord".

Example config.json:
{
  "m2m_dir": "/path/to/simnibs/m2m_SUBJECT",
  "brainsight_file": "/path/to/SUBJECT_brainsight.txt",
  "coil_file": "/Applications/SimNIBS-4.1/simnibs/resources/coil_models/Drakaki_BrainStim_2022/MagVenture_Cool-B65.ccd",
  "out_dir": "/path/to/results/sim_SUBJECT",
  "m1_target": "M1 (anatomical)",
  "targets": {"IPL": "IPL", "DLPFC": "DLPFC", "R_DLPFC": "DLPFC right", "Occipital": "OCC"},
  "didt_A_per_us": 87.0,
  "rmt_pct": 60,
  "radius_mm": 10.0, "pct": 99.9, "roi_mode": "whole_gm",
  "percents": [120, 135], "custom_pct": 105,
  "open_meshes": true
}
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple
import csv
import json
import sys

from brainsight import parse_brainsight_txt
from apex import apex_multi, ApexResult

# If the peak |E| sits more than this far from the cortex point under the coil
# centre, flag it (a strong coil tilt / off-target hotspot — the peak is under a
# wing, not the target; whole_gm over-reads it and the dose is deflated).
TILT_WARN_MM = 20.0


@dataclass
class PlannedPosition:
    label: str          # "M1", "IPL", "DLPFC"
    bs_name: str        # the Brainsight target name chosen
    matsimnibs: object  # 4x4 np.ndarray
    loc: object         # (3,) position (for the pre-flight sanity print)


def plan_positions(brainsight_file: str | Path, m1_target: str,
                   targets: Dict[str, str]) -> List[PlannedPosition]:
    """Resolve chosen Brainsight target names -> coil matrices. Raises a clear
    error (listing available names) if a name is missing."""
    bs = parse_brainsight_txt(brainsight_file)
    avail = list(bs.keys())

    def get(nm: str) -> "tuple":
        if nm not in bs:
            raise KeyError(
                f"Target {nm!r} not found in {Path(brainsight_file).name}. "
                f"Available targets: {avail}")
        return bs[nm].matsimnibs, bs[nm].loc

    order: List[Tuple[str, str]] = [("M1", m1_target)] + list(targets.items())
    planned = []
    for label, nm in order:
        mat, loc = get(nm)
        planned.append(PlannedPosition(label, nm, mat, loc))
    return planned


def run_apex_dose(
    *,
    m2m_dir: str | Path,
    brainsight_file: str | Path,
    coil_file: str | Path,
    out_dir: str | Path,
    m1_target: str,
    targets: Dict[str, str],
    didt_A_per_us: float,
    rmt_pct: float,
    radius_mm: float = 10.0,
    pct: float = 99.9,
    roi_mode: str = "whole_gm",
    coord: Optional[Sequence[float]] = None,   # sphere center for roi_mode='coord'
    percents: Sequence[float] = (120.0, 135.0),   # base itself is the 100% dose
    custom_pct: Optional[float] = None,
    open_meshes: bool = True,
    show_spheres: bool = True,
    open_csv: bool = True,
    make_subdir: bool = True,
    overwrite: bool = False,
    dry_run: bool = False,
):
    """Full single-subject APEX. If dry_run, validate + preview only."""
    planned = plan_positions(brainsight_file, m1_target, targets)

    print("Planned coil positions (Brainsight -> SimNIBS):")
    for pp in planned:
        x, y, z = (float(pp.loc[0]), float(pp.loc[1]), float(pp.loc[2]))
        print(f"  {pp.label:6s} <- {pp.bs_name!r:22s} @ [{x:7.1f},{y:7.1f},{z:7.1f}] mm")
    print(f"dI/dt = {didt_A_per_us} A/us (all positions) | rMT = {rmt_pct} %MSO | "
          f"ROI = {roi_mode}, {radius_mm}mm sphere, {pct}th pct |E|")

    if roi_mode == "coord":
        if coord is None or len(coord) != 3:
            raise ValueError("roi_mode 'coord' requires a 3-number coord [x, y, z] "
                             "(subject/scanner space, same as the m2m).")
        print(f"ROI center (coord, all sites): [{float(coord[0]):.1f}, "
              f"{float(coord[1]):.1f}, {float(coord[2]):.1f}] mm")

    if dry_run:
        print("\n[dry_run] validated Brainsight targets; SimNIBS not launched.")
        return None

    # --- heavy imports only when actually running ---
    import math
    from simulate import (Position, run_simulations, open_results_in_gmsh,
                          dedicated_output_dir)
    from extract import extract_peak, find_result_mesh
    from visualize import visualize_spheres_freeview, open_file

    # Treat the chosen out_dir as a PARENT and make our own subfolder inside it,
    # so the user can pick any folder (even a non-empty one) without risk.
    if make_subdir:
        subject = Path(m2m_dir).name
        if subject.startswith("m2m_"):
            subject = subject[len("m2m_"):]
        out_dir = dedicated_output_dir(out_dir, subject)
        print(f"Output folder (created inside your selection): {out_dir}")

    positions = [Position(pp.label, pp.matsimnibs, didt_A_per_us) for pp in planned]
    run = run_simulations(m2m_dir, coil_file, positions, out_dir, overwrite=overwrite)
    print(f"\nSimulations {'reused' if run.reused else 'completed'}: {run.pathfem}")

    idx_by_label = {name: i for i, name in run.mapping.items()}
    mat_by_label = {pp.label: pp.matsimnibs for pp in planned}

    peaks = {}
    meshes = {}
    for pp in planned:
        i = idx_by_label[pp.label]
        msh = find_result_mesh(run.pathfem, i)
        meshes[pp.label] = msh
        peaks[pp.label] = extract_peak(
            msh, matsimnibs=mat_by_label[pp.label],
            mode=roi_mode, radius_mm=radius_mm, pct=pct, coord=coord)
        pk = peaks[pp.label]
        print(f"  {pp.label:6s} peak |E| = {pk.value:7.2f} V/m "
              f"(whole-GM {pk.whole_gm_pct:6.2f}; n_roi={pk.n_elements_in_roi})")

    e_m1 = peaks["M1"].value
    e_targets = {pp.label: peaks[pp.label].value for pp in planned if pp.label != "M1"}
    results: List[ApexResult] = apex_multi(
        rmt_pct, e_m1, e_targets, percents=tuple(percents), custom_pct=custom_pct)

    print("\n=== APEX dose (%MSO) ===")
    for r in results:
        line = f"  {r.target:6s} 100%={r.base_pct:6.1f}"
        for k, v in r.doses.items():
            line += f"  {k}={v:6.1f}"
        print(line)
        for w in getattr(r, "warnings", []):
            print(f"    ! {w}")

    # --- coil-tilt / off-target QC: distance from the peak |E| to the cortex
    #     point directly under the coil centre. Large => the hotspot is off the
    #     target (tilt), so whole_gm over-reads it. Flags go into the CSV warnings.
    def _fin(c):
        return all(isinstance(x, (int, float)) and math.isfinite(x) for x in c)
    def _dist(a, b):
        return (math.sqrt(sum((x - y) ** 2 for x, y in zip(a, b)))
                if (_fin(a) and _fin(b)) else float("nan"))
    tilt = {pp.label: _dist(peaks[pp.label].peak_xyz, peaks[pp.label].coil_cortex_xyz)
            for pp in planned}
    print("\n=== coil-tilt QC (peak |E| distance from coil-centre cortex point) ===")
    for pp in planned:
        d = tilt[pp.label]
        strong = math.isfinite(d) and d > TILT_WARN_MM
        print(f"  {pp.label:6s} {d:6.1f} mm" + ("   <-- STRONG tilt / off-target hotspot" if strong else ""))
    m1_d = tilt.get("M1", float("nan"))
    for r in results:
        d = tilt.get(r.target, float("nan"))
        if math.isfinite(d) and d > TILT_WARN_MM:
            r.warnings.append(  # type: ignore[attr-defined]
                f"peak |E| is {d:.0f} mm from the coil-centre cortex point — possible coil "
                f"tilt / off-target hotspot; consider ROI mode 'coil_nearest'")
        if math.isfinite(m1_d) and m1_d > TILT_WARN_MM:
            r.warnings.append(  # type: ignore[attr-defined]
                f"M1 peak |E| is {m1_d:.0f} mm off the coil centre — E_M1 may be off-target")

    csv_path = Path(out_dir) / "apex_results.csv"
    _write_csv(csv_path, results, peaks, rmt_pct, pct=pct)
    print(f"\nSaved {csv_path}")

    if open_meshes:
        # non-blocking: opens each result mesh in its own Gmsh window and returns
        open_results_in_gmsh([meshes[pp.label] for pp in planned])

    if show_spheres:
        # QC sphere at the cortical point UNDER THE COIL (deterministic per coil,
        # so each target's sphere sits under its own coil and matches Gmsh).
        def _finite(c):
            return all(isinstance(x, (int, float)) and math.isfinite(x) for x in c)
        spheres = []
        for pp in planned:
            pk = peaks[pp.label]
            if roi_mode == "coord" and _finite(pk.roi_center):
                center = pk.roi_center            # show the sphere at the entered coordinate
            else:
                center = (pk.coil_cortex_xyz if _finite(pk.coil_cortex_xyz)
                          else pk.roi_center if _finite(pk.roi_center)
                          else pk.peak_xyz)
            spheres.append((pp.label, center, radius_mm))
        visualize_spheres_freeview(m2m_dir, spheres, out_dir)

    if open_csv:
        open_file(csv_path)

    return results, peaks


def _write_csv(path: Path, results, peaks, rmt_pct: float, pct: float = 99.9) -> None:
    # column suffix naming the percentile actually used, e.g. "99.9pct". The base
    # %MSO IS the 100% dose, so it's labelled dose_100% (no redundant column). |E|
    # columns state which percentile they report.
    p = f"{pct:g}pct"
    rows = []
    for r in results:
        pk = peaks[r.target]
        row = {
            "target": r.target,
            "rMT_%MSO": round(r.rmt_pct, 2),
            f"E_M1_{p}_Vm": round(r.e_m1, 3),
            f"E_target_{p}_Vm": round(r.e_target, 3),
            "dose_100%_%MSO": round(r.base_pct, 2),
        }
        for k, v in r.doses.items():                # 120%, 135%, custom (e.g. 105%)
            row[f"dose_{k}_%MSO"] = round(v, 2)
        row["roi_center"] = ";".join(f"{c:.1f}" for c in pk.roi_center)
        row["coil_cortex_xyz"] = ";".join(f"{c:.1f}" for c in pk.coil_cortex_xyz)
        row["peak_xyz"] = ";".join(f"{c:.1f}" for c in pk.peak_xyz)
        row[f"whole_gm_{p}_Vm"] = round(pk.whole_gm_pct, 3)
        row["warnings"] = " | ".join(getattr(r, "warnings", []))
        rows.append(row)
    if not rows:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)


def _main(argv: List[str]) -> int:
    if len(argv) < 2:
        print(__doc__)
        return 1
    cfgpath = Path(argv[1])
    if not cfgpath.exists():
        print(f"Config file not found: {cfgpath}")
        return 1
    if cfgpath.is_dir():
        print(f"Expected a JSON config file but got a directory: {cfgpath}\n"
              f"Point at the .json file inside it.")
        return 1
    cfg = json.loads(cfgpath.read_text())
    dry = "--dry-run" in argv or cfg.get("dry_run", False)
    cfg.pop("dry_run", None)
    run_apex_dose(dry_run=dry, **cfg)
    return 0


if __name__ == "__main__":
    raise SystemExit(_main(sys.argv))
