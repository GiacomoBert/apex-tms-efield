"""Self-contained offline tests for the v3 pre-visit pipeline (no FreeSurfer/
SimNIBS/dcm2niix needed). Run with any python3 that has this folder on its path:
    python3 test_full_pipeline.py
"""
import tempfile
from pathlib import Path

import recon
import charm
from dicom import classify_series


def test_classify():
    assert classify_series("T1 MPRAGE", 2.98, 2300, 900) == "T1"
    assert classify_series("t2_space_sag", 400, 3200, None) == "T2"
    assert classify_series("Sag CUBE T2", 250, 2500, None) == "T2"
    assert classify_series("BRAVO", 3.1, 8.5, 450) == "T1"
    assert classify_series("random_struct", 3.0, 2000, 1100) == "T1"   # TI + short TE
    assert classify_series("random_struct", 90, 4000, None) == "T2"    # long TE/TR
    assert classify_series("FLAIR", 90, 9000, 2500) is None            # excluded
    assert classify_series("AAHead_Scout", 2, 5, None) is None         # localizer
    print("classify_series: PASS")


def test_commands():
    c = recon.recon_all_command("TRACE105", "/x/T1.nii.gz", "/sd", "/x/T2.nii.gz",
                                use_t1=True, use_t2=True, openmp=4)
    assert c == ["recon-all", "-s", "TRACE105", "-i", "/x/T1.nii.gz",
                 "-T2", "/x/T2.nii.gz", "-T2pial", "-all", "-parallel",
                 "-openmp", "4"], c
    c0 = recon.recon_all_command("S", "/T1", "/sd", None, use_t2=False, openmp=2)
    assert "-T2" not in c0 and c0[-1] == "2"
    ct2 = recon.recon_all_command("S", "/T1", "/sd", "/T2", use_t1=False, use_t2=True)
    assert ct2[:5] == ["recon-all", "-s", "S", "-i", "/T2"] and "-T2pial" not in ct2

    ch = charm.charm_command("TRACE105", "/x/T1.nii.gz", "/x/T2.nii.gz", "/fs/TRACE105")
    assert ch == ["charm", "TRACE105", "/x/T1.nii.gz", "/x/T2.nii.gz",
                  "--fs-dir", "/fs/TRACE105"], ch
    assert "--forcerun" in charm.charm_command("S", "/T1", None, None, forcerun=True)
    ch2 = charm.charm_command("S", "/T1", "/T2", "/fs", use_t2=False, use_fs=False)
    assert ch2 == ["charm", "S", "/T1"], ch2
    print("command builders: PASS")


def test_done_checks():
    with tempfile.TemporaryDirectory() as d:
        d = Path(d)
        sd = d / "fs" / "TRACE105" / "scripts"
        sd.mkdir(parents=True)
        (sd / "recon-all.log").write_text("recon-all ... finished without error\n")
        assert recon.is_recon_done(d / "fs", "TRACE105") is True
        assert recon.is_recon_done(d / "fs", "NOPE") is False
        m = d / "sim" / "m2m_TRACE105"
        m.mkdir(parents=True)
        (m / "TRACE105.msh").write_text("x")
        assert charm.is_charm_done(d / "sim", "TRACE105") is True
        assert charm.is_charm_done(d / "sim", "OTHER") is False
    print("done-checks: PASS")


if __name__ == "__main__":
    test_classify()
    test_commands()
    test_done_checks()
    print("\nALL FULL-PIPELINE OFFLINE TESTS PASS")
