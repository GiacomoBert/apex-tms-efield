#!/usr/bin/env python
"""
check_setup.py — Preflight environment check for the APEX-MT E-field pipeline.

Run it with the SimNIBS python so it can also verify `import simnibs`:
    /Applications/SimNIBS-4.1/bin/simnibs_python check_setup.py

It prints what's already present and ONLY the steps you still need — so on a
machine that already has Homebrew / FreeSurfer / dcm2niix, you skip those.
Exit code is non-zero if anything REQUIRED is missing.
"""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

OK, MISS, OPT = "OK  ", "MISS", "opt "


def _pyver(mod: str):
    try:
        m = __import__(mod)
        return getattr(m, "__version__", "?")
    except Exception:
        return None


def _which(name: str):
    return shutil.which(name)


def _find_coil():
    """Locate the Cool-B65 .ccd, cross-platform (macOS + Linux/WSL). See locate.py."""
    import locate
    c = locate.coil_file()
    return Path(c) if c else None


def _find_gui_python():
    """Return a python executable with a WORKING tkinter (for the GUI), or None."""
    import subprocess
    cands = []
    brew = shutil.which("brew")
    if brew:
        try:
            prefix = subprocess.check_output([brew, "--prefix"], text=True).strip()
            cands.append(os.path.join(prefix, "bin", "python3"))
        except Exception:
            pass
    cands += [shutil.which("python3"),                       # Linux / WSL (python3-tk)
              "/usr/local/bin/python3", "/opt/homebrew/bin/python3", "/usr/bin/python3"]
    for c in cands:
        if c and os.path.exists(c):
            try:
                subprocess.check_call([c, "-c", "import tkinter"],
                                      stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                return c
            except Exception:
                continue
    return None


def main() -> int:
    print("=" * 68)
    print("APEX-MT E-field pipeline — environment check")
    print("=" * 68)
    print(f"python: {sys.executable}")
    is_simnibs_py = "simnibs" in sys.executable.lower() or _pyver("simnibs")
    if not is_simnibs_py:
        print("  ! Not the SimNIBS python. Re-run with e.g.\n"
              "    /Applications/SimNIBS-4.1/bin/simnibs_python check_setup.py")
    print()

    rows = []  # (status, label, detail, fix)

    # --- Python packages (all ship inside the SimNIBS env) ---
    # If any are missing you're almost certainly on the wrong interpreter.
    py_fix = ("run under simnibs_python (these ship with SimNIBS); if still "
              "missing: <simnibs_python> -m pip install <pkg>")
    for mod in ("numpy", "simnibs", "pydicom", "nibabel"):
        v = _pyver(mod)
        if v:
            rows.append((OK, f"py:{mod}", v, ""))
        else:
            rows.append((MISS, f"py:{mod}", "not importable", py_fix))

    # --- CLI tools ---
    cli = [
        ("dcm2niix", False, "needs Homebrew first (INSTALL.md §1.4), then: "
                            "brew install dcm2niix  — or skip it and use mri_convert"),
        ("mri_convert", True, "install FreeSurfer + source SetUpFreeSurfer.sh (INSTALL.md §1.1)"),
        ("recon-all", True, "install FreeSurfer + source SetUpFreeSurfer.sh (INSTALL.md §1.1)"),
        ("charm", True, "comes with SimNIBS; ensure SimNIBS bin is on PATH (INSTALL.md §1.2)"),
        ("gmsh", False, "bundled with SimNIBS; needed for the E-field QC viewer"),
        ("freeview", False, "FreeSurfer viewer; used to review T1/T2 + QC spheres (INSTALL.md §1.1)"),
        ("brew", False, "install Homebrew — INSTALL.md §1.4 (needs admin + Xcode CLT). "
                        "Required to get dcm2niix and the GUI's python-tk"),
    ]
    for name, required, fix in cli:
        p = _which(name)
        rows.append((OK if p else (MISS if required else OPT),
                     f"cli:{name}", p or "not on PATH", "" if p else fix))

    # --- FreeSurfer env ---
    fsh = os.environ.get("FREESURFER_HOME", "")
    rows.append((OK if fsh else OPT, "env:FREESURFER_HOME", fsh or "unset",
                 "" if fsh else "add FreeSurfer block to ~/.bash_profile (see INSTALL.md)"))

    # --- Coil file ---
    coil = _find_coil()
    rows.append((OK if coil else MISS, "coil:Cool-B65",
                 str(coil) if coil else "not found",
                 "" if coil else "check SimNIBS install / coil_models path"))

    # --- GUI python (needs a WORKING tkinter — the SimNIBS python usually can't
    #     provide one on macOS; Homebrew's python-tk is what works) ---
    gui_py = _find_gui_python()
    rows.append((OK if gui_py else OPT, "gui:python+tkinter",
                 gui_py or "none found",
                 "" if gui_py else "brew install python-tk  (then launch via run_pipeline.command)"))

    # --- print table ---
    print(f"{'status':6} {'component':22} detail")
    print("-" * 68)
    missing_required = 0
    fixes = []
    for status, label, detail, fix in rows:
        print(f"[{status}] {label:22} {detail}")
        if status == MISS:
            missing_required += 1
        if fix and status in (MISS, OPT):
            fixes.append((status, label, fix))

    print("\n" + "-" * 68)
    if fixes:
        print("Suggested actions (only what's missing):")
        for status, label, fix in fixes:
            tag = "REQUIRED" if status == MISS else "optional"
            print(f"  [{tag}] {label}: {fix}")
        print("\n  → Full step-by-step install instructions: see INSTALL.md (§1), "
              "in this folder.")
    else:
        print("All good — nothing to install.")

    print("-" * 68)
    if missing_required:
        print(f"RESULT: {missing_required} required item(s) missing.")
        return 1
    print("RESULT: ready to run the dose pipeline.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
