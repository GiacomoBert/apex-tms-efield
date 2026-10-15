import sys, os, tempfile; sys.path.insert(0, ".")
import numpy as np
import extract, simulate   # both must import WITHOUT simnibs (lazy imports)
from extract import weighted_percentile as wp, project_coil_to_cortex as proj, sphere_mask, find_result_mesh

# --- weighted_percentile: hand-computable cases ---
assert abs(wp([10,20,30,40],[1,1,1,1],50) - 25.0) < 1e-9
assert abs(wp([10,20,30,40],[1,1,1,1],99.9) - 40.0) < 1e-9
assert abs(wp([10,20,30,40],[1,1,1,1],12.5) - 10.0) < 1e-9
# volume weighting pulls the "median" toward the heavy element
assert abs(wp([10,100],[1000,1],50) - 10.09) < 0.1
# equal-weight ~ matches numpy percentile on a large uniform sample
x = np.linspace(0,100,10001)
for p in (50, 95, 99.9):
    assert abs(wp(x, np.ones_like(x), p) - np.percentile(x,p)) < 0.2, p
print("weighted_percentile: PASS")

# --- coil->cortex projection ---
# GM elements on the z=0 plane; coil 20mm above origin
xs, ys = np.meshgrid(np.linspace(-30,30,13), np.linspace(-30,30,13))
centers = np.c_[xs.ravel(), ys.ravel(), np.zeros(xs.size)]
def mat(normal_z):
    M = np.eye(4); M[:3,3] = [0,0,20]; M[:3,2] = [0,0,normal_z]; return M
assert np.allclose(proj(mat(-1), centers, mode="coil_nearest"), [0,0,0])
assert np.allclose(proj(mat(-1), centers, mode="coil_axis"),    [0,0,0])
# sign-robust: wrong-signed normal still finds the cortex under the coil
assert np.allclose(proj(mat(+1), centers, mode="coil_axis"),    [0,0,0])
# off-center coil picks nearest cortical element
Moff = np.eye(4); Moff[:3,3] = [10,-5,20]; Moff[:3,2]=[0,0,-1]
got = proj(Moff, centers, mode="coil_nearest")
assert np.allclose(got, [10,-5,0]), got
print("project_coil_to_cortex: PASS")

# --- sphere_mask ---
m = sphere_mask(centers, [0,0,0], 10.0)
assert m.sum() > 0 and np.all(np.linalg.norm(centers[m],axis=1) < 10.0)
print("sphere_mask: PASS")

# --- find_result_mesh: naming + exclusions ---
with tempfile.TemporaryDirectory() as d:
    for fn in ["sub_TMS_1-0001_MagVenture_Cool-B65.msh",
               "sub_TMS_1-0001_MagVenture_Cool-B65_scalar.msh",
               "sub_TMS_1-0001_MagVenture_Cool-B65_MNI.msh",
               "sub_TMS_1-0002_MagVenture_Cool-B65.msh"]:
        open(os.path.join(d,fn),"w").close()
    r1 = find_result_mesh(d, 1)
    assert r1.name == "sub_TMS_1-0001_MagVenture_Cool-B65_scalar.msh", r1.name  # prefer scalar, exclude MNI
    r2 = find_result_mesh(d, 2)
    assert r2.name == "sub_TMS_1-0002_MagVenture_Cool-B65.msh", r2.name
print("find_result_mesh: PASS")

# --- simulate.py sanity: dI/dt unit conversion constant + Position dataclass ---
assert simulate.DIDT_US_TO_S == 1e6
p = simulate.Position(name="M1_rMT", matsimnibs=np.eye(4), didt_A_per_us=90.0)
assert p.name == "M1_rMT"
print("simulate import + Position: PASS")

print("\nALL EXTRACT/SIMULATE OFFLINE TESTS PASS")
