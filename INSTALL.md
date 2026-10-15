# APEX-MT TMS E-field Pipeline — Setup Guide (macOS + Windows/WSL)

**Living document — updated as we hit new steps or fixes.** Last updated: 2026-07-28.

This guide sets up a new Mac to run the pipeline (FreeSurfer → CHARM → SimNIBS E-field → APEX dose).
It is written to be followed by someone new to these tools. It is **conditional**: check each
component first and install **only the ones that are missing**. If a machine already has Homebrew,
FreeSurfer, or dcm2niix, skip those parts.

---

## 0. Fast path — let the checker tell you what is needed

Copy the `v3_python/` folder to the Mac, then run the automated checker with the SimNIBS python:

```bash
/Applications/SimNIBS-4.1/bin/simnibs_python check_setup.py
```

It prints every component as `OK` / `MISS` / `opt` and lists **only the actions you still need**.
Do those, then re-run it until it reports `RESULT: ready to run the dose pipeline`. The sections
below are the manual reference for each item it flags.

Alternatively, once the graphical interface can open (this needs `python-tk`; see §1.6), you can
launch it and click **"Check environment"** in the top-right corner — it runs the same check and
prints the report in the interface's message area.

---

## 1. Components (check first, install only if missing)

| Component | Needed for | Check | If missing |
|---|---|---|---|
| FreeSurfer (tested 8.2.0 / 7.2.0) | building the head model (`recon-all`) | `ls /Applications/freesurfer/*/bin/recon-all` | §1.1 |
| SimNIBS 4.1 (Intel Mac) | everything (charm, simnibs_python, gmsh, coils, numpy/pydicom/nibabel) | `ls /Applications/SimNIBS-*/bin/simnibs_python` | §1.2 |
| pydicom, nibabel | reading DICOM + identifying T1/T2 | `simnibs_python -c "import pydicom,nibabel"` | §1.3 |
| Homebrew | installing dcm2niix and python-tk | `brew --version` | §1.4 |
| dcm2niix | **required** — converting DICOM to NIfTI | `which dcm2niix` | §1.5 |
| python-tk (Homebrew) | the graphical interface window (working tkinter) | `"$(brew --prefix)/bin/python3" -c "import tkinter"` | §1.6 |

### 1.1 FreeSurfer
FreeSurfer runs `recon-all`, which reconstructs the cortical surfaces from the T1w (and, optionally,
T2w) image. This pipeline has been tested with **FreeSurfer 8.2.0** and **7.2.0**.

Installing FreeSurfer on macOS has extra steps compared with other platforms: it requires **XQuartz**
(an X11 display server), and macOS security settings must be adjusted so the programs are allowed to
run. Follow the official macOS walkthrough step by step:

- macOS install instructions (including XQuartz): <https://surfer.nmr.mgh.harvard.edu/fswiki//FS7_mac>
- Downloads: <https://surfer.nmr.mgh.harvard.edu/fswiki/rel7downloads>
- Main site (all documentation): <https://surfer.nmr.mgh.harvard.edu>

FreeSurfer needs a free license file, obtained from the FreeSurfer website. Place it **inside the
FreeSurfer folder for the specific version you installed** — i.e. in `$FREESURFER_HOME`. For example,
for version 8.2.0 the file goes at `/Applications/freesurfer/8.2.0/license.txt`.

**Make each new Terminal load FreeSurfer automatically** (so it prints the `FREESURFER_HOME` banner)
by appending the block below to your shell start-up file: `~/.bash_profile` for the bash shell, or
`~/.zprofile` if the account uses zsh. Change the version number to the one you installed.

```bash
cat >> ~/.bash_profile << 'EOF'

# FreeSurfer setup
export FREESURFER_HOME=/Applications/freesurfer/8.2.0
export SUBJECTS_DIR=$FREESURFER_HOME/subjects
source $FREESURFER_HOME/SetUpFreeSurfer.sh
EOF
source ~/.bash_profile
```

### 1.2 SimNIBS
Install SimNIBS from <https://simnibs.github.io/simnibs>, and note the install path (for example
`/Applications/SimNIBS-4.1`). SimNIBS provides `simnibs_python` (the interpreter that runs the
simulation engine), `charm` (builds the head model), the Gmsh viewer, the MagVenture Cool-B65 coil
model, and a Python environment that already includes numpy, scipy, pydicom, and nibabel.

This pipeline was developed and tested on an **Intel-based Mac with SimNIBS 4.1**. Two points to be
aware of when choosing a version (both confirmed in the SimNIBS changelog):

- SimNIBS **discontinued support for Intel Macs in version 4.5**. On an Intel Mac use SimNIBS **4.1**;
  version 4.5 and later require a Mac with Apple Silicon (an M-series chip).
