# v3 — Full pre-visit pipeline

`v3_python` is a copy of the validated `v2_python` (the short-process dose tool) **plus** the
pre-visit stages, so the whole thing runs from one command:

```
DICOM / .tbz  ->  T1/T2 NIfTI  ->  recon-all  ->  CHARM (m2m)  ->  APEX dose
```

Everything in `v2_python` still works here unchanged (`dose_tool_gui.py`, `apex_pipeline.py`, …).
This folder just adds the stages that build the `m2m` for you.

## New modules
- `dicom.py` — DICOM (folder or `.tbz`) → T1/T2 NIfTI, auto-classified (name + TE/TR/TI). Prefers
  `dcm2niix`, falls back to FreeSurfer `mri_convert`. Override with `t1_hint`/`t2_hint`.
- `recon.py` — runs FreeSurfer `recon-all` (sources the FS env itself; resumable).
- `charm.py` — runs SimNIBS `charm` to build `m2m_<id>` (resumable).
- `full_pipeline.py` — chains all four stages; resumable at each; `--dry-run` prints the whole plan.

## The unified GUI  (recommended)

Double-click **`run_pipeline.command`** (or `<brew python> pipeline_gui.py`). One window, three
progressive stages — fill the earliest input you have; the rest is computed and the fields auto-fill:

1. **Anatomy** — pick a `.tbz`/DICOM → **① Extract & identify T1/T2**. It converts, auto-picks T1/T2,
   opens both in FreeView to review, and fills the T1/T2 fields. Wrong pick? Browse for the right file.
2. **Head model** — **② Build head model**. recon-all → CHARM run **detached in the background**, so you
   can close the window or let the Mac sleep; reopen it later and it resumes / fills the m2m field when
   done. "stop after recon-all" pauses before CHARM. Or paste an existing m2m to skip Stages 1–2.
3. **APEX dose** — the validated short-process step (M1 + targets → %MSO → CSV + Gmsh + FreeView spheres).

If a stage fails, it shows in the log; you can run recon-all/CHARM yourself and just paste the path in.

`stage_runner.py` handles the detached launch + resume; the GUI polls the log to track progress.

## Or the command line

```bash
cd v3_python
# 1) validate the plan without running anything heavy:
/Applications/SimNIBS-4.1/bin/simnibs_python full_pipeline.py full_config.example.json --dry-run
# 2) real run (recon-all takes hours — run overnight / in screen or tmux):
/Applications/SimNIBS-4.1/bin/simnibs_python full_pipeline.py my_full_config.json
```

Edit a copy of `full_config.example.json`. Two ways to supply the anatomy:
- **From DICOM:** set `dicom_input` to the DICOM folder or `.tbz` (leave `t1`/`t2` out).
- **From existing NIfTI:** add `"t1": ".../T1.nii.gz"` (and optional `"t2"`) and drop `dicom_input`.

Set `"skip_dose": true` to stop after building the `m2m` (no Brainsight/dose needed).

## Where things go (under `work_dir`)
```
work_dir/
  dicom_work/nifti/        converted T1/T2
  freesurfer/<id>/         recon-all output (this is SUBJECTS_DIR)
  simnibs/m2m_<id>/        CHARM head model
```
The dose step writes `apex_<id>/` inside your chosen `out_dir` (same as the short process).

## Resumability
Each stage checks for its own finished output and skips if present, so re-running after an
interruption continues where it left off. recon-all is Mac/Linux only (Windows → run in WSL).

## Notes
- **`pipeline_gui.py`** is the one unified GUI (it supersedes `dose_tool_gui.py`, which stays as a
  minimal fallback). Launch it via `run_pipeline.command`.
- T1/T2 auto-ID is heuristic — always eyeball the FreeView review and the `[dicom] T1 -> …` lines.
- recon-all resume: an interrupted run is continued with `recon-all -make all` (stale IsRunning lock
  cleared first), so a crash mid-way doesn't cost the whole run.
- Untested-on-Mac so far: the GUI's live wiring of the detached head-model stage — expect a rough
  edge or two on first real use; the underlying pieces (detached launch/poll, shell builders,
  classification, dose) are all unit-tested.
