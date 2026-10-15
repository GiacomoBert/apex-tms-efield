"""
charm.py — Run SimNIBS CHARM from code (builds the m2m head model).

Builds and runs `charm <id> <T1> [<T2>] [--fs-dir <recon>] [--forcerun]` in a
chosen simnibs parent directory (charm writes `m2m_<id>` into the current dir).
Resumable: if `m2m_<id>/<id>.msh` already exists, it is skipped.

Note the flag is `--fs-dir` (hyphen), correct for SimNIBS >= 4.1.
"""

from __future__ import annotations

from pathlib import Path
from typing import Callable, List, Optional
import shutil
import subprocess


def find_charm() -> Optional[str]:
    """The SimNIBS `charm` program, cross-platform (macOS + Linux/WSL). See locate.py."""
    import locate
    return locate.charm_bin()


def charm_command(subject_id: str, t1: str | Path, t2: Optional[str | Path] = None,
                  fs_dir: Optional[str | Path] = None, use_t1: bool = True,
                  use_t2: bool = True, use_fs: bool = True,
                  forcerun: bool = False, forceqform: bool = False,
                  forcesform: bool = False) -> List[str]:
    """The charm argument list (for display / testing). Images are positional and
    ORDER MATTERS: charm's first positional is the T1, the second the T2. So a T1
    is required; we never let a T2 slide into the T1 slot. --fs-dir imports the
    FreeSurfer surfaces (optional).

    Flags (all verified against SimNIBS' charm CLI):
      --forcerun    overwrite an existing m2m_<id> (charm refuses otherwise).
      --forceqform  use the NIfTI qform for the MNI init (fixes some bad-header /
                    affine-registration failures).
      --forcesform  same, using the sform. (Don't set both — they conflict.)"""
    if not (use_t1 and t1):
        raise ValueError(
            "charm needs a T1w as its first input. (A T2w/FLAIR can be added as a "
            "second input, but cannot replace the T1w.)")
    imgs = [str(t1)]
    if use_t2 and t2:
        imgs.append(str(t2))
    cmd = ["charm", subject_id] + imgs
    if use_fs and fs_dir:
        cmd += ["--fs-dir", str(fs_dir)]   # hyphen — SimNIBS >= 4.1
    if forcerun:
        cmd.append("--forcerun")
    if forceqform:
        cmd.append("--forceqform")
    if forcesform:
        cmd.append("--forcesform")
    return cmd


def is_charm_done(simnibs_parent: str | Path, subject_id: str) -> bool:
    """True if CHARM finished for this subject: the m2m folder exists and holds the
    final head mesh `<subject>.msh` (charm writes it last)."""
    return is_m2m_valid(Path(simnibs_parent) / f"m2m_{subject_id}")


def is_m2m_valid(m2m_dir: str | Path) -> bool:
    """Canonical 'is this a completed head model' check for a DIRECT path to an
    `m2m_<id>` folder. Validity == the final head mesh `.msh` is present (folder
    presence alone is NOT enough — charm creates the folder early but writes the
    mesh only on success). Derives the expected `<id>.msh` from the folder name,
    with a glob fallback for any `.msh`."""
    p = Path(m2m_dir)
    if not p.is_dir():
        return False
    name = p.name[len("m2m_"):] if p.name.startswith("m2m_") else p.name
    if (p / f"{name}.msh").is_file():
        return True
    return any(p.glob("*.msh"))


def m2m_path(simnibs_parent: str | Path, subject_id: str) -> Path:
    return Path(simnibs_parent) / f"m2m_{subject_id}"