- SimNIBS **4.1** is also the first version that added the `--fs-dir` option to `charm`, which this
  pipeline uses to bring the FreeSurfer surfaces into the head model. So 4.1 is the minimum version
  in all cases.

### 1.3 Python versions and dependencies
Two Python interpreters are used, by design:

- The **simulation engine** runs under SimNIBS's own bundled Python (**Python 3.9** in SimNIBS 4.1).
  This is installed together with SimNIBS — you do not install it separately.
- The **graphical interface window** runs under Homebrew's Python (tested with **Python 3.14**),
  because it needs a working Tk display library (see §1.6).

The engine needs the `pydicom` and `nibabel` packages, which are normally already present in the
SimNIBS environment. Verify:
```bash
/Applications/SimNIBS-4.1/bin/simnibs_python -c "import pydicom, nibabel; print('ok')"
```
If either is missing:
```bash
/Applications/SimNIBS-4.1/bin/simnibs_python -m pip install pydicom nibabel
```
**Do NOT install `dicom2nifti`** — it pulls in `python-gdcm`, which will not build on this Python 3.9
(see Troubleshooting). DICOM conversion is done with `dcm2niix` (§1.5).

### 1.4 Homebrew (to install dcm2niix)
Homebrew is a package manager for macOS. Here it is the simplest way to install `dcm2niix` (§1.5) and
`python-tk` (§1.6). Skip this step if `brew --version` already prints a version. To install it you
need: **admin rights** (the installer runs `sudo`), the **Xcode Command Line Tools**, macOS 14+, and
the **bash** shell.
```bash
uname -m        # arm64 = Apple Silicon (prefix /opt/homebrew); x86_64 = Intel (/usr/local)
sudo -v         # if this refuses your password, you are NOT admin -> use §1.5 instead
xcode-select --install   # if the CLT popup appears, click through (~1 GB)
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
# then put brew on PATH (Apple Silicon shown):
echo 'eval "$(/opt/homebrew/bin/brew shellenv)"' >> ~/.bash_profile
eval "$(/opt/homebrew/bin/brew shellenv)"
```

### 1.5 dcm2niix (required — converts DICOM to NIfTI)
`dcm2niix` converts the scanner's DICOM images into the NIfTI files the rest of the pipeline uses. It
is **required**. Skip this step only if `which dcm2niix` already finds it. Install it one of two ways:
- **With Homebrew (recommended):** `brew install dcm2niix` (needs §1.4).
- **Without admin rights:** download the prebuilt macOS `dcm2niix` binary from its official releases,
  unzip it, and put the binary somewhere on your PATH (no compiler and no `sudo` required).

### 1.6 python-tk — the GUI's Tk  (**required for the GUI**)
The GUI window needs a Python with a *working* tkinter. On this Mac neither the system python nor the
SimNIBS python could provide one (see Troubleshooting for why). The fix that works:
```bash
brew install python-tk
"$(brew --prefix)/bin/python3" -c "import tkinter; print('tk', tkinter.TkVersion)"   # expect tk 8.6/9.0
```
Then launch the GUI with that interpreter (or just use `run_dose_tool.command`, which finds it).
The command-line pipeline (`apex_pipeline.py`) does **not** need this — only the GUI does.

---

## 1b. Running on Windows (via WSL2 / Ubuntu)

The pipeline is written for Unix (bash, symlinks, `rsync`, detached background jobs), so on Windows it
runs **inside WSL2** (the Ubuntu-on-Windows subsystem) — not on native Windows. recon-all is Linux-only
and must run in WSL anyway, and SimNIBS officially lists *"Ubuntu 22.04 running under Windows 11 WSL2"*
as a tested platform. Treat the WSL Ubuntu as a Linux machine and install the same components there:

1. **WSL2 + Ubuntu** (Windows 11 includes WSLg, so GUI windows — the pipeline window, FreeView, Gmsh —
   display with no extra X server). `wsl --install -d Ubuntu` from an admin PowerShell if not present.
2. **FreeSurfer** for Linux, inside WSL (you already run it there). Set `FREESURFER_HOME` and source
   `SetUpFreeSurfer.sh` in `~/.bashrc` (§1.1 block, Linux paths).
3. **SimNIBS** for Linux, inside WSL (its installer adds `simnibs_python`/`charm` to your `PATH`).
4. **dcm2niix** (`sudo apt install dcm2niix` or the official binary) and **tkinter**
   (`sudo apt install python3-tk`).

Then launch with **`run_pipeline.sh`** (`chmod +x run_pipeline.sh` once, then `./run_pipeline.sh`) —
the Linux equivalent of `run_pipeline.command`. Tool locations are auto-detected on both macOS and
Linux/WSL (via `locate.py`: it checks `PATH`, `FREESURFER_HOME`, and the usual install dirs such as
`~/SimNIBS-*`, `/usr/local/freesurfer`), so you normally don't set any paths by hand; if a field is
blank, point `simnibs_python` at your WSL SimNIBS and the rest follows.

