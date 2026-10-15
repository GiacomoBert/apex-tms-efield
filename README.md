# APEX-MT TMS E-field dosing pipeline

A desktop pipeline that turns a structural MRI and a Brainsight coil-positioning
export into an individualized TMS dose using the **APEX MT** method (Caulfield et
al., 2021). It runs SimNIBS electric-field simulations from code — no manual GUI
steps — so a research assistant can operate it end to end. It is a general tool for
APEX-adjusted TMS dosing, not tied to any one study.

Given a subject's resting motor threshold (rMT) measured over M1, APEX computes the
machine output (% of maximum stimulator output, %MSO) that reproduces the same
cortical electric field at a non-motor target (e.g. IPL, DLPFC). The field is
estimated for M1 and for each target at the same stimulation intensity; because the
field scales linearly with the coil current rate (dI/dt), the APEX ratio is
independent of the exact dI/dt calibration.

## What it does

The unified GUI (`pipeline_gui.py`) runs three progressive stages; you supply the
earliest input you have and the rest is computed:

1. **Anatomy** — DICOM (folder or compressed archive) → T1/T2 NIfTI (via `dcm2niix`),
   with automatic T1/T2 identification.
2. **Head model** — FreeSurfer `recon-all` → SimNIBS `charm` → an `m2m_<id>` head
   model. (Can reuse an existing recon-all, or build from T1/T2 alone.)
3. **APEX dose** — parse the Brainsight targets → SimNIBS E-field simulation →
   peak cortical |E| per site → APEX %MSO → results CSV + QC views.

Already have an `m2m` head model? Point the GUI at it and jump straight to dosing.

## Requirements

- **FreeSurfer** (tested with 8.2.0 and 7.2.0) — for `recon-all`.
- **SimNIBS 4.1** (Intel Mac) — provides `charm`, `simnibs_python`, the Gmsh viewer,
  the MagVenture Cool-B65 coil model, and the numpy/pydicom/nibabel stack. (SimNIBS
  4.5+ dropped Intel-Mac support; 4.1 is also the first with `charm --fs-dir`.)
- **dcm2niix** — DICOM → NIfTI conversion.
- A Python with a working **tkinter** for the GUI window (the engine runs under
  `simnibs_python`).

Platforms: **macOS**, and **Windows via WSL2 (Ubuntu)** — see `INSTALL.md` §1b.
Native Windows is not supported (FreeSurfer is Linux-only). Full, step-by-step setup
for both is in **[`INSTALL.md`](INSTALL.md)**.

## Quick start

```bash
# macOS
./run_pipeline.command        # or:  "$(brew --prefix)/bin/python3" pipeline_gui.py

# Linux / Windows-WSL2
./run_pipeline.sh             # needs: sudo apt install python3-tk
```

Tool locations (SimNIBS, FreeSurfer, the coil, …) are auto-detected on both macOS
and Linux/WSL by `locate.py`. A command-line path (no GUI) is also available:

```bash
simnibs_python apex_pipeline.py example_config.json --dry-run   # validate
simnibs_python apex_pipeline.py example_config.json             # run
```

## Repository layout

| Area | Files |
|---|---|
| GUI / launchers | `pipeline_gui.py`, `run_pipeline.command`, `run_pipeline.sh` |
| Inputs | `brainsight.py`, `dicom.py`, `dicom_input.py` |
| Head model | `recon.py`, `charm.py`, `spacefree.py`, `stage_runner.py` |
| Simulation & dose | `simulate.py`, `extract.py`, `apex.py`, `apex_pipeline.py`, `full_pipeline.py` |
| Helpers | `locate.py`, `didt_ref.py`, `visualize.py`, `check_setup.py` |
| Configs / docs / tests | `*config*.json`, `INSTALL.md`, `SPEC.md`, `test_*.py` |

## Data & privacy

This repository contains **code only**. No MRI images, head models, or Brainsight
exports (which carry subject coordinates) are included; `.gitignore` is set up to
keep such files out. Do not commit subject data.

## Method reference

Caulfield KA, et al. *Four electric field modeling methods of Dosing Prefrontal
Transcranial Magnetic Stimulation (TMS): Introducing APEX MT dosimetry.*
**Brain Stimulation**, 2021. See `SPEC.md` for how this implementation maps to the
method (`apex.py` is the dosing math; `simulate.py`/`extract.py` the E-field side).

## Credits

This tool builds on the original SimNIBS E-field / CHARM dosing pipeline developed by
**Brice Passera** (MATLAB scripts plus a tkinter command-generator with an APEX
calculator, where the field simulation was run by hand). It is a cross-platform
Python reimplementation that fully automates that workflow — DICOM → recon-all →
CHARM → E-field simulation → APEX dose, with a GUI — by **Giacomo Bertazzoli**.
Please credit both when using or extending this work.

## Status & disclaimer

Research software, provided as-is under the MIT License (see `LICENSE`). It is not a
medical device and is not for clinical decision-making; validate outputs for your
own setup before use.
