"""
dicom.py — DICOM (folder or .tbz archive) -> T1 / T2 NIfTI, with auto ID.

Pipeline:
  1. resolve the input (folder or archive) -> a folder of DICOMs   [dicom_input.py]
  2. convert every series to NIfTI (dcm2niix preferred; mri_convert fallback)
  3. classify each series as T1 / T2 / other, from name + TE/TR/TI physics
  4. pick the best T1 (required) and T2 (optional); honour name hints/overrides

`classify_series` and the metadata parsers are pure and unit-tested offline; the
conversion functions need dcm2niix or FreeSurfer's mri_convert (tested on the Mac).
"""

from __future__ import annotations

from dataclasses import dataclass, field as dc_field
from pathlib import Path
from typing import Dict, List, Optional, Tuple
import glob
import json
import shutil
import subprocess

from dicom_input import resolve_dicom_input

# series whose description marks them as neither a usable T1 nor T2 for CHARM
_EXCLUDE = ("flair", "stir", "dwi", "dti", "bold", "func", "fmap", "fieldmap",
            "localizer", "scout", "survey", "phoenix", "report", "asl", "swi",
            "perfusion", "angio", "mra")
_T1_KW = ("mprage", "mp-rage", "mp_rage", "bravo", "fspgr", "spgr", "t1w",
          "tfl", "t1_mprage")
_T2_KW = ("space", "cube", "t2w", "t2spc", "spc")


@dataclass
class Series:
    nifti: Optional[Path]     # converted NIfTI (None until converted)
    desc: str
    te_ms: Optional[float]
    tr_ms: Optional[float]
    ti_ms: Optional[float]
    n_files: int = 0
    guess: Optional[str] = None            # "T1" / "T2" / None
    dicom_file: Optional[Path] = None      # a representative DICOM (for mri_convert)
    size: int = 0                          # NIfTI size in bytes (tie-break)


# --------------------------------------------------------------------------
# classification (pure — unit-tested)
# --------------------------------------------------------------------------

def classify_series(desc: str, te_ms: Optional[float], tr_ms: Optional[float],
                    ti_ms: Optional[float]) -> Optional[str]:
    """Return 'T1', 'T2', or None for a structural series."""
    d = (desc or "").lower()
    if any(k in d for k in _EXCLUDE):
        return None
    is_t1 = ("t1" in d) or any(k in d for k in _T1_KW)
    is_t2 = ("t2" in d) or any(k in d for k in _T2_KW)
    if is_t1 and not is_t2:
        return "T1"
    if is_t2 and not is_t1:
        return "T2"
    # physics fallback (all in ms)
    if te_ms is not None and te_ms > 60 and (tr_ms is None or tr_ms > 1500):
        return "T2"
    if ti_ms and ti_ms > 0 and (te_ms is None or te_ms < 10):
        return "T1"
    if te_ms is not None and te_ms < 10 and tr_ms is not None and tr_ms < 3000:
        return "T1"
    return None


def _meta_from_json(j: dict) -> Tuple[str, Optional[float], Optional[float], Optional[float]]:
    """dcm2niix JSON -> (desc, te_ms, tr_ms, ti_ms). BIDS times are in SECONDS."""
    desc = j.get("SeriesDescription") or j.get("ProtocolName") or ""
    def ms(key):
        v = j.get(key)
        return float(v) * 1000.0 if isinstance(v, (int, float)) else None
    return desc, ms("EchoTime"), ms("RepetitionTime"), ms("InversionTime")


def _meta_from_dicom(ds) -> Tuple[str, Optional[float], Optional[float], Optional[float]]:
    """pydicom dataset -> (desc, te_ms, tr_ms, ti_ms). DICOM times are in MS."""
    def g(attr):
        try:
            v = getattr(ds, attr, None)
            return float(v) if v is not None else None
        except (TypeError, ValueError):
            return None
    desc = str(getattr(ds, "SeriesDescription", "") or getattr(ds, "ProtocolName", ""))
    return desc, g("EchoTime"), g("RepetitionTime"), g("InversionTime")


# --------------------------------------------------------------------------
# conversion
# --------------------------------------------------------------------------