Two practical notes:
- **Work on the WSL filesystem** (e.g. `~/…`), not a Windows drive (`/mnt/c/…`): WSL↔Windows-drive I/O
  is much slower, which matters for recon-all's hours of small-file work. The build already happens in
  a local temp folder (`~/APEX_local`) and copies the finished m2m back, so pointing the Work folder at
  a `/mnt/…` share still works — it just copies at the end.
- Native Windows (no WSL) is **not** supported: recon-all can't run there and the code depends on Unix
  shell features.

---

## 2. DICOM input — folders and `.tbz` archives

The pipeline accepts either a **folder of DICOMs** or a **compressed archive**. Scanner exports often
arrive as `.tbz` (tar + bzip2). Supported: `.tbz/.tbz2/.tar.bz2`, `.tgz/.tar.gz`, `.tar.xz/.txz`,
`.tar`, `.zip`. If you select an archive where a DICOM folder is expected, the tool extracts it
safely and finds the DICOMs (`dicom_input.py`).

Manual equivalent (what we ran by hand) if you ever need it:
```bash
tar -xjf '/path/to/scan_007936.tbz' -C '/path/to/destination/'   # -j = bzip2 (.tbz)
```

---

## 2b. Exporting the correct .txt from Brainsight  *(TODO — fill exact menu steps)*

The tool reads the **Targets** block of a Brainsight text export. For it to work, the export must:
- Be in the **NIfTI:Scanner** coordinate system (matches the T1 used to build the m2m — a different
  coordinate system will put the coil in the wrong place). The file header should read
  `# Coordinate system: NIfTI:Scanner`.
- Contain the **planned targets with orientation**, i.e. the block headed
  `# Target Name  Loc. X  Loc. Y  Loc. Z  m0n0 … m2n2` (name + position + three orientation vectors),
  not just recorded samples.
- Be **tab-delimited** (the default), units mm/degrees.
- Name the targets clearly — each Brainsight target name becomes a selectable name in the GUI
  (e.g. an M1 hotspot target, "IPL", "DLPFC"). If M1 and the sites are in **separate** Brainsight
  files, name them `<subj>_M1.txt` and `<subj>_IPL.txt` (the pipeline also handles that split).

> **TODO (Giac to verify on the actual Brainsight 2.4.x station):** exact click-path —
> likely `File → Export → Targets`, then set the coordinate system to **NIfTI:Scanner**. Capture the
> real menu sequence + a screenshot here so an RA can reproduce it every time.

## 2c. Head model (Stage 2) — how much you need to run

