#!/usr/bin/env python3
"""
pipeline_gui.py — ONE unified GUI for the whole APEX-MT dosing pipeline.

Three progressive stages; you supply the earliest input you have and the rest is
computed. Upstream inputs grey out once a downstream one is filled.

  Stage 1  Anatomy      DICOM/.tbz --> (extract + auto-ID) --> T1 / T2 NIfTI
                        (opens T1/T2 in FreeView to review; fields auto-fill)
  Stage 2  Head model   T1 (+T2) --> recon-all -> CHARM  (DETACHED: runs in the
                        background, survives closing this window; resumes on reopen)
                        --> m2m folder
  Stage 3  APEX dose    m2m + Brainsight --> sims -> APEX %MSO -> CSV + QC views

Fill an m2m folder to jump straight to Stage 3; fill T1/T2 to skip Stage 1; or run
recon-all/CHARM yourself and just drop the resulting path into the field.

Launch with a Python that has a working tkinter (Homebrew python after
`brew install python-tk`) — or use run_pipeline.command. The heavy work runs under
the SimNIBS python set in the field below; recon-all/CHARM run as detached shells.
"""

import json
import os
import queue
import subprocess
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

import charm
import recon
import stage_runner as sr
import didt_ref
import locate

HERE = Path(__file__).resolve().parent
DOSE_ENGINE = HERE / "apex_pipeline.py"
DICOM_CLI = HERE / "dicom.py"


# Auto-detect SimNIBS python + coil across macOS and Linux/WSL (see locate.py);
# fall back to the lab Mac default so the field is never blank.
DEFAULT_SIMPY = locate.simnibs_python() or "/Applications/SimNIBS-4.1/bin/simnibs_python"
DEFAULT_COIL = locate.coil_file() or (
    "/Applications/SimNIBS-4.1/simnibs/resources/coil_models/"
    "Drakaki_BrainStim_2022/MagVenture_Cool-B65.ccd")
DOSE_CONFIG = Path(os.path.expanduser("~")) / "_pipeline_dose_config.json"


def list_target_names(path):
    names, inb = [], False
    try:
        with open(path, "r", encoding="utf-8-sig", errors="replace") as fh:
            for line in fh:
                s = line.strip()
                if not s:
                    continue
                if s.startswith("#"):
                    low = s.lower()
                    if "target name" in low:
                        inb = True
                    elif "sample name" in low:
                        break
                    continue
                if not inb:
                    continue
                p = line.rstrip("\n").split("\t")
                if len(p) >= 13:
                    try:
                        [float(x) for x in p[1:13]]
                    except ValueError:
                        continue
                    names.append(p[0].strip())
    except OSError:
        pass
    return names


