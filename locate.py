"""
locate.py — cross-platform discovery of the external tools the pipeline needs.

Finds SimNIBS (root, simnibs_python, charm, coil, gmsh), FreeSurfer (home,
freeview) and dcm2niix on **macOS** and on **Linux / Windows-WSL**. For each
tool the search order is: an importable `simnibs` package or `FREESURFER_HOME`
env var where relevant, then `PATH`, then the common install directories for the
OS (macOS `/Applications`, Linux `~/`, `/usr/local`, `/opt`). Everything is
best-effort and returns `None` when nothing is found, so callers fall back
cleanly or let the user set the path in the GUI.

This keeps a single place to update when install locations change, and lets the
same code run unchanged on the lab Mac and inside Ubuntu-on-WSL.
"""

from __future__ import annotations

from pathlib import Path
from typing import List, Optional
import glob
import os
import shutil

_COIL_SUBPATH = ("resources", "coil_models", "Drakaki_BrainStim_2022")
DEFAULT_COIL_NAME = "MagVenture_Cool-B65.ccd"


def _uniq_existing_dirs(paths) -> List[Path]:
    out, seen = [], set()
    for p in paths:
        try:
            rp = Path(p).resolve()
        except OSError:
            continue
        key = str(rp)
        if key not in seen and rp.is_dir():
            seen.add(key)
            out.append(rp)
    return out


# --------------------------------------------------------------------------
# SimNIBS
# --------------------------------------------------------------------------

def simnibs_roots() -> List[Path]:
    """Candidate SimNIBS install roots (macOS + Linux/WSL), plus any derived from
    a `simnibs_python`/`charm` already on PATH. Newest-named first; roots that
    actually contain the bin tools are preferred."""
    home = os.path.expanduser("~")
    patterns = [
        "/Applications/SimNIBS-*", "/Applications/SimNIBS_*",               # macOS
        f"{home}/SimNIBS-*", f"{home}/SimNIBS_*", f"{home}/Applications/SimNIBS-*",
        "/usr/local/SimNIBS-*", "/usr/local/SimNIBS_*",                     # Linux / WSL
        "/opt/SimNIBS-*", "/opt/SimNIBS_*",
    ]
    cands: List[str] = []
    for pat in patterns:
        cands += glob.glob(pat)
    # Linux/WSL installers put bin on PATH: <root>/bin/<exe>
    for exe in ("simnibs_python", "charm"):
        w = shutil.which(exe)
        if w:
            cands.append(str(Path(w).resolve().parent.parent))
    roots = _uniq_existing_dirs(cands)
    roots.sort(key=lambda p: p.name, reverse=True)
    good = [r for r in roots
            if (r / "bin" / "simnibs_python").is_file() or (r / "bin" / "charm").is_file()]
    return good or roots


def simnibs_root() -> Optional[Path]:
    rs = simnibs_roots()
    return rs[0] if rs else None


def simnibs_python() -> Optional[str]:
    w = shutil.which("simnibs_python")
    if w:
        return w
    for r in simnibs_roots():
        c = r / "bin" / "simnibs_python"
        if c.is_file():
            return str(c)
    return None


def charm_bin() -> Optional[str]:
    w = shutil.which("charm")
    if w:
        return w
    for r in simnibs_roots():
        c = r / "bin" / "charm"
        if c.is_file():
            return str(c)
    return None


def gmsh_bin(explicit: Optional[str] = None) -> Optional[str]:
    if explicit and Path(explicit).is_file():
        return explicit
    w = shutil.which("gmsh")
    if w:
        return w
    for r in simnibs_roots():
        for c in (r / "bin" / "gmsh", r / "simnibs_env" / "bin" / "gmsh"):
            if c.is_file():
                return str(c)
    try:
        import simnibs  # last resort: relative to an importable simnibs
        c = Path(simnibs.__file__).parents[3] / "bin" / "gmsh"
        if c.is_file():
            return str(c)
    except Exception:
        pass
    return None


def coil_file(name: str = DEFAULT_COIL_NAME) -> Optional[str]:
    """The .ccd coil model — relative to an importable simnibs first (works wherever
    SimNIBS is installed), then under any discovered SimNIBS root."""
    try:
        import simnibs
        c = Path(simnibs.__file__).parent.joinpath(*_COIL_SUBPATH, name)
        if c.is_file():
            return str(c)
    except Exception:
        pass
    for r in simnibs_roots():
        c = r.joinpath("simnibs", *_COIL_SUBPATH, name)
        if c.is_file():
            return str(c)
        hits = glob.glob(str(r / "simnibs" / "resources" / "coil_models" / "**" / name),
                         recursive=True)
        if hits:
            return hits[0]
    return None


# --------------------------------------------------------------------------
# FreeSurfer
# --------------------------------------------------------------------------

def freesurfer_home() -> Optional[str]:
    fsh = os.environ.get("FREESURFER_HOME")
    if fsh and Path(fsh, "SetUpFreeSurfer.sh").is_file():
        return fsh
    home = os.path.expanduser("~")
    bases = ["/Applications/freesurfer", "/usr/local/freesurfer", "/opt/freesurfer",
             f"{home}/freesurfer"]
    cands: List[str] = []
    for b in bases:
        if Path(b, "SetUpFreeSurfer.sh").is_file():            # installed directly in <base>
            cands.append(b)
        for sub in glob.glob(b + "/*"):                        # versioned subdirs (<base>/7.4.1)
            if Path(sub, "SetUpFreeSurfer.sh").is_file():
                cands.append(sub)
    cands = sorted(dict.fromkeys(cands), reverse=True)
    return cands[0] if cands else None


def freeview_bin() -> Optional[str]:
    w = shutil.which("freeview")
    if w:
        return w
    fsh = freesurfer_home()
    if fsh:
        c = Path(fsh) / "bin" / "freeview"
        if c.is_file():
            return str(c)
    return None


# --------------------------------------------------------------------------
# dcm2niix
# --------------------------------------------------------------------------

def dcm2niix_bin() -> Optional[str]:
    return shutil.which("dcm2niix")