The dose step only needs an **`m2m_<id>` folder** (SimNIBS's head model). There are several ways to
get one; pick the lightest that fits what you already have. All are driven from **Stage 2** of the
unified GUI (`pipeline_gui.py`).

- **Full (recommended): recon-all → CHARM.** Give a T1w (T2w optional). recon-all builds the
  FreeSurfer surfaces (~several hours) and CHARM imports them (`--fs-dir`) for the most accurate
  cortical surface. This is the default.
- **CHARM only, no recon-all (tick "skip recon-all").** CHARM segments the head straight from the
  T1w — **or T1w + T2w** — with **no FreeSurfer** and no `--fs-dir`. Much faster (tens of minutes, not
  hours). Trade-off: the cortical surface is less accurate than with FreeSurfer, which can slightly
  change the E-field near the target. Fine for a quick look; use the full path for the real dosing.
- **Reuse an existing recon-all.** Paste a finished FreeSurfer subject folder into the **"recon-all
  folder"** field. recon-all is skipped and CHARM uses it via `--fs-dir`. **Completion is judged by
  the log, not by folder presence:** the tool reads `scripts/recon-all.log` and requires the
  `"finished without error"` marker — a missing/half-finished recon is rejected up front (GUI) *and*
  re-checked at run time in the background job, so CHARM never runs against a partial recon. (The
  auto-filled "recon-all folder" after a local run is only set once this same log check passes.)
- **Already have an m2m? Skip Stage 2 entirely.** Paste the `m2m_<id>` folder into the **"m2m
  folder"** field (Stage 2) and go straight to **Stage 3 dosing**. Nothing is rebuilt.

**T1w is always required** (it's CHARM's first input and the anatomical reference). A T2w is optional
but improves the pial surface. **A FLAIR can stand in for the T2w** — put the FLAIR in the T2w field;
it's a fine second input for CHARM's segmentation and for recon-all's `-T2pial` refinement. (If you
only have a FLAIR and no true T2w, that's better than nothing; a true T2w is still preferable.)

The GUI validates these before launching the multi-hour background job: it won't start with no
recon-all input, with a CHARM run missing its T1w, with a bad "recon-all folder", or if `charm`
isn't found.

**What pressing ② actually does (the decision).** When you press ②, the tool first looks for existing
**complete** outputs — a recon-all whose log ends in `finished without error`, and an `m2m` that
contains `<id>.msh` — checking the **work folder, the local temp folder, and the pasted "recon-all
folder" field**. It then applies these rules, in order (first match wins):

1. **A complete `m2m` already exists** and no re-run box is ticked → **does nothing**: it fills the
   m2m field and stops (you can go straight to Stage 3). No background job is launched.
2. **"stop after recon-all" is ticked** → runs **recon-all only** (no CHARM); if recon-all is already
   complete, it does nothing.
3. **"skip recon-all" is ticked, or "recon-all folder" is un-ticked under CHARM inputs** → runs
   **CHARM only** from T1(/T2), no FreeSurfer, no `--fs-dir`.
4. **A complete recon-all exists and "re-run recon-all" is not ticked** → **skips recon-all, reuses
   it** (`--fs-dir`), and runs **CHARM** (this is the "I already have the recon, just build the m2m"
   case). recon-all is **not** re-run.
5. **Otherwise** → runs **recon-all fresh**, then CHARM.

Whenever a complete recon-all is found and not re-run (rules 1–4), its path is placed in the
**"recon-all folder"** field for visualisation.

**Force a rebuild.** Tick the Stage-2 "re-run" boxes:

- **re-run recon-all** — redo FreeSurfer even if it finished (deletes the subject, starts fresh; since
  the recon changes, CHARM is rebuilt too).
- **re-run CHARM (`--forcerun`)** — rebuild the `m2m` even if complete, reusing the existing recon-all
  (recon-all is **not** re-run). `charm` needs `--forcerun` to overwrite an existing `m2m_<id>`; the
  tool also adds it automatically if it finds a *partial* leftover `m2m`, so an interrupted CHARM
  re-runs cleanly. (If you tick re-run CHARM but there is **no** completed recon-all and "recon-all
  folder" is still ticked, recon-all has to run first — CHARM needs a head model to build from.)

**CHARM affine fix (`--forceqform` / `--forcesform`).** If CHARM's segmentation looks wrong because
the initial registration to MNI space went off (spurious tissue borders in `results.html`), you can
force CHARM to trust the NIfTI header's qform or sform for that initial alignment. Tick **one** of
`--forceqform` / `--forcesform` (not both — they conflict; the GUI blocks that). Leave both off for
normal runs.

**Where the build runs (auto-detected).** recon-all needs a space-free, symlink-capable location.
When you press ②, the tool probes your **work folder** (checks the path has no spaces and that it can
actually create a symlink there):

- **Work folder is local + symlink-capable + space-free** → it builds **directly in the work folder**
  (`<work>/freesurfer`, `<work>/simnibs`). No temp copy, nothing to clean up — the m2m is written
  straight where the dose step reads it. ("delete local temp" is ignored; there is no temp.)
- **Work folder is a network share and/or has spaces** (e.g. `/Volumes/Lab Data/...`)
  → it builds in the local **`~/APEX_local/<subject>`** temp folder and copies the finished m2m back
  to `<work>/simnibs`, exactly as before.

The read-only **"Build folder"** field shows the path actually used (work folder or the temp folder).
It fills when you press ② and when the GUI reopens on a running build, so you always know where the
in-progress FreeSurfer/CHARM output lives.

**If recon-all succeeds but CHARM fails** (the common case — recon-all takes hours and usually works;
CHARM is quicker but can trip on affine registration): recon-all runs first and only if it returns 0
does CHARM start, so a CHARM failure never loses the recon-all output. On a CHARM failure:

- The **recon-all output is kept** in the local temp folder, and its folder path is **auto-filled into
  "recon-all folder"**. Nothing partial is pushed to the share (only a *complete* `m2m` — one with the
  final `<id>.msh` — is ever copied), and the **local temp is not deleted** even if "delete local temp
  when done" is ticked (the guard requires the m2m to be confirmed on the share first).
- The GUI reports **FAILED** and tells you recon-all is preserved. **Press ② again and it re-runs
  ONLY CHARM** (recon-all is reused via the filled-in folder — no second multi-hour recon). If the
  failure was CHARM's segmentation/registration, tick a **CHARM affine fix** (`--forceqform` or
  `--forcesform`) and/or **re-run CHARM**, then press ② again. The CHARM error itself is in the log.

## 3. Run the dose pipeline (short process — existing m2m)

**GUI:** double-click **`run_dose_tool.command`** — it auto-finds a Python with a working Tk and
`cd`s into the folder for you (first time: right-click → Open → Open past Gatekeeper). Or from a
terminal — you must be **inside the v3_python folder** and use **Homebrew's python** (requires
`brew install python-tk`; the macOS *system* python and the *SimNIBS* python both fail on Tk here —
see Troubleshooting):
```bash
cd /path/to/v3_python
"$(brew --prefix)/bin/python3" dose_tool_gui.py
```
Two interpreters are in play by design: **brew's python** runs the GUI *window*, and **SimNIBS's
python** (the `simnibs_python` field in the window) runs the actual *simulation*.
Browse the Brainsight `.txt` (dropdowns populate), pick the m2m folder, coil, and a **new/empty**
output folder, choose M1 + stim targets, enter rMT and dI/dt, then Dry-run → Run. Results open in
Gmsh (fields), FreeView (sphere on brain per target), and the CSV opens automatically.