class PipelineGUI:
    def __init__(self, root):
        self.root = root
        root.title("APEX pipeline")
        root.geometry("980x960")
        root.minsize(900, 760)
        self.q = queue.Queue()
        self.last_dir = os.path.expanduser("~")
        self._hm_pos = None         # byte offset already shown; None = not yet positioned
        self._hm_active = False     # a head-model run is being tracked

        self.v = {k: tk.StringVar() for k in (
            "subject", "work", "simpy", "dicom", "t1", "t2", "t1_hint", "recon_dir",
            "m2m", "brainsight", "coil", "out", "m1", "rmt", "didt", "roi_mode",
            "radius", "pct", "percents", "custom", "openmp", "coord")}
        self.v["simpy"].set(DEFAULT_SIMPY)
        self.v["coil"].set(DEFAULT_COIL)
        self.v["rmt"].set("60"); self.v["didt"].set("87")
        self.v["roi_mode"].set("whole_gm"); self.v["radius"].set("10")
        self.v["pct"].set("99.9"); self.v["percents"].set("120,135")
        self.v["custom"].set("105"); self.v["openmp"].set("4")
        self.opt = {k: tk.BooleanVar(value=True) for k in
                    ("open_meshes", "show_spheres", "open_csv")}
        self.opt["overwrite"] = tk.BooleanVar(value=False)
        self.opt["stop_after_recon"] = tk.BooleanVar(value=False)
        self.opt["skip_recon"] = tk.BooleanVar(value=False)      # CHARM-only (no recon-all)
        self.opt["cleanup_local"] = tk.BooleanVar(value=False)   # delete local temp when done
        self.opt["recon_overwrite"] = tk.BooleanVar(value=False)  # re-run recon-all even if complete
        self.opt["charm_forcerun"] = tk.BooleanVar(value=False)   # charm --forcerun (overwrite m2m)
        self.opt["charm_forceqform"] = tk.BooleanVar(value=False) # charm --forceqform
        self.opt["charm_forcesform"] = tk.BooleanVar(value=False) # charm --forcesform
        self.v["local_tmp"] = tk.StringVar()                      # shows the local temp folder path
        for k in ("recon_t1", "recon_t2", "charm_t1", "charm_t2", "charm_fs"):
            self.opt[k] = tk.BooleanVar(value=True)   # recon/charm input toggles
        self.tgt_vars = {}
        self._togglable = []        # (widget, stage) for grey-out
        self._busy = set()          # names of stages currently running

        self._bs_loaded = None      # last Brainsight path whose targets we loaded
        self._build()
        for key in ("m2m", "t1", "dicom", "subject", "work"):
            self.v[key].trace_add("write", lambda *_: self._refresh())
        # load Brainsight targets whether the path is browsed OR pasted/typed
        self.v["brainsight"].trace_add("write", lambda *_: self._maybe_load_targets())
        # live "expected dI/dt" under the dI/dt box, updated as the rMT changes
        self.v["rmt"].trace_add("write", lambda *_: self._update_didt_est())
        self._update_didt_est()
        self._refresh()
        self.root.after(150, self._drain)
        self.root.after(1500, self._poll_headmodel)
        self.root.after(250, self._init_sash)

    def _init_sash(self):
        """On launch, show all of Stage 3 (so the 'Stim targets' list is visible,
        not hidden behind the log) and give the log the remaining space. The user
        can then drag the divider up to expand the log."""
        try:
            self.root.update_idletasks()
            h = self.paned.winfo_height()
            if h <= 200:
                return
            s3_req = self._s3.winfo_reqheight()      # full Stage 3 incl. a targets strip
            y = min(s3_req, h - 120)                  # keep >=120px for the log
            y = max(y, 200)
            self.paned.sash_place(0, 1, y)
        except Exception:
            pass

    # ---------- generic widgets ----------
    def _ask_open(self, cap, ft=None):
        p = filedialog.askopenfilename(title=cap, initialdir=self.last_dir,
                                       filetypes=ft or [("All", "*")])
        if p:
            self.last_dir = os.path.dirname(p)
        return p

    def _ask_dir(self, cap):
        p = filedialog.askdirectory(title=cap, initialdir=self.last_dir)
        if p:
            self.last_dir = p
        return p

    def _field(self, parent, label, var, kind="file", cap="Select", stage=None):
        f = tk.Frame(parent); f.pack(fill="x", padx=8, pady=2)
        tk.Label(f, text=label, width=18, anchor="w").pack(side="left")
        e = tk.Entry(f, textvariable=var); e.pack(side="left", fill="x", expand=True)
        if kind == "none":                     # plain text field, no Browse button
            if stage:
                self._togglable.append((e, stage))
            return e

        def pick():
            p = self._ask_dir(cap) if kind == "dir" else self._ask_open(cap)
            if p:
                var.set(p)
        b = tk.Button(f, text="Browse…", command=pick)
        b.pack(side="left", padx=4)
        if stage:
            self._togglable += [(e, stage), (b, stage)]
        return e

    def _build(self):
        top = tk.Frame(self.root); top.pack(fill="x")
        tk.Label(top, text="APEX pipeline", font=("Helvetica", 16, "bold")).pack(
            side="left", padx=10, pady=6)
        self.activity = tk.Label(top, text="● idle", fg="#888",
                                 font=("Helvetica", 12, "bold"))
        self.activity.pack(side="left", padx=14)
        tk.Button(top, text="Check environment", command=self._check_env).pack(
            side="right", padx=10)
        # transient warning banner (e.g. dI/dt vs rMT mismatch); shown ~5 s
        self.warn_label = tk.Label(top, text="", fg="#c0392b",
                                   font=("Helvetica", 12, "bold"))
        self.warn_label.pack(side="right", padx=10)

        # common
        cf = tk.Frame(self.root); cf.pack(fill="x")
        self._field(cf, "Subject ID", self.v["subject"], "none")
        self._field(cf, "Work folder", self.v["work"], "dir",
                    "Folder for freesurfer/ + simnibs/ outputs")
        self._field(cf, "simnibs_python", self.v["simpy"], "file", "Select simnibs_python")

        # Stage 1
        s1 = tk.LabelFrame(self.root, text="Stage 1 — Anatomy  (DICOM/archive → T1/T2)")
        s1.pack(fill="x", padx=8, pady=4)
        # DICOM path field (filled by whichever picker below matches your data) —
        # no generic Browse; the two explicit buttons make the choice clear.
        self._field(s1, "DICOM or archive", self.v["dicom"], "none", stage=1)
        tk.Label(s1, text="supported archives: .tbz/.tbz2/.tar.bz2, .tgz/.tar.gz, .tar.xz/.txz, "
                          ".tar, .zip  (or a folder of DICOMs)",
                 fg="#666", font=("Helvetica", 10)).pack(fill="x", padx=8, anchor="w")
        row = tk.Frame(s1); row.pack(fill="x", padx=8, pady=2)
        _arch_ft = [("Compressed archive",
                     "*.tbz *.tbz2 *.tar.bz2 *.tgz *.tar.gz *.tar.xz *.txz *.tar *.zip"),
                    ("All files", "*")]
        b_arch = tk.Button(row, text="Pick compressed file…",
                           command=lambda: self.v["dicom"].set(
                               self._ask_open("Select the compressed archive", _arch_ft) or self.v["dicom"].get()))
        b_arch.pack(side="left")
        tk.Label(row, text="— or —").pack(side="left", padx=8)
        b_folder = tk.Button(row, text="Pick DICOM folder…",
                             command=lambda: self.v["dicom"].set(
                                 self._ask_dir("Select the DICOM folder") or self.v["dicom"].get()))
        b_folder.pack(side="left")
        self._togglable += [(b_arch, 1), (b_folder, 1)]     # grey out with Stage 1 once m2m is set
        row2 = tk.Frame(s1); row2.pack(fill="x", padx=8, pady=(0, 2))
        self.b_extract = tk.Button(row2, text="① Extract & identify T1/T2",
                                   font=("Helvetica", 12, "bold"), command=self._extract)
        self.b_extract.pack(side="left")
        self._field(s1, "T1w (NIfTI)", self.v["t1"], "file", "Select T1w", stage=1)
        self._field(s1, "T2w (NIfTI)", self.v["t2"], "file", "Select T2w (optional)", stage=1)

        # Stage 2
        s2 = tk.LabelFrame(self.root, text="Stage 2 — Head model  (recon-all → CHARM)")
        s2.pack(fill="x", padx=8, pady=4)
        row2 = tk.Frame(s2); row2.pack(fill="x", padx=8, pady=2)
        self.b_build = tk.Button(row2, text="② Build head model (hours)",
                                 font=("Helvetica", 12, "bold"), command=self._build_headmodel)
        self.b_build.pack(side="left", padx=4)
        tk.Checkbutton(row2, text="stop after recon-all",
                       variable=self.opt["stop_after_recon"]).pack(side="left", padx=8)
        tk.Checkbutton(row2, text="skip recon-all (CHARM from T1/T2 only)",
                       variable=self.opt["skip_recon"]).pack(side="left", padx=8)
        # second options row (kept off the button row so nothing gets clipped)
        row2b = tk.Frame(s2); row2b.pack(fill="x", padx=8, pady=(0, 2))
        tk.Checkbutton(row2b, text="delete local temp when done",
                       variable=self.opt["cleanup_local"]).pack(side="left", padx=(4, 8))
        tk.Label(row2b, text="CPU cores:").pack(side="left")
        tk.Entry(row2b, textvariable=self.v["openmp"], width=4).pack(side="left")
        # input toggles for each stage
        ir = tk.Frame(s2); ir.pack(fill="x", padx=8)
        tk.Label(ir, text="recon-all inputs:").pack(side="left")
        tk.Checkbutton(ir, text="T1w", variable=self.opt["recon_t1"]).pack(side="left")
        tk.Checkbutton(ir, text="T2w", variable=self.opt["recon_t2"]).pack(side="left")
        tk.Label(ir, text="     CHARM inputs:").pack(side="left")
        tk.Checkbutton(ir, text="T1w", variable=self.opt["charm_t1"]).pack(side="left")
        tk.Checkbutton(ir, text="T2w", variable=self.opt["charm_t2"]).pack(side="left")
        tk.Checkbutton(ir, text="recon-all folder", variable=self.opt["charm_fs"]).pack(side="left")
        # re-run / force flags (for overwriting an already-built subject)
        fr = tk.Frame(s2); fr.pack(fill="x", padx=8)
        tk.Label(fr, text="re-run:").pack(side="left")
        tk.Checkbutton(fr, text="recon-all", variable=self.opt["recon_overwrite"]).pack(side="left")
        tk.Checkbutton(fr, text="CHARM (--forcerun)", variable=self.opt["charm_forcerun"]).pack(side="left")
        tk.Label(fr, text="   CHARM affine fix:").pack(side="left")
        tk.Checkbutton(fr, text="--forceqform", variable=self.opt["charm_forceqform"]).pack(side="left")
        tk.Checkbutton(fr, text="--forcesform", variable=self.opt["charm_forcesform"]).pack(side="left")
        self._field(s2, "recon-all folder", self.v["recon_dir"], "dir",
                    "Auto-filled after recon-all; or paste one you ran outside (skips recon-all)")
        self._field(s2, "m2m folder", self.v["m2m"], "dir",
                    "Auto-filled after CHARM; or paste an existing m2m to skip Stages 1–2")
        # read-only display of the build folder (work folder if local, else ~/APEX_local temp)
        lt = tk.Frame(s2); lt.pack(fill="x", padx=8, pady=2)
        tk.Label(lt, text="Build folder", width=18, anchor="w").pack(side="left")
        e_tmp = tk.Entry(lt, textvariable=self.v["local_tmp"], state="readonly",
                         readonlybackground="#eee", fg="#555")
        e_tmp.pack(side="left", fill="x", expand=True)
        self.hm_status = tk.Label(s2, text="", anchor="w", fg="#444")
        self.hm_status.pack(fill="x", padx=8)

        # Stage 3 (dose) and the log share a DRAGGABLE vertical split: drag the
        # divider to grow the log — it trades space with the targets list below.
        self.paned = tk.PanedWindow(self.root, orient=tk.VERTICAL, sashwidth=8,
                                    sashrelief="raised", showhandle=True, bg="#b9b9c6")
        self.paned.pack(fill="both", expand=True, padx=8, pady=(2, 8))

        s3 = tk.LabelFrame(self.paned, text="Stage 3 — APEX dose")
        f = tk.Frame(s3); f.pack(fill="x", padx=8, pady=2)
        tk.Label(f, text="Brainsight .txt", width=18, anchor="w").pack(side="left")
        tk.Entry(f, textvariable=self.v["brainsight"]).pack(side="left", fill="x", expand=True)
        tk.Button(f, text="Browse…", command=self._pick_brainsight).pack(side="left", padx=4)
        self._field(s3, "Coil (.ccd)", self.v["coil"], "file", "Select coil")
        self._field(s3, "Output parent", self.v["out"], "dir",
                    "Any folder — an apex_<subject> subfolder is created inside")
        tr = tk.Frame(s3); tr.pack(fill="x", padx=8)
        tk.Label(tr, text="M1 (rMT hotspot):", width=18, anchor="w").pack(side="left")
        self.m1_combo = ttk.Combobox(tr, textvariable=self.v["m1"], state="readonly")
        self.m1_combo.pack(side="left", fill="x", expand=True)

        # params + options + Run button are FIXED (packed first, so they stay
        # visible when the targets list is shrunk to give the log more room)
        pf = tk.Frame(s3); pf.pack(fill="x", padx=8, pady=2)
        def param(lbl, var, w=7):
            g = tk.Frame(pf); g.pack(side="left", padx=4)
            tk.Label(g, text=lbl).pack()
            e = tk.Entry(g, textvariable=var, width=w); e.pack()
            return e
        param("rMT %MSO", self.v["rmt"])
        # dI/dt with a live "expected" value under it (MagVenture curve, sanity check)
        gd = tk.Frame(pf); gd.pack(side="left", padx=4)
        tk.Label(gd, text="dI/dt A/µs").pack()
        tk.Entry(gd, textvariable=self.v["didt"], width=7).pack()
        self.didt_est = tk.Label(gd, text="", fg="#0a7a5a", font=("Helvetica", 9))
        self.didt_est.pack()
        g = tk.Frame(pf); g.pack(side="left", padx=4)
        tk.Label(g, text="ROI mode").pack()
        ttk.Combobox(g, textvariable=self.v["roi_mode"], width=11, state="readonly",
                     values=["whole_gm", "coil_nearest", "coil_axis", "coord"]).pack()
        self.radius_entry = param("radius", self.v["radius"]); param("pct", self.v["pct"])
        param("percents", self.v["percents"], 12); param("custom%", self.v["custom"])
        # coord entry: same style as 'percents', greyed out unless ROI mode == 'coord'
        self.coord_entry = param("x;y;z (coord)", self.v["coord"], 12)
        of = tk.Frame(s3); of.pack(fill="x", padx=8)
        self.spheres_cb = None
        for k, lbl in [("open_meshes", "Gmsh"), ("show_spheres", "FreeView spheres"),
                       ("open_csv", "open CSV"), ("overwrite", "overwrite")]:
            cb = tk.Checkbutton(of, text=lbl, variable=self.opt[k]); cb.pack(side="left", padx=4)
            if k == "show_spheres":
                self.spheres_cb = cb
        # radius / coord / sphere-viz are only meaningful for a spherical ROI
        self.v["roi_mode"].trace_add("write", lambda *_: self._sync_roi_widgets())
        self._sync_roi_widgets()

        # The Run button is pinned to the BOTTOM of Stage 3 (packed BEFORE the
        # targets canvas so it always stays visible above the divider, below the
        # targets list, even when the targets area is shrunk to grow the log).
        self.b_dose = tk.Button(s3, text="③ Run APEX dose",
                                font=("Helvetica", 13, "bold"), command=self._run_dose)
        self.b_dose.pack(side="bottom", anchor="w", padx=8, pady=(4, 6))

        # targets list: expandable, filling the space between the label and the
        # Run button, so it flexes with the pane
        tk.Label(s3, text="Stim targets (tick any):", anchor="w").pack(side="top", fill="x", padx=8)
        canv = tk.Canvas(s3, height=90, highlightthickness=1, highlightbackground="#ccc")
        scb = tk.Scrollbar(s3, orient="vertical", command=canv.yview)
        self.tgt_inner = tk.Frame(canv)
        self.tgt_inner.bind("<Configure>", lambda e: canv.configure(scrollregion=canv.bbox("all")))
        canv.create_window((0, 0), window=self.tgt_inner, anchor="nw")
        canv.configure(yscrollcommand=scb.set)
        canv.pack(side="top", fill="both", expand=True, padx=8, pady=(0, 4))
        scb.place(in_=canv, relx=1.0, rely=0, relheight=1.0, anchor="ne")
        # minsize keeps the whole Stage 3 (targets strip + Run button) on screen
        self._s3 = s3
        self.paned.add(s3, minsize=250, stretch="always")

        self.log = tk.Text(self.paned, height=12, bg="#1c1c2b", fg="#cdd5f2", wrap="word")
        self.paned.add(self.log, minsize=90, stretch="always")

    # ---------- enable/disable (progressive grey-out) ----------
    def _refresh(self):
        have_m2m = bool(self.v["m2m"].get().strip())
        have_t1 = bool(self.v["t1"].get().strip())
        have_dcm = bool(self.v["dicom"].get().strip())
        # Stages 1 upstream inputs grey out once we have an m2m
        for w, stage in self._togglable:
            if stage == 1:
                w.config(state="disabled" if have_m2m else "normal")
        self.b_extract.config(state="normal" if (have_dcm and not have_m2m) else "disabled")
        self.b_build.config(state="normal" if (have_t1 and not have_m2m) else "disabled")
        self.b_dose.config(state="normal" if have_m2m else "disabled")

    def _work_paths(self):
        work = Path(self.v["work"].get().strip())
        return (work / "freesurfer", work / "simnibs",
                work / "logs" / "headmodel.log")

    # ---------- Stage 1 ----------
    def _extract(self):
        for k, lbl in [("dicom", "DICOM/.tbz"), ("work", "work folder"),
                       ("subject", "subject ID"), ("simpy", "simnibs_python")]:
            if not self.v[k].get().strip():
                messagebox.showerror("Missing", f"Set: {lbl}"); return
        work = Path(self.v["work"].get().strip()) / "dicom_work"
        cmd = [self.v["simpy"].get().strip(), str(DICOM_CLI),
               self.v["dicom"].get().strip(), str(work),
               self.v["t1_hint"].get().strip(), ""]
        self._say("\n$ " + " ".join(cmd) + "\n")
        self.b_extract.config(state="disabled")
        self._set_busy("extract T1/T2", True)
        threading.Thread(target=self._extract_worker, args=(cmd,), daemon=True).start()

    def _extract_worker(self, cmd):
        try:
            p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                 text=True, bufsize=1)
            for line in p.stdout:
                self.q.put(line)
                if line.startswith("RESULT_T1="):
                    self.q.put(("SET_T1", line.split("=", 1)[1].strip()))
                elif line.startswith("RESULT_T2="):
                    self.q.put(("SET_T2", line.split("=", 1)[1].strip()))
            p.wait()
            self.q.put(f"[extract] exit {p.returncode}\n")
        except Exception as e:
            self.q.put(f"[extract error] {e}\n")
        finally:
            self.q.put(("ENABLE", "extract"))

    # ---------- Stage 2 (detached head model) ----------
    def _build_headmodel(self):
        # Only work + subject are needed to LOCATE existing outputs; T1 and the rest
        # are validated in _launch_headmodel, only when something will actually run.
        for k, lbl in [("work", "work folder"), ("subject", "subject ID")]:
            if not self.v[k].get().strip():
                messagebox.showerror("Missing", f"Set: {lbl}"); return
        subject = self.v["subject"].get().strip()
        work = self.v["work"].get().strip()
        if self.opt["charm_forceqform"].get() and self.opt["charm_forcesform"].get():
            messagebox.showerror("CHARM affine fix",
                                 "--forceqform and --forcesform conflict — pick at most one."); return

        import spacefree
        local_fs, local_sp = spacefree.local_headmodel_dirs(subject)
        share_fs, share_sp = spacefree.share_headmodel_dirs(work)
        field_recon = self.v["recon_dir"].get().strip()
        field_m2m = self.v["m2m"].get().strip()

        # ---- detect existing, COMPLETE outputs (pasted field, work folder, local temp) ----
        def _recon_done_at(p):
            p = Path(p)
            return recon.is_recon_done(p.parent, p.name)
        found_recon = None
        for cand in ([field_recon] if field_recon else []) + \
                    [str(share_fs / subject), str(local_fs / subject)]:
            if _recon_done_at(cand):
                found_recon = cand; break
        recon_done = found_recon is not None

        m2m_found = None
        for cand in ([field_m2m] if field_m2m else []) + \
                    [str(share_sp / f"m2m_{subject}"), str(local_sp / f"m2m_{subject}")]:
            if charm.is_m2m_valid(cand):
                m2m_found = cand; break
        m2m_done = m2m_found is not None

        redo_recon = self.opt["recon_overwrite"].get()
        redo_charm = self.opt["charm_forcerun"].get()
        skip_recon = self.opt["skip_recon"].get()
        stop_recon = self.opt["stop_after_recon"].get()
        use_fs = self.opt["charm_fs"].get()

        def _show_recon():
            # place the found recon-all folder in the field, for visualization
            if found_recon and not self.v["recon_dir"].get().strip():
                self.v["recon_dir"].set(str(found_recon))

        # ============ DECIDE WHAT TO RUN (first matching rule wins) ============
        # Rule 1 — m2m already complete and nothing forces a rebuild: do nothing.
        if m2m_done and not redo_charm and not redo_recon and not stop_recon:
            self.v["m2m"].set(m2m_found)
            _show_recon()
            self._say(f"[head model] m2m already complete — filled in; nothing to run:\n  {m2m_found}\n")
            self._refresh()
            return

        # Rule 2 — stop after recon-all: run recon only.
        if stop_recon:
            if recon_done and not redo_recon:
                _show_recon()
                self._say(f"[head model] recon-all already complete — nothing to run:\n  {found_recon}\n")
                return
            self._launch_headmodel(recon_dir=None, skip_recon=False,
                                   stop_after_recon=True, recon_overwrite=redo_recon)
            return

        # Rule 3 — CHARM without recon-all (skip recon-all, or 'recon-all folder' unticked for CHARM).
        if skip_recon or not use_fs:
            _show_recon()   # informational only; CHARM won't use it in this mode
            self._launch_headmodel(recon_dir=None, skip_recon=True,
                                   stop_after_recon=False, recon_overwrite=False)
            return

        # Rule 4 — reuse an existing COMPLETE recon-all, run CHARM (recon-all not re-run).
        if not redo_recon and recon_done:
            _show_recon()
            self._launch_headmodel(recon_dir=found_recon, skip_recon=False,
                                   stop_after_recon=False, recon_overwrite=False)
            return

        # Rule 5 — full build: fresh recon-all, then CHARM.
        self._launch_headmodel(recon_dir=None, skip_recon=False,
                               stop_after_recon=False, recon_overwrite=redo_recon)

    def _launch_headmodel(self, *, recon_dir, skip_recon, stop_after_recon, recon_overwrite):
        """Validate the inputs for what will actually run, build the detached shell,
        and launch it. Called by _build_headmodel once the decision is made."""
        subject = self.v["subject"].get().strip()
        work = self.v["work"].get().strip()
        t1 = self.v["t1"].get().strip()
        t2 = self.v["t2"].get().strip() or None
        _sd, _sp, log = self._work_paths()

        try:
            openmp = int(self.v["openmp"].get() or 4)
            if openmp < 1:
                raise ValueError
        except ValueError:
            messagebox.showerror("Bad number", "CPU cores must be a positive whole number."); return

        will_run_recon = (not recon_dir) and (not skip_recon)   # a FRESH recon-all
        will_run_charm = not stop_after_recon

        if recon_dir and not recon.is_recon_done(Path(recon_dir).parent, Path(recon_dir).name):
            messagebox.showerror(
                "recon-all folder",
                f"The recon-all folder is not a finished FreeSurfer subject "
                f"(no scripts/recon-all.log ending in 'finished without error'):\n\n{recon_dir}")
            return
        if will_run_recon and not ((self.opt["recon_t1"].get() and t1) or
                                   (self.opt["recon_t2"].get() and t2)):
            messagebox.showerror(
                "recon-all inputs",
                "recon-all needs a primary input. Tick 'recon-all inputs: T1w' (recommended) and set "
                "a T1w file — or tick 'skip recon-all' to build CHARM from T1/T2 only.")
            return
        if will_run_charm:
            if not (self.opt["charm_t1"].get() and t1):
                messagebox.showerror(
                    "CHARM inputs",
                    "CHARM needs a T1w as its first input. Tick 'CHARM inputs: T1w' and set a T1w file. "
                    "(A T2w/FLAIR may be added, but cannot replace the T1w.)")
                return
            if charm.find_charm() is None:
                messagebox.showerror(
                    "charm not found",
                    "Could not find the SimNIBS 'charm' program. Install SimNIBS or put it on PATH.")
                return

        import spacefree
        hp = spacefree.headmodel_build_paths(work, subject, t1, t2)
        self.v["local_tmp"].set(str(hp.local_root))
        cleanup = self.opt["cleanup_local"].get()

        if skip_recon:
            self._say("[head model] skip recon-all: CHARM will segment from T1"
                      f"{'+T2' if t2 else ''} only (no FreeSurfer surfaces).\n")
        elif recon_dir:
            self._say(f"[head model] reusing recon-all (skipping recon-all):\n  {recon_dir}\n")

        if hp.direct:
            self._say(f"[head model] work folder is local & symlink-capable — building DIRECTLY in it, "
                      f"no temp copy:\n  {hp.local_root}\n")
            if cleanup:
                self._say("[head model] ('delete local temp' ignored — there is no temp folder).\n")
            share_sp_arg = share_fs_arg = None
            cleanup = False
            local_root_arg = None
        else:
            self._say(f"[head model] building in a local temp folder:\n  {hp.local_root}\n"
                      f"[head model] the finished m2m is copied to your work folder:\n  {hp.share_simnibs_parent}\n")
            if cleanup:
                self._say("[head model] cleanup ON — after the m2m is safely on the share, the recon-all "
                          "output is archived there and the local temp folder is deleted.\n")
            share_sp_arg = hp.share_simnibs_parent
            share_fs_arg = hp.share_subjects_dir
            local_root_arg = hp.local_root

        shell = charm.head_model_shell(
            subject, hp.t1, hp.subjects_dir, hp.simnibs_parent, t2=hp.t2,
            openmp=openmp,
            stop_after_recon=stop_after_recon,
            skip_recon=skip_recon,
            recon_overwrite=recon_overwrite,
            charm_forcerun=self.opt["charm_forcerun"].get(),
            charm_forceqform=self.opt["charm_forceqform"].get(),
            charm_forcesform=self.opt["charm_forcesform"].get(),
            recon_dir=recon_dir,
            recon_use_t1=self.opt["recon_t1"].get(),
            recon_use_t2=self.opt["recon_t2"].get(),
            charm_use_t1=self.opt["charm_t1"].get(),
            charm_use_t2=self.opt["charm_t2"].get(),
            charm_use_fs=self.opt["charm_fs"].get(),
            share_simnibs_parent=share_sp_arg,
            share_subjects_dir=share_fs_arg,
            cleanup_local=cleanup,
            local_root=local_root_arg)
        log.parent.mkdir(parents=True, exist_ok=True)
        open(log, "w").close()          # fresh log for this launch
        self._hm_pos = 0
        pid = sr.launch_detached(shell, log, cwd=str(hp.simnibs_parent))
        self._hm_active = True
        self._say(f"\n[head model] launched detached (pid {pid}); log:\n  {log}\n"
                  f"[head model] you can close this window — it keeps running.\n")

    def _poll_headmodel(self):
        try:
            subject = self.v["subject"].get().strip()
            work = self.v["work"].get().strip()
            if subject and work:
                import spacefree
                local_fs, local_sp = spacefree.local_headmodel_dirs(subject)
                share_fs, share_sp = spacefree.share_headmodel_dirs(work)
                if not self.v["local_tmp"].get():
                    # show the build root that actually has content (temp vs direct-in-work)
                    temp_root = spacefree.local_build_root(subject)
                    if (temp_root / "freesurfer").exists() or (temp_root / "simnibs").exists():
                        self.v["local_tmp"].set(str(temp_root))
                    elif (share_fs / subject).exists() or (share_sp / f"m2m_{subject}").exists():
                        self.v["local_tmp"].set(str(Path(work)))
                _sd, _sp, log = self._work_paths()   # log path in the work folder
                pid = sr.read_pid(log)
                # Position at the END of any pre-existing log on first sight, so we
                # never REPLAY a finished build's log (that looked like a live run).
                if self._hm_pos is None:
                    try:
                        self._hm_pos = Path(log).stat().st_size if Path(log).is_file() else 0
                    except OSError:
                        self._hm_pos = 0
                # Is a head-model run actually ACTIVE? (launched this session, or a
                # detached run still alive / writing its log — incl. another machine)
                import time as _t
                try:
                    log_fresh = Path(log).is_file() and (_t.time() - Path(log).stat().st_mtime) < 1200
                except OSError:
                    log_fresh = False
                active = self._hm_active or sr.is_running(pid) or log_fresh

                # display existing outputs (idle-safe): show a found recon-all folder
                done_recon_local = recon.is_recon_done(local_fs, subject)
                done_recon_share = recon.is_recon_done(share_fs, subject)
                done_recon = done_recon_local or done_recon_share
                if not self.v["recon_dir"].get().strip():
                    if done_recon_local:
                        self.v["recon_dir"].set(str(local_fs / subject))
                    elif done_recon_share:
                        self.v["recon_dir"].set(str(share_fs / subject))

                if not active:
                    self._set_busy("head model", False)
                    self.root.after(2500, self._poll_headmodel)
                    return

                # stream new log content (only for an active run)
                if Path(log).is_file():
                    data = Path(log).read_bytes()[self._hm_pos:]
                    if data:
                        self._say(data.decode(errors="replace"))
                        self._hm_pos += len(data)
                done_charm_local = charm.is_charm_done(local_sp, subject)
                done_charm_share = charm.is_charm_done(share_sp, subject)
                stop_recon = self.opt["stop_after_recon"].get()
                # finish line: the share copy (or recon, if CHARM is skipped)
                done = done_charm_share or (stop_recon and done_recon)
                st = sr.status(done, pid, log)
                self._set_busy("head model", st == "running")
                if pid:
                    charm_tag = ("  (CHARM done)" if (done_charm_local or done_charm_share)
                                 else ("  (recon done)" if done_recon else ""))
                    self.hm_status.config(text=f"head model: {st}{charm_tag}")
                if self._hm_active and st in ("done", "failed"):
                    self._hm_active = False
                    if done_charm_share:
                        self.v["m2m"].set(str(share_sp / f"m2m_{subject}"))
                        self._say("[head model] CHARM finished — m2m ready in your work "
                                  "folder and filled in.\n")
                    elif done_charm_local:
                        # CHARM built locally but the copy to the share didn't land
                        self.v["m2m"].set(str(charm.m2m_path(local_sp, subject)))
                        self._say("[head model] CHARM finished, but copying m2m to the work "
                                  "folder may have failed — using the local m2m (see log).\n")
                    elif stop_recon and done_recon:
                        self._say("[head model] recon-all finished (stop-after-recon). "
                                  "Untick it and press ② again to run CHARM.\n")
                    elif st == "failed":
                        if done_recon_local:
                            self._say(
                                "[head model] FAILED — recon-all completed but CHARM did not finish. "
                                "recon-all is preserved and its folder is filled in above, so pressing "
                                "② again re-runs ONLY CHARM (recon is reused). If CHARM's segmentation "
                                "was the problem, try a 'CHARM affine fix' (--forceqform / --forcesform) "
                                "or 're-run CHARM'. See the log above for the CHARM error.\n")
                        else:
                            self._say("[head model] FAILED — see the log above. Fix the cause and press "
                                      "② again (or run recon-all/CHARM manually and paste the m2m path).\n")
        except Exception as e:
            self._say(f"[poll error] {e}\n")
        self.root.after(2500, self._poll_headmodel)

    # ---------- Stage 3 (dose) ----------
    def _pick_brainsight(self):
        p = self._ask_open("Brainsight .txt", [("Text", "*.txt"), ("All", "*")])
        if p:
            self.v["brainsight"].set(p)   # the trace loads the targets (browsed or pasted)

    def _maybe_load_targets(self):
        """Load Brainsight targets when the path points at a readable file — works
        for a pasted/typed path, not just the Browse button."""
        p = self.v["brainsight"].get().strip()
        if p and os.path.isfile(p) and self._bs_loaded != p:
            self._bs_loaded = p
            self._load_targets(p)

    def _sync_roi_widgets(self):
        """Grey out ROI-only controls when they don't apply:
          - whole_gm  -> no ROI sphere: grey the radius, turn OFF + grey 'FreeView
                         spheres' (no per-target sphere to show).
          - coord     -> enable the x;y;z box; other modes grey it out.
        radius stays editable for coil_nearest / coil_axis / coord."""
        mode = self.v["roi_mode"].get()
        whole = mode == "whole_gm"
        try:
            self.coord_entry.config(state="normal" if mode == "coord" else "disabled")
            self.radius_entry.config(state="disabled" if whole else "normal")
            if whole:
                self.opt["show_spheres"].set(False)
                self.spheres_cb.config(state="disabled")
            else:
                self.spheres_cb.config(state="normal")
                self.opt["show_spheres"].set(True)
        except Exception:
            pass

    def _update_didt_est(self):
        """Show the dI/dt expected for the entered rMT (MagVenture Cool-B65 curve)."""
        try:
            mso = float(self.v["rmt"].get())
            self.didt_est.config(text=f"≈ {didt_ref.est_didt(mso):.0f} @ {mso:.0f}%")
        except (ValueError, Exception):
            self.didt_est.config(text="")

    def _warn_didt_mismatch(self, mso, didt):
        """If the entered dI/dt is grossly inconsistent with the rMT %MSO (MagVenture
        Cool-B65 curve), print a prominent banner and flash a 5-second top warning.
        Does NOT stop the run — it's a data-entry sanity check."""
        try:
            very_off, implied, expected = didt_ref.mismatch(mso, didt)
        except Exception:
            return
        if not very_off:
            return
        bar = "#" * 66
        self._say(
            f"\n{bar}\n"
            f"#  WARNING: dI/dt = {didt:g} A/µs looks inconsistent with rMT = {mso:g}% MSO.\n"
            f"#  On the MagVenture Cool-B65 curve, {didt:g} A/µs corresponds to ~{implied:.0f}% MSO,\n"
            f"#  and {mso:g}% MSO corresponds to ~{expected:.0f} A/µs.\n"
            f"#  Please double-check the dI/dt / %MSO reading. (Proceeding with the run anyway.)\n"
            f"{bar}\n")
        self.warn_label.config(text="⚠ Check dI/dt vs rMT — see log")
        self.root.after(5000, lambda: self.warn_label.config(text=""))

    def _load_targets(self, path):
        names = list_target_names(path)
        for w in self.tgt_inner.winfo_children():
            w.destroy()
        self.tgt_vars = {}
        if not names:
            self._say(f"! no targets parsed from {Path(path).name}\n"); return
        self.m1_combo["values"] = names
        for n in names:
            if "m1" in n.lower():
                self.v["m1"].set(n); break
        for n in names:
            var = tk.BooleanVar(value=False); self.tgt_vars[n] = var
            tk.Checkbutton(self.tgt_inner, text=n, variable=var, anchor="w").pack(fill="x", anchor="w")
        self._say(f"loaded {len(names)} targets from {Path(path).name}\n")

    def _run_dose(self):
        need = {"m2m": "m2m folder", "brainsight": "Brainsight file",
                "coil": "coil", "out": "output parent", "simpy": "simnibs_python"}
        for k, lbl in need.items():
            if not self.v[k].get().strip():
                messagebox.showerror("Missing", f"Set: {lbl}"); return
        # an m2m is valid only if it holds the head mesh (<id>.msh), not just a folder
        m2m_dir = self.v["m2m"].get().strip()
        if not charm.is_m2m_valid(m2m_dir):
            messagebox.showerror(
                "m2m folder",
                f"That m2m folder is not a completed head model — no <id>.msh inside:\n\n"
                f"{m2m_dir}\n\nPoint at a finished m2m_<id> folder (build it in Stage 2, "
                f"or paste an existing completed one).")
            return
        m1 = self.v["m1"].get().strip()
        if not m1:
            messagebox.showerror("Missing", "Choose M1."); return
        sel = [n for n, v in self.tgt_vars.items() if v.get() and n != m1]
        if not sel:
            messagebox.showerror("Missing", "Tick at least one stim target."); return
        # ROI mode 'coord': parse & validate the x;y;z sphere center
        roi_mode = self.v["roi_mode"].get()
        coord = None
        if roi_mode == "coord":
            parts = [p for p in self.v["coord"].get().replace(";", " ").replace(",", " ").split() if p]
            try:
                coord = [float(x) for x in parts]
            except ValueError:
                coord = None
            if not coord or len(coord) != 3:
                messagebox.showerror(
                    "Coordinate",
                    "ROI mode 'coord' needs an x;y;z coordinate — three numbers, e.g. "
                    "-42;-56;38 (subject/scanner space, same coordinates as your m2m).")
                return
        try:
            cfg = {
                "m2m_dir": self.v["m2m"].get().strip(),
                "brainsight_file": self.v["brainsight"].get().strip(),
                "coil_file": self.v["coil"].get().strip(),
                "out_dir": self.v["out"].get().strip(),
                "m1_target": m1, "targets": {s: s for s in sel},
                "didt_A_per_us": float(self.v["didt"].get()),
                "rmt_pct": float(self.v["rmt"].get()),
                "radius_mm": float(self.v["radius"].get()),
                "pct": float(self.v["pct"].get()),
                "roi_mode": roi_mode,
                "percents": [float(x) for x in self.v["percents"].get().split(",") if x.strip()],
                "custom_pct": float(self.v["custom"].get()) if self.v["custom"].get().strip() else None,
                "open_meshes": self.opt["open_meshes"].get(),
                "show_spheres": self.opt["show_spheres"].get(),
                "open_csv": self.opt["open_csv"].get(),
                "overwrite": self.opt["overwrite"].get(),
            }
        except ValueError as e:
            messagebox.showerror("Bad number", str(e)); return
        if coord is not None:
            cfg["coord"] = coord
        # sanity check: is the entered dI/dt consistent with the rMT %MSO? (does NOT stop the run)
        self._warn_didt_mismatch(cfg["rmt_pct"], cfg["didt_A_per_us"])
        DOSE_CONFIG.write_text(json.dumps(cfg, indent=2))
        cmd = [self.v["simpy"].get().strip(), str(DOSE_ENGINE), str(DOSE_CONFIG)]
        self._say("\n$ " + " ".join(cmd) + "\n")
        self.b_dose.config(state="disabled")
        self._set_busy("dose", True)
        threading.Thread(target=self._dose_worker, args=(cmd,), daemon=True).start()

    def _dose_worker(self, cmd):
        try:
            p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                 text=True, bufsize=1)
            for line in p.stdout:
                self.q.put(line)
            p.wait()
            self.q.put(f"[dose] exit {p.returncode}\n")
        except Exception as e:
            self.q.put(f"[dose error] {e}\n")
        finally:
            self.q.put(("ENABLE", "dose"))

    # ---------- environment check ----------
    def _check_env(self):
        simpy = self.v["simpy"].get().strip() or DEFAULT_SIMPY
        cmd = [simpy, str(HERE / "check_setup.py")]
        self._say("\n$ " + " ".join(cmd) + "\n")
        threading.Thread(target=self._simple_worker, args=(cmd,), daemon=True).start()

    def _simple_worker(self, cmd):
        try:
            p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                 text=True, bufsize=1)
            for line in p.stdout:
                self.q.put(line)
            p.wait()
            self.q.put(f"[exit {p.returncode}]\n")
        except Exception as e:
            self.q.put(f"[error] {e}\n")

    # ---------- queue drain ----------
    def _drain(self):
        try:
            while True:
                item = self.q.get_nowait()
                if isinstance(item, tuple):
                    tag, val = item
                    if tag == "SET_T1" and val:
                        self.v["t1"].set(val)
                    elif tag == "SET_T2":
                        self.v["t2"].set(val)
                    elif tag == "ENABLE":
                        self._set_busy("extract T1/T2" if val == "extract" else "dose", False)
                        self._refresh()
                else:
                    self._say(item)
        except queue.Empty:
            pass
        self.root.after(150, self._drain)

    def _say(self, text):
        self.log.insert("end", text); self.log.see("end")

    def _set_busy(self, name, on):
        if on:
            self._busy.add(name)
        else:
            self._busy.discard(name)
        if self._busy:
            self.activity.config(text="● " + ", ".join(sorted(self._busy)) + " running…",
                                 fg="#B26A00")
        else:
            self.activity.config(text="● idle", fg="#888")


if __name__ == "__main__":
    root = tk.Tk()
    PipelineGUI(root)
    root.mainloop()
