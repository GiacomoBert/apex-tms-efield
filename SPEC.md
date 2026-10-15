# APEX-MT TMS E-field / Dosing Pipeline — Spec

Status: **draft for review** · Target platform: **macOS first** (Windows/WSL pinned) ·
Coil **MagVenture Cool-B65** (`Drakaki_BrainStim_2022/MagVenture_Cool-B65.ccd`)

**Environment (study Mac, confirmed):**
- `simnibs_python`: `/Applications/SimNIBS-4.1/bin/simnibs_python`  → **SimNIBS 4.1** (build target; keep 4.5-compatible — analysis/MATLAB machine is 4.5; standardize later for reproducibility)
- FreeSurfer: `/Applications/freesurfer/8.2.0`
- Coil: `/Applications/SimNIBS-4.1/simnibs/resources/coil_models/Drakaki_BrainStim_2022/MagVenture_Cool-B65.ccd`
- No Homebrew; `dcm2niix` not installed → DICOM handled in pure Python (see §4).
- Code is written version-tolerant: GM = tag `2` (not `ElementTags.GM`), and the field key is
  probed (`magnE` vs `E_magn`) since it differs across versions/mesh types.

Replaces the v1 macOS tkinter *command-generator* (`original/EfieldPipeline.py`) and the manual
SimNIBS-GUI step with a code-driven pipeline, ported from the working MATLAB
`my_matlab_script/simNIBS_BF_final.m`.

---

## 1. Goal

Given a subject's MRI and the Brainsight-navigated coil positions, compute the **APEX-adjusted
%MSO** for non-motor targets (IPL, DLPFC) — the machine output that reproduces, at the target, the
individual's motor-threshold E-field. Everything an RA touches must be validated and hard to misuse.

APEX (per Caulfield et al. 2021, Eq. 2):

    base %MSO = (rMT / E_target) * E_M1
    dose      = base * (percent / 100)   # e.g. 120, 135, or custom

where `E_M1` and `E_target` are the peak cortical |E| from SimNIBS with M1 and the target coil
positions simulated **at the same intensity**. Because |E| scales linearly with dI/dt, the ratio —
and thus the APEX % — is **invariant to the dI/dt calibration**. dI/dt is a direct RA input (read off
the MagVenture screen); no %MSO→dI/dt conversion function is needed.

---

## 2. ONE GUI, one engine  (revised 2026-07-23 per Giac)

A **single** tkinter GUI with a mode switch at the top:
- **"Start from DICOM"** — run the whole pipeline: DICOM/.tbz → NIfTI → T1/T2 ID → recon-all → CHARM →
  (produces m2m) → sim → extract → APEX.
- **"Use existing m2m"** — skip straight to sim → extract → APEX (the during-visit short path).

Choosing a mode shows/hides the relevant fields. Both modes share the same engine modules.
`dose_tool_gui.py` (built now) is effectively the "Use existing m2m" branch; it will be folded into
the unified GUI once the recon-all/CHARM orchestration exists. The GUI is a thin launcher that shells
out to `simnibs_python apex_pipeline.py` (and, later, the full-pipeline entry point), so the GUI itself
needs only tkinter — no numpy/simnibs in the GUI process.

### Core engine modules
- `brainsight.py` — parse export → `{name: matsimnibs}`. **Done & validated** (581 targets / 45 files,
  0 bad matrices). Axis flip `[-m0, m1, -m2, loc]`; reads only the target block.
- `dicom.py` — run `dcm2niix`; classify T1 vs T2 (see §4); user-confirm fallback.
- `simulate.py` — build SESSION/TMSLIST/POSITION, set coil + `matsimnibs` + `didt`, `run_simnibs`.
- `extract.py` — peak cortical |E| in a coil-derived ROI (see §3).
- `apex.py` — the dose math above; per target; base/120/135/custom.
- `report.py` — CSV (long+wide, matching MATLAB) + a human-readable per-subject PDF/HTML record.
- `paths.py` — locate SimNIBS / FreeSurfer / coil / dcm2niix; cross-platform; auto-detect + override.

---

## 3. E-field extraction (verified against SimNIBS 4.5/4.6 docs)

Default = **99.9th percentile of |E| in a 10 mm gray-matter sphere** under the coil. Both the radius
and the percentile (99.9 / 99 / 95 / mean) are settable in the UI.

ROI center is derived from the coil matrix (no separate coordinate input):
1. Coil center `c = matsimnibs[:3,3]`, coil normal `n = matsimnibs[:3,2]` (points toward head).
2. Cast from `c` along `n` to the GM surface (fallback: nearest GM element to `c`) → ROI center.
3. Sphere mask + percentile:

```python
import simnibs, numpy as np
m  = simnibs.read_msh(scalar_msh)
gm = m.crop_mesh(simnibs.ElementTags.GM)          # GM = tag 2
ctr = gm.elements_baricenters()[:]
vol = gm.elements_volumes_and_areas()[:]
E   = gm.field["magnE"][:]
roi = np.linalg.norm(ctr - roi_center, axis=1) < radius_mm
val = np.percentile(E[roi], pct)                  # default pct = 99.9
```

Alternative/cross-check mode: whole-GM 99.9th percentile (what `simNIBS_BF_final.m` used).

---

## 4. DICOM → NIfTI T1/T2 identification (zero new installs)

Everything needed is already on the study Mac: **pydicom 2.4.4** and **nibabel 3.2.2** in the SimNIBS
env, and **FreeSurfer `mri_convert`**. (Do NOT use `dicom2nifti` — it drags in `python-gdcm`, which
has no wheel for this Python 3.9 and fails to compile.)

