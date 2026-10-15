import sys, glob, os
sys.path.insert(0, ".")
import numpy as np
from brainsight import parse_brainsight_txt, check_matsimnibs

bs_dir = "/sessions/fervent-youthful-franklin/mnt/brice_efield_pipeline/my_matlab_script/brainsight_txt"
files = sorted(glob.glob(os.path.join(bs_dir, "*.txt")))
print(f"Found {len(files)} Brainsight files\n")

total_targets = 0
bad = 0
name_counter = {}
for f in files:
    tg = parse_brainsight_txt(f)
    total_targets += len(tg)
    for nm, t in tg.items():
        name_counter[nm] = name_counter.get(nm, 0) + 1
        ok, diag = check_matsimnibs(t.matsimnibs)
        if not ok:
            bad += 1
            print(f"  BAD matrix: {os.path.basename(f)} :: {nm} -> {diag}")

print(f"Total targets parsed: {total_targets}")
print(f"Invalid rotation matrices: {bad}")
print()
# Show most common target names (to build the dropdown / target selection)
print("Most common target names across subjects:")
for nm, c in sorted(name_counter.items(), key=lambda x: -x[1])[:15]:
    print(f"  {c:3d}x  {nm!r}")

print("\n--- Detailed check on BF001-0708.txt (M1, IPL, DLPFC) ---")
tg = parse_brainsight_txt(os.path.join(bs_dir, "BF001-0708.txt"))
for nm in ["M1 (anatomical)", "IPL", "DLPFC"]:
    if nm in tg:
        t = tg[nm]
        ok, diag = check_matsimnibs(t.matsimnibs)
        print(f"\n{nm}:  pos = [{t.loc[0]:.2f}, {t.loc[1]:.2f}, {t.loc[2]:.2f}]  valid={ok}")
        print("  matsimnibs =")
        print(np.array2string(t.matsimnibs, precision=4, suppress_small=True, prefix="    "))
        print(f"  det(R)={diag['det']:.4f}  ortho_err={diag['ortho_err']:.2e}  col_norms={[f'{n:.4f}' for n in diag['col_norms']]}")

# Verify against the MATLAB convention explicitly for one target (DLPFC row of BF001)
print("\n--- Verify axis flip against raw file values (BF001 DLPFC) ---")
# raw: DLPFC loc=-46.87,56.59,30.46 m0=0.4237,-0.3099,0.8511 m1=0.4304,0.8957,0.1118 m2=-0.7970,0.3190,0.5129
m0=np.array([0.4237,-0.3099,0.8511]); m1=np.array([0.4304,0.8957,0.1118]); m2=np.array([-0.7970,0.3190,0.5129])
loc=np.array([-46.87,56.59,30.46])
expected = np.eye(4); expected[:3,0]=-m0; expected[:3,1]=m1; expected[:3,2]=-m2; expected[:3,3]=loc
got = tg["DLPFC"].matsimnibs
print("matches expected [-m0, m1, -m2, loc]:", np.allclose(got, expected, atol=1e-4))