def head_model_shell(
    subject_id: str,
    t1: str | Path,
    subjects_dir: str | Path,
    simnibs_parent: str | Path,
    t2: Optional[str | Path] = None,
    openmp: int = 4,
    fs_home: Optional[str] = None,
    charm_bin: Optional[str] = None,
    stop_after_recon: bool = False,
    skip_recon: bool = False,
    recon_overwrite: bool = False,
    charm_forcerun: bool = False,
    charm_forceqform: bool = False,
    charm_forcesform: bool = False,
    recon_dir: Optional[str | Path] = None,
    recon_use_t1: bool = True,
    recon_use_t2: bool = True,
    charm_use_t1: bool = True,
    charm_use_t2: bool = True,
    charm_use_fs: bool = True,
    share_simnibs_parent: Optional[str | Path] = None,
    share_subjects_dir: Optional[str | Path] = None,
    cleanup_local: bool = False,
    local_root: Optional[str | Path] = None,
) -> str:
    """Build ONE bash command that runs recon-all and (unless stop_after_recon)
    then CHARM, back-to-back — for the detached head-model stage.

    `subjects_dir` / `simnibs_parent` should be LOCAL, symlink-capable paths
    (recon-all creates internal symlinks that SMB/CIFS network shares reject).
    recon-all handling (in priority order):
      * `recon_dir` given          -> skip recon-all, use it as CHARM --fs-dir
      * `skip_recon`               -> skip recon-all entirely; CHARM segments from
                                      T1(/T2) alone (no --fs-dir). Fast, but the
                                      cortical surface is less accurate than with
                                      FreeSurfer.
      * local subject already done AND not `recon_overwrite` -> skip, use as --fs-dir
      * otherwise                  -> FRESH run (any partial local subject is
                                      deleted first). FreeSurfer 7.x removed
                                      `-make`, so partial resume isn't reliable;
                                      re-running clean is the robust choice.

    CHARM always takes a T1 as its first input; `--fs-dir` is passed only when a
    real recon folder exists AND `charm_use_fs` is set. CHARM is skipped when a
    complete local m2m already exists unless `charm_forcerun` is set. charm refuses
    to overwrite an existing m2m without `--forcerun`, so we pass it automatically
    whenever an m2m folder (complete or partial) is already present and we are
    (re)building. `charm_forceqform`/`charm_forcesform` add the matching flags.

    Copy-back / cleanup (only after CHARM):
      * `share_simnibs_parent`  -> copy finished `m2m_<id>` there (rsync -rL: real
                                   files, never a symlink on the share) for the
                                   dose step / other machines.
      * `cleanup_local` + `local_root` -> once the m2m is safely on the share,
                                   archive the recon-all output to
                                   `share_subjects_dir` and delete `local_root`."""
    import recon
    from stage_runner import sh_quote as q
    fs_home = fs_home or recon.find_freesurfer_home()
    exe = charm_bin or find_charm()
    subjects_dir = Path(subjects_dir)
    simnibs_parent = Path(simnibs_parent)

    # Decide recon-all behavior and whether a FreeSurfer folder will exist for CHARM.
    if recon_dir:
        fs_dir_for_charm = Path(recon_dir)   # external recon -> CHARM --fs-dir
        fs_available = True
        # Verify at RUN TIME that this really is a finished recon-all (log marker),
        # not just a folder that exists — so CHARM never runs against a partial recon
        # even if the engine is called outside the GUI. Aborts before CHARM if not.
        rlog = Path(recon_dir) / "scripts" / "recon-all.log"
        recon_core = (
            f'if grep -q "finished without error" {q(rlog)} 2>/dev/null; '
            f'then echo "using the provided recon-all folder (verified complete) — skipping recon-all"; '
            f'else echo "ERROR: the recon-all folder is not a completed FreeSurfer subject '
            f'(scripts/recon-all.log has no finished marker): {recon_dir}"; exit 1; fi')
    elif skip_recon:
        fs_dir_for_charm = None              # CHARM-only (T1/T2), no FreeSurfer surfaces
        fs_available = False
        recon_core = 'echo "skip recon-all requested — CHARM will segment from T1/T2 only (no --fs-dir)"'
    elif recon.is_recon_done(subjects_dir, subject_id) and not recon_overwrite:
        fs_dir_for_charm = subjects_dir / subject_id
        fs_available = True
        recon_core = 'echo "recon-all already complete (local) — skipping (tick re-run recon-all to rebuild)"'
    else:
        fs_dir_for_charm = subjects_dir / subject_id
        fs_available = True
        # Fresh run. Delete any partial local subject first (FS 7.x has no
        # reliable `-make` resume, so we start clean).
        fresh = " ".join(q(c) for c in recon.recon_all_command(
            subject_id, t1, subjects_dir, t2,
            use_t1=recon_use_t1, use_t2=recon_use_t2, openmp=openmp))
        recon_core = (f'rm -rf {q(subjects_dir / subject_id)}; '
                      f'echo "starting recon-all fresh (no reliable partial resume on FreeSurfer 7.x)"; '
                      f'{fresh}')

    parts = [
        f'source {q(Path(fs_home) / "SetUpFreeSurfer.sh")} >/dev/null 2>&1' if fs_home else 'true',
        f'export SUBJECTS_DIR={q(subjects_dir)}',
        f'mkdir -p {q(subjects_dir)}',
        'echo "===== recon-all: the exact command below runs in 3 s ====="',
        f'echo {q(recon_core)}',
        'sleep 3',
        recon_core,
        'rc=$?; echo "===== recon-all exit $rc ====="',
        'if [ $rc -ne 0 ]; then exit $rc; fi',
    ]
    if stop_after_recon:
        parts.append('echo "===== stop_after_recon set — CHARM skipped ====="')
    else:
        local_m2m_exists = (simnibs_parent / f"m2m_{subject_id}").exists()
        if is_charm_done(simnibs_parent, subject_id) and not charm_forcerun:
            # complete m2m already here -> reuse it (still copied to the share below)
            parts += [
                f'mkdir -p {q(simnibs_parent)}',
                'echo "===== m2m already complete locally — skipping CHARM '
                '(tick \'re-run CHARM\' to rebuild) ====="',
            ]
        else:
            # charm refuses to overwrite an existing m2m without --forcerun, so pass
            # it whenever an m2m folder (complete or partial) is already present.
            eff_forcerun = charm_forcerun or local_m2m_exists
            # --fs-dir only when a real recon folder exists AND the user wants it.
            cc = charm_command(subject_id, t1, t2, fs_dir_for_charm, use_t1=charm_use_t1,
                               use_t2=charm_use_t2, use_fs=(charm_use_fs and fs_available),
                               forcerun=eff_forcerun, forceqform=charm_forceqform,
                               forcesform=charm_forcesform)
            charm_run = " ".join([q(exe or "charm")] + [q(c) for c in cc[1:]])
            parts += [
                f'mkdir -p {q(simnibs_parent)}',
                f'cd {q(simnibs_parent)}',
                'echo "===== CHARM: the exact command below runs in 3 s ====="',
                f'echo {q(charm_run)}',
                'sleep 3',
                charm_run,
                'crc=$?; echo "===== CHARM exit $crc ====="',
            ]
        if share_simnibs_parent:
            local_m2m = simnibs_parent / f"m2m_{subject_id}"
            local_msh = local_m2m / f"{subject_id}.msh"  # only copy a COMPLETE m2m (charm writes
            #                                              the mesh last; absent if CHARM failed)
            dest_m2m = Path(share_simnibs_parent) / f"m2m_{subject_id}"
            msh_dst = dest_m2m / f"{subject_id}.msh"     # CHARM's key output = proof of a good copy
            m_src = str(local_m2m).rstrip("/") + "/"
            m_dst = str(dest_m2m).rstrip("/") + "/"
            # Copy flags — deliberately NOT `rsync -a`:
            #   -r  recurse (copies every file, including dotfiles)
            #   -L  dereference symlinks -> write REAL files (recon-all makes internal
            #       symlinks the share can't store; -L is the flag that fixes the classic
            #       "some files weren't copied" problem)
            #   --delete  mirror exactly (drop any stale files at the destination)
            # We drop -p/-o/-g/-t: an SMB/CIFS share can't honour POSIX perms/owner/times
            # and `-a` would spew chmod/chgrp errors (non-zero exit) even when all the
            # DATA copied fine. Success is judged by the actual output file, not by rsync's
            # metadata warnings.
            RS = "rsync -rL --delete"
            parts.append(
                f'if [ -f {q(local_msh)} ]; then '
                f'mkdir -p {q(Path(share_simnibs_parent))}; '
                f'echo "===== copying m2m to your work folder ({RS}, dereferencing symlinks) ====="; '
                f'{RS} {q(m_src)} {q(m_dst)}; mrc=$?; '
                f'if [ -f {q(msh_dst)} ]; then echo "===== m2m copied to {dest_m2m} (rsync exit $mrc) ====="; '
                f'else echo "===== WARNING: m2m copy incomplete (rsync exit $mrc) — it stays local at {local_m2m} ====="; fi; '
                f'else echo "===== no complete m2m to copy — CHARM did not finish; nothing sent to the share (recon-all output kept locally) ====="; fi')
            # optional: archive the full recon-all to the share, then delete the local temp
            if cleanup_local and local_root and share_subjects_dir:
                f_src = str(subjects_dir / subject_id).rstrip("/") + "/"
                f_dst = str(Path(share_subjects_dir) / subject_id).rstrip("/") + "/"
                log_dst = Path(share_subjects_dir) / subject_id / "scripts" / "recon-all.log"
                parts.append(
                    f'if [ -f {q(msh_dst)} ]; then '     # only clean up once m2m is proven on the share
                    f'echo "===== cleanup: archiving recon-all to your work folder ({RS}) ====="; '
                    f'mkdir -p {q(Path(share_subjects_dir))}; '
                    f'{RS} {q(f_src)} {q(f_dst)}; frc=$?; '
                    f'if [ $frc -eq 0 ] && [ -f {q(log_dst)} ]; then '   # rsync clean AND log present
                    f'echo "===== recon-all archived (rsync exit 0); deleting local temp {Path(local_root)} ====="; '
                    f'rm -rf {q(Path(local_root))}; '
                    f'else echo "===== WARNING: recon archive incomplete (rsync exit $frc) — keeping local temp {Path(local_root)} ====="; fi; '
                    f'else echo "===== m2m not confirmed on the share — keeping local temp for safety ====="; fi')
    return "; ".join(parts)


