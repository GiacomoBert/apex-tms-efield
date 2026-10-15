import sys, os, tempfile, tarfile, zipfile; sys.path.insert(0,".")
from pathlib import Path
import dicom_input as di

def make_fake_dicom(p):
    with open(p,"wb") as f:
        f.write(b"\x00"*128 + b"DICM" + b"\x00"*64)   # DICM magic at offset 128

with tempfile.TemporaryDirectory() as d:
    d = Path(d)
    # build a source tree: series folder with 3 fake dicoms (extensionless)
    src = d/"115TR"/"series1"; src.mkdir(parents=True)
    for i in range(3): make_fake_dicom(src/f"IM{i:04d}")
    open(d/"115TR"/"README.txt","w").write("not a dicom")

    # --- pack as .tbz (tar.bz2) like the real scanner export ---
    tbz = d/"study.tbz"
    with tarfile.open(tbz, "w:bz2") as tf:
        tf.add(d/"115TR", arcname="115TR")
    assert di.is_archive(tbz) and di.is_archive("x.tar.gz") and di.is_archive("y.zip")
    assert not di.is_archive("folder")

    work = d/"work"
    root = di.resolve_dicom_input(tbz, work)
    n = di.count_dicoms(root)
    print(f".tbz extracted -> {root.name}/, DICOMs found: {n}")
    assert n == 3, n

    # --- folder input path ---
    root2 = di.resolve_dicom_input(d/"115TR", work)
    assert di.count_dicoms(root2) == 3

    # --- zip path ---
    z = d/"study.zip"
    with zipfile.ZipFile(z,"w") as zf:
        for i in range(2): zf.writestr(f"s/IM{i}", b"\x00"*128+b"DICM"+b"\x00"*8)
    assert di.count_dicoms(di.resolve_dicom_input(z, d/"work2")) == 2

    # --- path traversal guard ---
    eviltar = d/"evil.tar"
    with tarfile.open(eviltar,"w") as tf:
        info = tarfile.TarInfo(name="../escape.txt"); data=b"x"
        import io; info.size=len(data); tf.addfile(info, io.BytesIO(data))
    try:
        di.extract_archive(eviltar, d/"safe"); print("FAIL: traversal not caught")
    except ValueError as e:
        print("path-traversal guard: PASS ("+str(e)[:40]+"...)")

    # --- non-dicom folder rejected ---
    empty = d/"empty"; empty.mkdir()
    try:
        di.resolve_dicom_input(empty, work); print("FAIL: empty allowed")
    except FileNotFoundError:
        print("empty-folder rejection: PASS")

print("\nALL DICOM-INPUT TESTS PASS")
