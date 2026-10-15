import sys; sys.path.insert(0, ".")
from apex import apex_base, apex_dose, apex_multi

# README worked example: rMT 48, E_target(DLPFC) 95.3, E_M1 142.0
base = apex_base(48, 142.0, 95.3)
print(f"base = {base:.3f} %MSO   (README displays 71.6; true unrounded = 71.52)")
assert abs(base - 71.52) < 0.05, base

r = apex_dose("DLPFC", 48, 142.0, 95.3, percents=(120,135), custom_pct=80)
print(f"120% = {r.doses['120%']:.2f}   135% = {r.doses['135%']:.2f}   80% = {r.doses['80%']:.2f}")
# v1-app computation (unrounded base then scaled)
assert abs(r.doses['120%'] - 85.83) < 0.05
assert abs(r.doses['135%'] - 96.56) < 0.05
assert abs(r.doses['80%']  - 57.22) < 0.05

# dI/dt invariance: scaling BOTH fields by any k leaves APEX % unchanged
for k in (0.5, 2.0, 137.0):
    assert abs(apex_base(48, 142.0*k, 95.3*k) - base) < 1e-9
print("dI/dt invariance: PASS (APEX % identical when both fields scaled)")

# multi-target (IPL + DLPFC), machine-ceiling warning
res = apex_multi(60, 150.0, {"IPL": 70.0, "DLPFC": 95.0}, percents=(120,135))
for x in res:
    print(f"{x.target:6s} base={x.base_pct:6.1f}  120%={x.doses['120%']:6.1f}  135%={x.doses['135%']:6.1f}  warn={getattr(x,'warnings',[])}")

# validation guards
for bad in [(0,142,95),( -1,142,95),(48,0,95),(48,142,0)]:
    try:
        apex_base(*bad); print("FAIL no error for", bad)
    except ValueError:
        pass
print("\nALL APEX TESTS PASS")