def _find(exe: str, *fallbacks: str) -> Optional[str]:
    p = shutil.which(exe)
    if p:
        return p
    for f in fallbacks:
        if Path(f).is_file():
            return f
    return None


def _series_from_nifti_dir(out_dir: Path) -> List[Series]:
    """Build Series from NIfTIs already in out_dir (dcm2niix JSON metadata if
    present, else the filename as the description)."""
    series: List[Series] = []
    niis = sorted(glob.glob(str(out_dir / "*.nii.gz"))) + \
        sorted(glob.glob(str(out_dir / "*.nii")))
    seen = set()
    for nii in niis:
        nii = Path(nii)
        stem = nii.name[:-7] if nii.name.endswith(".nii.gz") else nii.stem
        if stem in seen:
            continue
        seen.add(stem)
        jf = out_dir / (stem + ".json")
        if jf.is_file():
            try:
                desc, te, tr, ti = _meta_from_json(json.loads(jf.read_text()))
            except (OSError, json.JSONDecodeError):
                desc, te, tr, ti = stem, None, None, None
        else:
            desc, te, tr, ti = stem, None, None, None   # classify from filename
        series.append(Series(
            nifti=nii, desc=desc, te_ms=te, tr_ms=tr, ti_ms=ti,
            guess=classify_series(desc, te, tr, ti),
            size=(nii.stat().st_size if nii.is_file() else 0)))
    return series


def convert_with_dcm2niix(dicom_root: Path, out_dir: Path,
                          dcm2niix: Optional[str] = None) -> List[Series]:
    exe = dcm2niix or _find("dcm2niix")
    if exe is None:
        raise RuntimeError("dcm2niix not found")
    out_dir.mkdir(parents=True, exist_ok=True)
    print(f"[dicom] running dcm2niix on {dicom_root} ...", flush=True)
    # inherit stdout/stderr so its progress is visible in the GUI/terminal log
    subprocess.run([exe, "-b", "y", "-z", "y", "-o", str(out_dir),
                    "-f", "%s_%d", str(dicom_root)], check=True)
    print("[dicom] dcm2niix finished.", flush=True)
    return _series_from_nifti_dir(out_dir)


def scan_with_pydicom(dicom_root: Path) -> List[Series]:
    """Group DICOMs by series (one representative file each) for mri_convert."""
    import pydicom
    by_uid: Dict[str, Series] = {}
    for root, _dirs, files in __import__("os").walk(dicom_root):
        for f in files:
            fp = Path(root) / f
            try:
                ds = pydicom.dcmread(str(fp), stop_before_pixels=True, force=True)
                uid = str(getattr(ds, "SeriesInstanceUID", "") or fp.parent)
            except Exception:
                continue
            if uid not in by_uid:
                desc, te, tr, ti = _meta_from_dicom(ds)
                by_uid[uid] = Series(nifti=None, desc=desc, te_ms=te, tr_ms=tr,
                                     ti_ms=ti, dicom_file=fp,
                                     guess=classify_series(desc, te, tr, ti))
            by_uid[uid].n_files += 1
    return list(by_uid.values())


def _mri_convert_series(s: Series, out_path: Path,
                        mri_convert: Optional[str] = None) -> Path:
    exe = mri_convert or _find("mri_convert")
    if exe is None:
        raise RuntimeError("mri_convert not found (FreeSurfer)")
    print(f"[dicom] mri_convert {s.dicom_file} -> {out_path}", flush=True)
    subprocess.run([exe, str(s.dicom_file), str(out_path)], check=True)
    return out_path


# --------------------------------------------------------------------------
# top level
# --------------------------------------------------------------------------

def _pick(cands: List[Series]) -> Optional[Series]:
    if not cands:
        return None
    return max(cands, key=lambda s: (s.size, s.n_files))   # highest-res / most slices


