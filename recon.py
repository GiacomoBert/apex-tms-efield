"""
recon.py — Run FreeSurfer recon-all from code (the first heavy pre-visit stage).

Builds and runs `recon-all -s <id> -i <T1> [-T2 <T2> -T2pial] -all -parallel
-openmp N`, sourcing the FreeSurfer environment first (a subprocess does NOT read
your ~/.bash_profile, so we source SetUpFreeSurfer.sh explicitly). Resumable:
if the subject already finished, it is skipped.

recon-all is Mac/Linux only (on Windows it must run inside WSL).
"""

from __future__ import annotations

from pathlib import Path
from typing import Callable, List, Optional
import os
import subprocess


def find_freesurfer_home() -> Optional[str]:
    """FreeSurfer install dir, cross-platform (macOS + Linux/WSL). See locate.py."""
    import locate
    return locate.freesurfer_home()


def recon_all_command(subject_id: str, t1: str | Path, subjects_dir: str | Path,
                      t2: Optional[str | Path] = None, use_t1: bool = True,
                      use_t2: bool = True, openmp: int = 4) -> List[str]:
    """The recon-all argument list (for display / testing). Normally T1 is the
    primary input (`-i`) with T2 added for the pial refinement (`-T2 -T2pial`).
    If only T2 is selected, it is used as the primary input (unusual)."""
    cmd = ["recon-all", "-s", subject_id]
    if use_t1 and t1:
        cmd += ["-i", str(t1)]
        if use_t2 and t2:
            cmd += ["-T2", str(t2), "-T2pial"]
    elif use_t2 and t2:
        cmd += ["-i", str(t2)]        # T2-only run
    if "-i" not in cmd:
        raise ValueError(
            "recon-all needs a primary input volume: select a T1w (recommended) "
            "or a T2w. With both unchecked (or no file provided) there is no -i input.")
    cmd += ["-all", "-parallel", "-openmp", str(openmp)]
    return cmd


def is_recon_done(subjects_dir: str | Path, subject_id: str) -> bool:
    """True if recon-all finished successfully for this subject."""
    log = Path(subjects_dir) / subject_id / "scripts" / "recon-all.log"
    if not log.is_file():
        return False
    try:
        tail = log.read_text(errors="replace")[-4000:]
    except OSError:
        return False
    return "finished without error" in tail


def run_recon_all(
    subject_id: str,
    t1: str | Path,
    subjects_dir: str | Path,
    t2: Optional[str | Path] = None,
    use_t1: bool = True,
    use_t2: bool = True,
    openmp: int = 4,
    fs_home: Optional[str] = None,
    on_line: Optional[Callable[[str], None]] = None,
    dry_run: bool = False,
) -> Path:
    """Run recon-all (resumable). Returns the subject's recon output folder."""
    subjects_dir = Path(subjects_dir)
    out = subjects_dir / subject_id
    cmd = recon_all_command(subject_id, t1, subjects_dir, t2, use_t1, use_t2, openmp)

    log = on_line or (lambda s: print(s, end=""))
    if is_recon_done(subjects_dir, subject_id):
        log(f"[recon-all] already complete for {subject_id} — skipping.\n")
        return out
    log(f"[recon-all] {' '.join(cmd)}\n[recon-all] SUBJECTS_DIR={subjects_dir}\n")
    if dry_run:
        log("[dry_run] recon-all not launched.\n")
        return out

    fs_home = fs_home or find_freesurfer_home()
    if fs_home is None:
        raise RuntimeError("FreeSurfer not found (set FREESURFER_HOME).")
    subjects_dir.mkdir(parents=True, exist_ok=True)
    shell = (f'source "{fs_home}/SetUpFreeSurfer.sh" >/dev/null 2>&1; '
             f'export SUBJECTS_DIR="{subjects_dir}"; '
             + " ".join(_q(c) for c in cmd))
    _stream(["bash", "-c", shell], log)
    if not is_recon_done(subjects_dir, subject_id):
        raise RuntimeError(
            f"recon-all did not finish for {subject_id}. "
            f"See {out/'scripts'/'recon-all.log'}")
    log(f"[recon-all] finished: {out}\n")
    return out


# --- helpers ---
def _q(s: str) -> str:
    import shlex
    return shlex.quote(str(s))   # correct quoting incl. spaces in paths


def _stream(cmd: List[str], log: Callable[[str], None]) -> None:
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                         text=True, bufsize=1)
    for line in p.stdout:
        log(line)
    p.wait()
    if p.returncode != 0:
        raise RuntimeError(f"command failed (exit {p.returncode}): {' '.join(cmd)}")