- Also accept an **already-converted NIfTI** (T1 ± T2) and skip conversion entirely.
- **Classify** each series with `pydicom` headers (read with `stop_before_pixels=True`, so no pixel
  decompression / no gdcm):
  1. **Name match** — `SeriesDescription`/`ProtocolName` regex: T1 ← mprage|bravo|fspgr|spgr|t1;
     T2 ← space|cube|t2 (exclude flair/stir).
  2. **Physics fallback** — T1 ← `InversionTime` present & `EchoTime` < ~10 ms;
     T2 ← `EchoTime` > ~60 ms & long `RepetitionTime`.
  3. **Ambiguous / multiple candidates** → show the series list + key params (TE/TR/TI/desc)
     and ask the RA to assign T1 and T2.
- **Convert**: prefer `dcm2niix` (being installed via Homebrew — richer JSON + BIDS-style naming; its
  JSON can also drive the T1/T2 ID). Fall back to FreeSurfer `mri_convert <one_slice_of_series>
  T1.nii.gz` if dcm2niix is missing (handles compressed DICOM; FreeSurfer-native). Both are fine for
  recon-all + CHARM; the tool auto-detects which is available.

---

## 5. RA-proofing

- Target chosen from a **dropdown of names actually in the file** (names are freeform/inconsistent:
  `M1 (anatomical)`, `M1_anatomical`, `FDI 1.1`, `IPL New`, `Sample 244`, …) — no auto-guess. Show the
  target position + coil-to-scalp distance as a sanity check when selected.
- Validate every input (files exist, matrix orthonormal, rMT in range, dI/dt > 0).
- Gate each stage; never proceed on invalid input; never silently overwrite (resumable, like the
  MATLAB `completed_conditions.txt`).
- Preflight check: SimNIBS present, coil file present, m2m present, disk space.
- **Flag doses over the machine ceiling.** If the target field is much weaker than M1 (common for
  IPL), the APEX base can exceed 100 %MSO and 120/135% become physically impossible — the tool warns
  loudly rather than printing an unreachable number. (Implemented in `apex.py`.)
- **Auto-open each result mesh for visual QC.** After a simulation finishes, open the E-field `.msh`
  in SimNIBS's bundled Gmsh viewer so the RA can eyeball it (coil over the intended gyrus, field looks
  clean, no meshing artifacts) before trusting the dose. Method: `simnibs.read_msh(result).view(
  visible_fields="magnE").show()` (the `.view().show()` pattern is confirmed in the SimNIBS docs), or
  the SESSION open-in-gmsh option. For a multi-position run, open the M1 and target results.
- Auto-save the APEX numbers + all inputs + a field screenshot to a per-subject report for the record.

---

## 6. Cross-platform

- Mac: recon-all + CHARM + SimNIBS all native. **Build & test here first.**
- Windows (**pinned**): FreeSurfer already runs in WSL Ubuntu; SimNIBS can install there too, so the
  full pipeline runs inside WSL. CHARM/sim/extract/APEX are also fine on native Windows Python.
  No hard-coded drive letters; `pathlib` throughout.

---

## 7. Open items before build

- Mac paths — **provided** (see Environment block above).
- One-time setup on the study Mac: `pip install pydicom dicom2nifti` into the SimNIBS env (§4).
- (Optional) the shared Claude-chat code, if it tuned anything beyond `simNIBS_BF_final.m`.

## 8. Done (all offline-tested; SimNIBS-dependent parts pending Mac smoke-test)
- `brainsight.py` + `test_brainsight.py` — validated parser (581 targets / 45 files, 0 bad matrices).
- `apex.py` + `test_apex.py` — dose math, unit-tested vs the README example; dI/dt-invariance proven;
  multi-target + machine-ceiling warnings.
- `extract.py` — GM crop + coil-projected ROI + volume-weighted percentile |E|; pure-numpy helpers
  unit-tested (`test_extract.py`); simnibs imported lazily.
- `simulate.py` — SESSION/TMSLIST/POSITION from `matsimnibs` + dI/dt (A/µs→A/s), resumable, Gmsh QC open.
- `apex_pipeline.py` — end-to-end single-subject orchestrator (Brainsight→sim→extract→APEX→CSV+QC),
  with a `dry_run` pre-flight (validated on real BF001 data).
- `dicom_input.py` (+`test_dicom_input.py`) — accepts a DICOM folder OR a `.tbz`/`.tar.*`/`.zip`
  archive; safe extraction (path-traversal guarded) + DICOM discovery via DICM magic. Offline-tested.
- `check_setup.py` — automated preflight: reports OK/MISS/opt for simnibs/pydicom/nibabel, dcm2niix/
  mri_convert/recon-all/charm/gmsh, FREESURFER_HOME, coil; prints only the actions still needed.
- `INSTALL.md` — living, conditional Mac setup guide + troubleshooting (kept updated).
- SimNIBS run + analysis API verified against docs; environment/versions confirmed.

## 9. Build order (next)
1. **Mac smoke-test**: `simnibs_python apex_pipeline.py config.json` on one subject with an existing m2m.
2. `paths.py` (auto-detect SimNIBS/FS/coil/dcm2niix) + `dicom.py` (dcm2niix/mri_convert + pydicom T1/T2 ID).
3. recon-all + CHARM orchestration (executed, resumable) → the full-pipeline pre-visit stages.
4. Two tkinter GUIs (Dose tool first, then Full pipeline).
