"""
full_pipeline.py — Whole pre-visit pipeline, end to end.

    DICOM/.tbz  ->  T1/T2 NIfTI  ->  recon-all  ->  CHARM (m2m)  ->  APEX dose

Each stage is resumable (skips if its output already exists). `--dry-run` prints
the full plan (every command) without executing anything heavy.

Run under SimNIBS python:
    /Applications/SimNIBS-4.1/bin/simnibs_python full_pipeline.py full_config.json
    /Applications/SimNIBS-4.1/bin/simnibs_python full_pipeline.py full_config.json --dry-run

Layout created under `work_dir`:
    work_dir/nifti/                 (converted T1/T2)
    work_dir/freesurfer/<id>/       (recon-all; this is SUBJECTS_DIR)
    work_dir/simnibs/m2m_<id>/      (CHARM head model)
and the dose step writes apex_<id>/ inside your chosen `out_dir`.

Skip DICOM by giving `t1` (and optional `t2`) directly. Skip the dose step with
`"skip_dose": true` (just build the m2m).
"""

from __future__ import annotations

from pathlib import Path
from typing import Dict, List, Optional, Sequence
import json
import sys


def run_full_pipeline(
    *,
    subject_id: str,
    work_dir: str | Path,
    # ---- inputs: either a DICOM folder/archive, or ready T1 (+T2) ----
    dicom_input: Optional[str | Path] = None,
    t1: Optional[str | Path] = None,
    t2: Optional[str | Path] = None,
    use_t2: bool = True,
    t1_hint: Optional[str] = None,
    t2_hint: Optional[str] = None,
    openmp: int = 4,
    # ---- dose step (same fields as apex_pipeline) ----
    brainsight_file: Optional[str | Path] = None,
    coil_file: Optional[str | Path] = None,
    out_dir: Optional[str | Path] = None,
    m1_target: Optional[str] = None,
    targets: Optional[Dict[str, str]] = None,
    didt_A_per_us: Optional[float] = None,
    rmt_pct: Optional[float] = None,
    radius_mm: float = 10.0,
    pct: float = 99.9,
    roi_mode: str = "whole_gm",
    percents: Sequence[float] = (120.0, 135.0),
    custom_pct: Optional[float] = None,
    open_meshes: bool = True,
    show_spheres: bool = True,
    open_csv: bool = True,
    # ---- control ----
    skip_dose: bool = False,
    dry_run: bool = False,
):
    log = lambda s: print(s, end="")
    work = Path(work_dir)
    subjects_dir = work / "freesurfer"
    simnibs_parent = work / "simnibs"

    print("=" * 70)
    print(f"FULL PIPELINE — subject {subject_id}"
          f"{'   [DRY RUN]' if dry_run else ''}")
    print(f"work_dir: {work}")
    print("=" * 70)

    import recon
    import charm

    # ---- Stage 1: T1 / T2 -------------------------------------------------
    print("\n--- Stage 1/4: T1 / T2 ---")
    if t1:
        t1p = Path(t1)
        t2p = Path(t2) if (t2 and use_t2) else None
        print(f"[dicom] using provided T1: {t1p}")
        print(f"[dicom] using provided T2: {t2p}")
    elif dicom_input:
        if dry_run:
            print(f"[dicom] would convert {dicom_input} -> T1/T2 (dcm2niix/mri_convert) "
                  f"and auto-classify. (skipped in dry-run)")
            t1p = work / "nifti" / "T1.nii.gz"
            t2p = (work / "nifti" / "T2.nii.gz") if use_t2 else None
        else:
            import dicom as dcm
            t1p, t2p, _series = dcm.resolve_t1_t2(
                dicom_input, work / "dicom_work",
                t1_hint=t1_hint, t2_hint=t2_hint, on_line=log)
            if not use_t2:
                t2p = None
    else:
        raise ValueError("Provide either `dicom_input` or `t1` (+ optional `t2`).")

    # ---- Stage 2: recon-all ----------------------------------------------
    print("\n--- Stage 2/4: FreeSurfer recon-all (hours) ---")
    recon.run_recon_all(subject_id, t1p, subjects_dir, t2p,
                        use_t2=use_t2, openmp=openmp, on_line=log, dry_run=dry_run)
    recon_dir = subjects_dir / subject_id

    # ---- Stage 3: CHARM ---------------------------------------------------
    print("\n--- Stage 3/4: SimNIBS CHARM (m2m head model) ---")
    m2m = charm.run_charm(subject_id, t1p, simnibs_parent, t2p,
                          fs_dir=recon_dir, use_t2=use_t2, on_line=log, dry_run=dry_run)

    # ---- Stage 4: APEX dose ----------------------------------------------
    print("\n--- Stage 4/4: APEX dose ---")
    if skip_dose:
        print("[dose] skip_dose=true — stopping after building the m2m.")
        print(f"\nm2m ready: {m2m}")
        return {"m2m": str(m2m)}

    dose_fields = dict(brainsight_file=brainsight_file, coil_file=coil_file,
                       out_dir=out_dir, m1_target=m1_target, targets=targets,
                       didt_A_per_us=didt_A_per_us, rmt_pct=rmt_pct)
    missing = [k for k, v in dose_fields.items() if v in (None, {}, "")]
    if missing:
        raise ValueError(f"Dose step needs these config fields: {missing} "
                         f"(or set skip_dose=true).")

    from apex_pipeline import run_apex_dose
    return run_apex_dose(
        m2m_dir=m2m, brainsight_file=brainsight_file, coil_file=coil_file,
        out_dir=out_dir, m1_target=m1_target, targets=targets,
        didt_A_per_us=didt_A_per_us, rmt_pct=rmt_pct, radius_mm=radius_mm, pct=pct,
        roi_mode=roi_mode, percents=percents, custom_pct=custom_pct,
        open_meshes=open_meshes, show_spheres=show_spheres, open_csv=open_csv,
        dry_run=dry_run)


def _main(argv: List[str]) -> int:
    if len(argv) < 2:
        print(__doc__)
        return 1
    cfgpath = Path(argv[1])
    if not cfgpath.is_file():
        print(f"Config file not found: {cfgpath}")
        return 1
    cfg = json.loads(cfgpath.read_text())
    dry = "--dry-run" in argv or cfg.pop("dry_run", False)
    run_full_pipeline(dry_run=dry, **cfg)
    return 0


if __name__ == "__main__":
    raise SystemExit(_main(sys.argv))