def run_charm(
    subject_id: str,
    t1: str | Path,
    simnibs_parent: str | Path,
    t2: Optional[str | Path] = None,
    fs_dir: Optional[str | Path] = None,
    use_t2: bool = True,
    forcerun: bool = False,
    charm_bin: Optional[str] = None,
    on_line: Optional[Callable[[str], None]] = None,
    dry_run: bool = False,
) -> Path:
    """Run charm in `simnibs_parent` (resumable). Returns the m2m folder path."""
    simnibs_parent = Path(simnibs_parent)
    m2m = m2m_path(simnibs_parent, subject_id)
    cmd = charm_command(subject_id, t1, t2, fs_dir, use_t2=use_t2, forcerun=forcerun)
    log = on_line or (lambda s: print(s, end=""))

    if is_charm_done(simnibs_parent, subject_id) and not forcerun:
        log(f"[charm] m2m already exists for {subject_id} — skipping.\n")
        return m2m
    log(f"[charm] (cwd={simnibs_parent})\n[charm] {' '.join(cmd)}\n")
    if dry_run:
        log("[dry_run] charm not launched.\n")
        return m2m

    exe = charm_bin or find_charm()
    if exe is None:
        raise RuntimeError("charm not found (is SimNIBS installed / on PATH?).")
    simnibs_parent.mkdir(parents=True, exist_ok=True)
    run_cmd = [exe] + cmd[1:]   # replace 'charm' with its full path

    p = subprocess.Popen(run_cmd, cwd=str(simnibs_parent),
                         stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                         text=True, bufsize=1)
    for line in p.stdout:
        log(line)
    p.wait()
    if p.returncode != 0 or not is_charm_done(simnibs_parent, subject_id):
        raise RuntimeError(
            f"charm failed for {subject_id} (exit {p.returncode}). "
            f"Check {m2m/'charm_report.html'}")
    log(f"[charm] finished: {m2m}\n")
    return m2m
