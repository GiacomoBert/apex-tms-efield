"""
stage_runner.py — launch a long stage (recon-all / CHARM) fully DETACHED.

The multi-hour recon-all must survive the GUI being closed or the Mac sleeping.
We start it in its own session (setsid, via start_new_session=True) with stdin
from /dev/null and stdout/stderr redirected to a log file — the nohup pattern —
so it is not killed when the launching python exits. A `<log>.pid` file lets a
re-opened GUI find the running process again.

The GUI then just POLLS: read the log tail for display, `is_running(pid)` +
a done-check to know when a stage finished.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional
import os
import subprocess


def launch_detached(shell_cmd: str, log_path: str | Path,
                    cwd: Optional[str | Path] = None) -> int:
    """Start `shell_cmd` (a bash command string) detached; combined output goes
    to log_path. Returns the PID and writes <log_path>.pid."""
    log_path = Path(log_path)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    if cwd:                       # Popen(cwd=...) requires the dir to already exist
        Path(cwd).mkdir(parents=True, exist_ok=True)
    logf = open(log_path, "ab", buffering=0)
    try:
        p = subprocess.Popen(
            ["bash", "-lc", shell_cmd],
            cwd=str(cwd) if cwd else None,
            stdin=subprocess.DEVNULL, stdout=logf, stderr=subprocess.STDOUT,
            start_new_session=True,          # setsid -> survives parent exit
        )
    finally:
        logf.close()   # child keeps its own dup'd fd
    import socket
    pid_file(log_path).write_text(f"{p.pid}\n{socket.gethostname()}\n")
    return p.pid


def pid_file(log_path: str | Path) -> Path:
    return Path(str(log_path) + ".pid")


def read_pid(log_path: str | Path) -> Optional[int]:
    pf = pid_file(log_path)
    if pf.is_file():
        try:
            return int(pf.read_text().splitlines()[0].strip())
        except (ValueError, OSError, IndexError):
            return None
    return None


def read_host(log_path: str | Path) -> Optional[str]:
    pf = pid_file(log_path)
    if pf.is_file():
        try:
            lines = pf.read_text().splitlines()
            return lines[1].strip() if len(lines) >= 2 else None
        except OSError:
            return None
    return None


def _same_host(log_path: Optional[str | Path]) -> bool:
    if log_path is None:
        return True
    import socket
    h = read_host(log_path)
    return (h is None) or (h == socket.gethostname())


def _log_fresh(log_path: str | Path, within_s: float) -> bool:
    try:
        return (__import__("time").time() - Path(log_path).stat().st_mtime) < within_s
    except OSError:
        return False


def is_running(pid: Optional[int]) -> bool:
    """Best-effort 'is this pid alive'. NOTE: within the process that launched
    it, an exited-but-unreaped child shows as a zombie and still passes this
    check — use `has_exited()` for a reliable finished/running decision."""
    if not pid:
        return False
    try:
        os.kill(pid, 0)          # signal 0 = existence check
        return True
    except (OSError, ProcessLookupError):
        return False


def has_exited(pid: Optional[int]) -> bool:
    """True once the stage process has actually finished. Reaps the child if we
    are its parent (avoids the zombie-shows-as-running trap); falls back to a
    liveness check when the GUI was reopened and is no longer the parent."""
    if not pid:
        return True
    try:
        wpid, _status = os.waitpid(pid, os.WNOHANG)
        if wpid == pid:
            return True          # reaped -> exited
        if wpid == 0:
            return False         # still running
    except ChildProcessError:
        pass                     # not our child -> use liveness
    except OSError:
        pass
    return not is_running(pid)


def stop(pid: Optional[int]) -> None:
    """Terminate a detached stage (and its process group)."""
    if not pid:
        return
    try:
        os.killpg(os.getpgid(pid), 15)   # SIGTERM to the whole session
    except (OSError, ProcessLookupError):
        try:
            os.kill(pid, 15)
        except (OSError, ProcessLookupError):
            pass


def status(done: bool, pid: Optional[int],
           log_path: Optional[str | Path] = None) -> str:
    """Stage status for the GUI: 'done' / 'running' / 'failed' / 'not_started'.

    `done` (recon-all log / m2m mesh) is authoritative. Cross-machine aware: a run
    launched on ANOTHER Mac (shared work folder) can't be pid-checked here, so we
    treat a log that's still being written to (recent mtime) as 'running' rather
    than 'failed'."""
    if done:
        return "done"
    if pid is None:
        return "not_started"
    if _same_host(log_path) and not has_exited(pid):
        return "running"                       # local & alive
    if log_path is not None and _log_fresh(log_path, 1200):
        return "running"                       # likely running on another Mac
    return "failed"


def sh_quote(s) -> str:
    """Shell-quote a path/arg (handles the spaces in paths like
    '/Volumes/Lab Data/...')."""
    import shlex
    return shlex.quote(str(s))


def tail(log_path: str | Path, max_bytes: int = 12000) -> str:
    p = Path(log_path)
    if not p.is_file():
        return ""
    try:
        with open(p, "rb") as fh:
            fh.seek(0, os.SEEK_END)
            size = fh.tell()
            fh.seek(max(0, size - max_bytes))
            return fh.read().decode(errors="replace")
    except OSError:
        return ""
