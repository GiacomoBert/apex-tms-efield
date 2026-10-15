"""
apex.py — APEX MT dosing math.

APEX (Caulfield et al. 2021, Brain Stimulation, Eq. 2): the machine output at a
non-motor target that reproduces the individual's motor-threshold E-field.

    base %MSO = (rMT / E_target) * E_M1
    dose(p)   = base * p / 100          # p = 120, 135, custom, ...

E_M1 and E_target are peak cortical |E| (V/m) from SimNIBS with the M1 coil
position and the target coil position simulated AT THE SAME INTENSITY. Because
|E| scales linearly with dI/dt, the ratio E_M1/E_target — and therefore the APEX
% — is invariant to the dI/dt calibration.

Matches the v1 tkinter Tab-6 calculator (base computed unrounded, then scaled).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Tuple


DEFAULT_PERCENTS: Tuple[float, ...] = (120.0, 135.0)


@dataclass
class ApexResult:
    target: str
    rmt_pct: float          # resting motor threshold (%MSO)
    e_m1: float             # M1 peak |E| at rMT intensity (V/m)
    e_target: float         # target peak |E| at same intensity (V/m)
    base_pct: float         # %MSO reproducing E_M1 at the target (= "100% MT-equiv")
    doses: Dict[str, float] = field(default_factory=dict)  # {"120%": .., "135%": .., "custom(80%)": ..}

    def as_row(self) -> Dict[str, float | str]:
        row: Dict[str, float | str] = {
            "target": self.target,
            "rMT_%MSO": round(self.rmt_pct, 2),
            "E_M1_Vm": round(self.e_m1, 3),
            "E_target_Vm": round(self.e_target, 3),
            "base_%MSO": round(self.base_pct, 2),
        }
        for k, v in self.doses.items():
            row[f"dose_{k}_%MSO"] = round(v, 2)
        return row


def _validate(rmt_pct: float, e_m1: float, e_target: float) -> None:
    if not (rmt_pct and rmt_pct > 0):
        raise ValueError(f"rMT must be > 0 %MSO (got {rmt_pct!r})")
    if not (e_m1 and e_m1 > 0):
        raise ValueError(f"E_M1 must be > 0 V/m (got {e_m1!r})")
    if not (e_target and e_target > 0):
        raise ValueError(f"E_target must be > 0 V/m (got {e_target!r})")


def apex_base(rmt_pct: float, e_m1: float, e_target: float) -> float:
    """Base %MSO at the target that reproduces the M1 motor-threshold E-field."""
    _validate(rmt_pct, e_m1, e_target)
    return (rmt_pct / e_target) * e_m1


def apex_dose(
    target: str,
    rmt_pct: float,
    e_m1: float,
    e_target: float,
    percents: Tuple[float, ...] = DEFAULT_PERCENTS,
    custom_pct: float | None = None,
    clip_max: float | None = 100.0,
) -> ApexResult:
    """Full APEX result for one target.

    percents   : intensities to report (default 120, 135), as % of the
                 individualized threshold (base = 100%).
    custom_pct : optional extra intensity (e.g. 80 for an iTBS 80%-of-threshold dose).
    clip_max   : if set, flags doses exceeding this %MSO (machine ceiling); values
                 are still returned, but see `warnings`.
    """
    base = apex_base(rmt_pct, e_m1, e_target)
    doses: Dict[str, float] = {}
    wanted: List[float] = list(percents)
    if custom_pct is not None:
        wanted.append(custom_pct)
    for p in wanted:
        if p <= 0:
            raise ValueError(f"percent must be > 0 (got {p})")
        label = f"{p:g}%"
        doses[label] = base * p / 100.0

    res = ApexResult(target=target, rmt_pct=rmt_pct, e_m1=e_m1,
                     e_target=e_target, base_pct=base, doses=doses)
    # attach machine-ceiling warnings (RA-proofing; not an exception)
    res.warnings = []  # type: ignore[attr-defined]
    if clip_max is not None:
        for label, v in doses.items():
            if v > clip_max:
                res.warnings.append(  # type: ignore[attr-defined]
                    f"{label} dose = {v:.1f} %MSO exceeds machine max {clip_max:.0f}%")
    return res


def apex_multi(
    rmt_pct: float,
    e_m1: float,
    e_targets: Dict[str, float],
    percents: Tuple[float, ...] = DEFAULT_PERCENTS,
    custom_pct: float | None = None,
) -> List[ApexResult]:
    """APEX for several non-motor targets (e.g. {'IPL': .., 'DLPFC': ..})."""
    return [
        apex_dose(name, rmt_pct, e_m1, e_t, percents=percents, custom_pct=custom_pct)
        for name, e_t in e_targets.items()
    ]