**Command line (same engine):**
```bash
# edit example_config.json with this subject's paths + target names, then:
/Applications/SimNIBS-4.1/bin/simnibs_python apex_pipeline.py example_config.json --dry-run  # validate
/Applications/SimNIBS-4.1/bin/simnibs_python apex_pipeline.py example_config.json            # full run
```
The full run needs the subject's `m2m_<id>` folder (from CHARM) to already exist. Use a **matched**
subject (the m2m and the Brainsight targets from the same person) or the E-fields are meaningless.

**rMT and dI/dt.** Enter the resting motor threshold as **% MSO** and the dI/dt as read off the
MagVenture screen in **A/µs**. (SimNIBS expects A/s; the tool converts by ×10⁶ internally — you always
type the A/µs value the machine shows.) As you type the rMT, a small green "≈ N @ M%" appears under the
dI/dt box: the dI/dt *expected* at that %MSO from the MagVenture MagPro X100 + Cool-B65 curve, as a
sanity check while you read the real value off the machine. If the dI/dt you enter is grossly
inconsistent with the rMT (the entered value would imply a %MSO more than 8 points away), a warning
banner is printed to the log (and flashed at the top for ~5 s) asking you to double-check — it does
**not** stop the run. Because APEX uses the M1↔target ratio, the exact dI/dt does not change the dose
much; this check is only to catch a misread or typo.

**ROI mode** (how the |E| value fed to APEX is picked — all take a volume-weighted percentile of |E|
over gray matter, default 99.9th):

- **whole_gm** (default) — over the *entire* gray matter. This equals the 99.9th-percentile |E| that
  SimNIBS reports in its own `fields_summary.txt`. Most reproducible.
- **coil_nearest** — over a sphere (radius, default 10 mm) centered on the GM element nearest the
  *coil centre*. A local peak under the coil.
- **coil_axis** — same, but centred on the GM element nearest the *coil axis* line. Needs the coil-axis
  sign to be correct.