def resolve_t1_t2(
    dicom_input: str | Path,
    work_dir: str | Path,
    dcm2niix: Optional[str] = None,
    mri_convert: Optional[str] = None,
    t1_hint: Optional[str] = None,
    t2_hint: Optional[str] = None,
    on_line=None,
) -> Tuple[Path, Optional[Path], List[Series]]:
    """Return (t1_nifti, t2_nifti_or_None, all_series). Raises if no T1 found or
    if the choice is ambiguous and no hint disambiguates it."""
    log = on_line or (lambda s: print(s, end=""))
    work_dir = Path(work_dir)
    nifti_dir = work_dir / "nifti"

    existing = ((sorted(nifti_dir.glob("*.nii.gz")) + sorted(nifti_dir.glob("*.nii")))
                if nifti_dir.is_dir() else [])
    if existing:
        log(f"[dicom] NIfTI folder detected ({len(existing)} file(s)) — SKIPPING "
            f"conversion.\n[dicom] To re-run the conversion, delete this folder:\n"
            f"        {nifti_dir}\n")
        series = _series_from_nifti_dir(nifti_dir)
    else:
        root = resolve_dicom_input(dicom_input, work_dir)
        log(f"[dicom] DICOM root: {root}\n")
        use_dcm2niix = (dcm2niix or _find("dcm2niix")) is not None
        if use_dcm2niix:
            series = convert_with_dcm2niix(root, nifti_dir, dcm2niix)
        else:
            series = scan_with_pydicom(root)
            for s in series:   # convert only likely T1/T2
                if s.guess in ("T1", "T2"):
                    out = nifti_dir / f"{s.guess}_{abs(hash(s.desc)) % 10000}.nii.gz"
                    out.parent.mkdir(parents=True, exist_ok=True)
                    s.nifti = _mri_convert_series(s, out, mri_convert)
                    s.size = s.nifti.stat().st_size

    for s in series:
        log(f"[dicom]  {s.guess or '  ?'}  desc={s.desc!r}  "
            f"TE={s.te_ms} TR={s.tr_ms} TI={s.ti_ms}\n")

    def hinted(cands, hint):
        if not hint:
            return None
        h = hint.lower()
        m = [s for s in cands if h in (s.desc or "").lower()]
        return _pick(m) if m else None

    t1c = [s for s in series if s.guess == "T1" and s.nifti]
    t2c = [s for s in series if s.guess == "T2" and s.nifti]
    t1 = hinted(t1c, t1_hint) or _pick(t1c)
    t2 = hinted(t2c, t2_hint) or _pick(t2c)

    if t1 is None:
        raise RuntimeError(
            "Could not identify a T1 series. Series seen:\n" +
            "\n".join(f"  guess={s.guess} desc={s.desc!r} TE={s.te_ms}" for s in series) +
            "\nPass a t1_hint (a substring of the correct series description).")
    log(f"[dicom] T1 -> {t1.nifti}  (desc={t1.desc!r})\n")
    log(f"[dicom] T2 -> {t2.nifti if t2 else None}"
        f"{'' if t2 else '  (none; CHARM will run T1-only)'}\n")
    return Path(t1.nifti), (Path(t2.nifti) if t2 else None), series


def _open_for_review(t1: Path, t2: Optional[Path]) -> None:
    """Open T1 (and T2) in FreeView so the operator can confirm the auto-pick."""
    try:
        import visualize
        fv = visualize.find_freeview()
        if not fv:
            print("(freeview not found — open the T1/T2 manually to review)")
            return
        args = [fv, "-v", str(t1)] + ([str(t2)] if t2 else [])
        subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception as e:  # pragma: no cover
        print(f"(could not open freeview: {e})")


if __name__ == "__main__":
    # CLI used by the GUI's Stage-1: convert + identify, print RESULT_T1/RESULT_T2,
    # and open both in FreeView for review.
    #   dicom.py <dicom_input> <work_dir> [t1_hint] [t2_hint]
    import sys
    _inp, _work = sys.argv[1], sys.argv[2]
    _t1h = sys.argv[3] if len(sys.argv) > 3 and sys.argv[3] else None
    _t2h = sys.argv[4] if len(sys.argv) > 4 and sys.argv[4] else None
    _t1, _t2, _series = resolve_t1_t2(_inp, _work, t1_hint=_t1h, t2_hint=_t2h)
    print(f"RESULT_T1={_t1}")
    print(f"RESULT_T2={_t2 if _t2 else ''}")
    _open_for_review(_t1, _t2)
