"""
didt_ref.py — MagVenture MagPro X100 (MagOption) + MagVenture Cool-B65 coil.

Manufacturer %MSO <-> dI/dt reference (dI/dt in A/µs, as shown on the machine)
plus a least-squares linear fit. Used ONLY as an operator sanity check: given the
rMT (%MSO) we can show the expected dI/dt, and flag a grossly inconsistent entry.
The dose itself always uses the dI/dt the operator actually types (the machine's
reading, which varies slightly pulse-to-pulse).

Fit on the table below: dI/dt ≈ 1.518·%MSO − 2.75, R² = 0.9998, max residual < 1 A/µs.
Pure standard library (no numpy) so the GUI window can import it.
"""

from __future__ import annotations

# %MSO : dI/dt (A/µs)  — MagPro X100 + MagOption, Cool-B65
TABLE = {10: 13, 15: 21, 20: 27, 25: 35, 30: 42, 35: 51, 40: 58, 45: 65, 50: 73,
         55: 80, 60: 89, 65: 96, 70: 103, 75: 111, 80: 118, 85: 127, 90: 134,
         95: 142, 100: 149}


def _fit(points):
    n = len(points)
    sx = sum(x for x, _ in points)
    sy = sum(y for _, y in points)
    sxx = sum(x * x for x, _ in points)
    sxy = sum(x * y for x, y in points)
    slope = (n * sxy - sx * sy) / (n * sxx - sx * sx)
    intercept = (sy - slope * sx) / n
    return slope, intercept


SLOPE, INTERCEPT = _fit(sorted(TABLE.items()))

# How far the entered dI/dt may imply a different %MSO before we call it "very off".
WARN_MSO_DELTA = 8.0


def est_didt(mso: float) -> float:
    """Expected dI/dt (A/µs) for a given %MSO, from the linear fit."""
    return SLOPE * float(mso) + INTERCEPT


def est_mso(didt: float) -> float:
    """%MSO implied by a given dI/dt (A/µs), from the inverse fit."""
    return (float(didt) - INTERCEPT) / SLOPE


def mismatch(mso: float, didt: float):
    """Return (is_very_off, implied_mso, expected_didt) for the entered pair."""
    implied = est_mso(didt)
    expected = est_didt(mso)
    return abs(implied - float(mso)) > WARN_MSO_DELTA, implied, expected