- **coord** — sphere centred on an explicit **x;y;z** coordinate you type in the box next to the
  dropdown (it's greyed out unless you pick `coord`). Coordinates are in **subject/scanner space**
  (same frame as the m2m and your Brainsight targets), e.g. `-42;-56;38`. Note: this one coordinate is
  used for **every** site (M1 and each target), so it's meant for probing |E| at a specific location.
  (Config-file equivalent: `"roi_mode": "coord", "coord": [-42, -56, 38]`.)

For `whole_gm` there is no sphere, so the **radius** field is greyed out and the **FreeView spheres**
QC is turned off automatically (they only make sense for the sphere-based modes).

**When to prefer `coil_nearest` over `whole_gm`.** `whole_gm` takes the peak over the *entire* cortex,
so if the coil is **tilted** (e.g. one wing sits closer to the scalp because of the EEG cap) the peak
|E| can land under that wing, **away from the intended target**. That inflates `E_target`, which
*deflates* the computed dose. `coil_nearest` restricts the measurement to a sphere under the coil
centre — the field at the site you actually aimed at — so for tilted placements (common with a cap +
active electrodes) it is the more physically appropriate choice. Use the **same** mode for M1 and the
targets so the ratio is consistent. (The default is still `whole_gm`; switch the dropdown to
`coil_nearest` when the QC shows an off-target hotspot.)

**Automatic tilt flag.** After each run the tool prints a "coil-tilt QC" line per site — the distance
from the peak |E| element to the cortex point directly under the coil centre — and, if that distance
exceeds 20 mm (`TILT_WARN_MM` in `apex_pipeline.py`), adds a note to that site's `warnings` column
("peak |E| is N mm from the coil-centre cortex point — possible coil tilt / off-target hotspot;
consider ROI mode 'coil_nearest'"). A large value means `whole_gm` is reading a hotspot away from your
target, so you should re-run that site with `coil_nearest`.

**Results CSV columns.** Per target: `E_M1_<pct>pct_Vm` and `E_target_<pct>pct_Vm` (the |E| values,
with the percentile in the name, e.g. `E_M1_99.9pct_Vm`); `dose_100%_%MSO` (the base — the machine
output that reproduces the M1 motor-threshold field at the target); `dose_120%_%MSO`, `dose_135%_%MSO`
(and any custom, e.g. `dose_105%_%MSO`); `whole_gm_<pct>pct_Vm` (the whole-GM percentile as a
cross-check regardless of ROI mode); `roi_center` / `coil_cortex_xyz` / `peak_xyz` (QC locations); and
`warnings` (e.g. a dose over the machine's 100% ceiling). Default reported intensities are **120% and
135%** (the base itself is the 100% dose, so there is no separate `dose_100%` from the percents list).

---

## 4. Troubleshooting (issues we actually hit, with fixes)

- **`brew: command not found`** — Homebrew isn't installed. It is needed to install `dcm2niix`
  (§1.5) and `python-tk` (§1.6). To install brew you need admin rights + Xcode CLT (§1.4).
- **`pip install dicom2nifti` fails building `python-gdcm`** (`CMake Error ... does not contain
  CMakeLists.txt`, "Failed building wheel for python-gdcm") — `python-gdcm` has no prebuilt wheel for
  SimNIBS's Python 3.9 and tries to compile with CMake. **Don't use dicom2nifti.** You don't need it:
  `pydicom` (already installed) reads the DICOM headers for T1/T2 identification, and `dcm2niix`
  does the conversion.
- **No FreeSurfer banner on a new Terminal** — the setup script isn't being sourced. Append the
  FreeSurfer block in §1.1 to `~/.bash_profile` (bash) or `~/.zprofile` (zsh).
- **bash vs zsh** — macOS defaults to zsh now but this machine's Terminal opened `bash`. Put env lines
  in the startup file for the shell you actually use (`~/.bash_profile` for bash, `~/.zprofile`/
  `~/.zshrc` for zsh); add to both if unsure.
- **`.tbz` MRI archive** — extract with `tar -xjf file.tbz -C dest/`, or just select it in the tool
  and let it auto-extract (§2).
- **SimNIBS attribute errors on 4.1** — the code targets 4.1 but is written version-tolerant (GM tag
  `2`, field key probed `magnE`/`E_magn`). Report any attribute error and we'll pin it.
- **GUI aborts with `macOS 11 (1107) or later required … Abort trap: 6`** — this is the macOS
  *system* `python3`, whose bundled Tk framework requires a newer OS build than is installed. Don't
  launch the GUI with system `python3`. Use SimNIBS's python:
  `/Applications/SimNIBS-4.1/bin/simnibs_python dose_tool_gui.py` (or double-click
  `run_dose_tool.command`, which does this). If SimNIBS's python lacks tkinter
  (`simnibs_python -c "import tkinter"` errors), `brew install python-tk` and use brew's `python3`.
- **GUI under simnibs_python fails with `Can't find a usable init.tcl`** — SimNIBS's python has
  `_tkinter` but, launched directly, doesn't set `TCL_LIBRARY`/`TK_LIBRARY`, so Tk can't find its
  library files. `run_dose_tool.command` auto-detects and sets them. Manual equivalent:
  ```bash
  SIMROOT=/Applications/SimNIBS-4.1/simnibs_env
  export TCL_LIBRARY=$(dirname "$(find $SIMROOT/lib -name init.tcl | head -1)")
  export TK_LIBRARY=$(dirname "$(find $SIMROOT/lib -name tk.tcl  | head -1)")
  /Applications/SimNIBS-4.1/bin/simnibs_python dose_tool_gui.py
  ```
  If those `find`s return nothing, SimNIBS shipped no Tcl/Tk data → use `brew install python-tk`.
- **Never point `out_dir` at a folder that holds your data.** The tool now refuses if `out_dir` is
  the m2m folder, a parent of it, or any non-empty folder it didn't create (no `.recare_sim_output`
  marker). Use a fresh dedicated subfolder, e.g. `.../sim_SUBJECT`. (Earlier, a wrong
  `out_dir` started deleting a data folder before erroring — that can't happen anymore.)
- **recon-all dies late with `ln: <file>: Permission denied`** (e.g. `ln -s lh.white.preaparc.H
  lh.white.H`) — the head-model build was running on a **network share** (`/Volumes/Lab Data/...`,
  an SMB/CIFS mount). Shares let you *write files* but *refuse symlink creation*, and
  recon-all makes several symlinks near the end of the surface stage, so it runs for hours and then
  fails. **You don't have to do anything** — the GUI now always builds the head model in a **local
  APEX temp folder** (`~/APEX_local/<subject>/`, which is space-free and symlink-capable) and copies
  the finished `m2m_<subject>` back to your work folder (`<work>/simnibs/`) with `rsync -rL --delete`,
  which writes real files, never symlinks. Notes:
    - **The copy is complete.** `-r` copies every file including dotfiles; `-L` follows symlinks and
      writes the real file at the destination (this is the flag that fixes the classic "some files
      didn't copy" problem when a recon-all folder — full of internal symlinks — is copied to another
      filesystem). We deliberately don't use `rsync -a`: its `-p/-o/-g` (permissions/owner/group)
      can't be honoured on an SMB share and would throw errors even when all the data copied fine.
      Success is judged by the actual output file landing on the share (`<subject>.msh` for the m2m;
      the finished `recon-all.log` for the archive), not by rsync's metadata warnings.
    - **"delete local temp when done"** tickbox (Stage 2, default OFF): when ON, once the m2m is
      safely on the share the recon-all output is *also* archived to `<work>/freesurfer/<subject>` and
      the whole `~/APEX_local/<subject>` folder is deleted (frees ~1 GB). When OFF, the local temp
      folder stays (keeps the FreeSurfer subject for QC / a fast CHARM re-run).
    - **No partial resume.** FreeSurfer 7.2 removed `-make`, so recon-all can't reliably continue a
      half-finished subject. Each run starts **fresh** (a partial local subject is deleted first). If
      a run is interrupted, just launch ② again — it re-runs from the top (~several hours).
    - The FreeSurfer output lives in the local temp folder on the Mac that ran it (unless cleanup
      archived it to the share). The `m2m` — all the dose step needs — is always copied to the share.
    - Cross-machine: the dose step still works from any Mac (m2m is on the share). recon-all itself is
      machine-local — run a subject's head model start-to-finish on one Mac.
    - Left-over `~/.recare_links/` from the earlier scheme is now unused; you can delete it.

---

## 5. Change log
- **2026-07-23** — Initial guide. Confirmed env: SimNIBS 4.1 at `/Applications/SimNIBS-4.1`,
  FreeSurfer 8.2.0. Homebrew + dcm2niix installed. Added: `check_setup.py` automated preflight;
  `.tbz`/archive DICOM support; the `python-gdcm`/dicom2nifti pitfall and the FreeSurfer-profile fix.
- **2026-07-23 (after first Mac run)** — First full end-to-end run succeeded (whole-GM |E| matched
  SimNIBS's own summary). Fixes from that run: `out_dir` safety guards (a wrong out_dir had begun
  deleting a data folder); non-blocking Gmsh QC that opens the exact `*_scalar.msh` results; default
  extraction switched to **whole-GM 99.9th pct** (matches `simNIBS_BF_final.m`); `targets` documented
  as an open list (add right-DLPFC, occipital, etc.).
- **2026-07-24 (GUI Tk saga — RESOLVED)** — Getting a working tkinter for the GUI took three tries:
  (1) system `python3` → `Abort trap: 6` (Tk framework needs newer macOS build); (2) `simnibs_python`
  → `Can't find a usable init.tcl` (TCL_LIBRARY unset when run outside conda activation);
  (3) **`brew install python-tk` → brew's python3 (Tk 9.0) works.** Launch the GUI from *inside*
  `v3_python` with brew's python, or just use `run_dose_tool.command` (auto-picks the working python
  and cd's for you). The engine still runs under `simnibs_python`. See §1.6 + Troubleshooting.
- **2026-07-24 (recon-all on a network share — RESOLVED)** — First real head-model build (subject
  `115_TR`) ran for hours then died at the curvature step with `ln: lh.white.H: Permission denied`.
  Cause: the data lives on an SMB share (`/Volumes/Lab Data/...`) that allows file
  writes but **not symlink creation**, and recon-all creates symlinks. The earlier space-free-symlink
  fix removed the *space* problem but the FreeSurfer subject still physically lived on the share.
  Fix: the head model is now built entirely in a **local APEX temp folder** (`~/APEX_local/<subject>/`)
  and the finished `m2m` is `rsync --copy-links`'d back to `<work>/simnibs/`. A default-off "delete
  local temp when done" tickbox archives the recon-all output to the share and removes the local copy.
  See Troubleshooting for the full behavior. (`spacefree.py`, `charm.head_model_shell`,
  `pipeline_gui.py`.)
- **2026-07-24 (recon-all `-make` removed on FS 7.2)** — first retry hit `ERROR: -make is no longer
  supported`: FreeSurfer 7.2 dropped the `-make` dependency-resume mechanism the tool used to continue
  a partial subject. Removed all partial-resume/salvage logic — each run now starts recon-all **fresh**
  (deletes any partial local subject first). If interrupted, just relaunch ② (Giac confirmed a full
  re-run is acceptable).
- **2026-07-24 (head-model modes + hardened copy + bug pass)** —
  (1) The recon-all→share copy uses `rsync -rL --delete` (was `rsync -a`): `-L` dereferences the
  internal symlinks recon-all makes (the classic "some files didn't copy" cause), and dropping `-a`'s
  `-p/-o/-g` avoids spurious chmod/chgrp errors on SMB; success is judged by the actual output file
  (`<id>.msh` / finished `recon-all.log`), and cleanup deletes the local temp only after that proof.
  (2) Added a **"skip recon-all"** mode (CHARM builds from T1/T2 only, no `--fs-dir`); documented
  reusing an existing recon-all folder and loading an existing m2m (§2c); noted a **FLAIR can be used
  as the T2w**. (3) Bug pass: `recon_all_command` now errors if no `-i` input; `charm_command` requires
  a T1 as its first input (a T2/FLAIR can't slide into the T1 slot); the GUI validates recon/CHARM
  inputs, the pasted recon-all folder, CPU-cores, and `charm` presence **before** launching the
  hours-long job; Brainsight targets now load from a **pasted/typed** path, not only via Browse; and
  the SimNIBS python/coil defaults resolve to whatever `/Applications/SimNIBS-*` is installed.
- **2026-07-28 (cross-platform / WSL support)** — added `locate.py`, a single cross-platform finder
  for SimNIBS / FreeSurfer / charm / simnibs_python / coil / gmsh / freeview / dcm2niix (env → PATH →
  common macOS and Linux/WSL install dirs). `recon.py`, `charm.py`, `simulate.py`, `visualize.py`,
  `pipeline_gui.py` defaults and `check_setup.py` now delegate to it, so the same folder auto-detects
  tools on the lab Mac *and* inside Ubuntu-on-WSL2. Added `run_pipeline.sh` (Linux/WSL launcher) and a
  "Running on Windows (WSL2)" section (§1b). No change to macOS behavior.
- **2026-07-28 (dI/dt sanity check + defaults)** — confirmed the dI/dt unit conversion (operators type
  A/µs from the MagVenture screen; SimNIBS gets A/s via ×10⁶, applied in `simulate.py`). Defaults are
  now rMT = 60 %MSO and dI/dt = 87 A/µs. Added `didt_ref.py` — a least-squares fit of the MagVenture
  MagPro X100 + Cool-B65 %MSO↔dI/dt table (`dI/dt ≈ 1.518·%MSO − 2.75`, R² = 0.9998) — driving a live
  "expected dI/dt" hint under the dI/dt box and a non-blocking warning when the entered dI/dt is grossly
  inconsistent with the rMT.
- **2026-07-28 (Stage-2 decision logic + poll fixes)** — reworked what pressing ② does so it reliably
  reuses existing work: it now looks for a complete recon-all / `m2m` in the **work folder, local temp,
  and the pasted field** (previously only the build location, so an existing recon on a network-share
  work folder was missed and re-run), and it **does nothing** when a complete `m2m` already exists and
  no re-run box is ticked (previously it always launched a background job). Full rule table in §2c;
  a found recon-all path is shown in the field for reference. Also fixed the poll so it **no longer
  replays a finished build's log on open** (it looked like a live run while the status read "idle") —
  it now starts from the end of the log and only streams/updates status for a genuinely active run.
- **2026-07-24 (direct build on local folders + status fix)** — the build location is now
  auto-detected: if the work folder is local, symlink-capable and space-free (probed by actually
  creating a symlink), recon-all/CHARM build **directly in it** with no `~/APEX_local` temp folder and
  no copy-back; only network-share / spaced work folders use the temp-folder + copy path. The read-only
  Stage-2 field is renamed **"Build folder"** and shows whichever is used. Also fixed the top activity
  indicator showing **"idle"** during a running head-model build — it now reflects the detached run's
  live status (`● head model running…`) and clears when it finishes.
- **2026-07-24 (consistent validity rules)** — one rule each, applied everywhere: a **recon-all**
  folder is "done" only if `scripts/recon-all.log` contains `"finished without error"` (checked at
  auto-fill, at GUI pre-launch, and re-checked at run time in the background job — never folder
  presence alone); an **m2m** is "done" only if the head mesh `<id>.msh` is present (`charm.is_m2m_valid`,
  used by `is_charm_done`, the copy-back guard, and now the dose engine `simulate.run_simulations` +
  the Stage-3 pre-launch check — so a charm-failed folder-without-mesh is rejected with a clear
  message instead of a cryptic SimNIBS error). CHARM has no reliable log marker, so `.msh` presence is
  the signal (deliberately not a log grep).
- **2026-07-24 (re-run / force flags + temp-folder box)** — a complete recon-all / `m2m` is now
  reused on re-run (CHARM refuses to overwrite an existing `m2m` without `--forcerun`, so it's skipped
  rather than erroring). Added Stage-2 tickboxes: **re-run recon-all**, **re-run CHARM (`--forcerun`)**,
  and the CHARM affine-fix flags **`--forceqform` / `--forcesform`** (verified against SimNIBS' charm
  CLI; the GUI blocks ticking both). A partial leftover `m2m` auto-gets `--forcerun` so an interrupted
  CHARM re-runs cleanly. Added a read-only **"Local temp folder"** field showing `~/APEX_local/<subject>`.
