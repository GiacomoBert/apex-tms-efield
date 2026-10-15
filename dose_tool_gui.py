#!/usr/bin/env python3
"""
dose_tool_gui.py — Short-process APEX Dose Tool (GUI).

For when the subject's `m2m_<id>` folder already exists (CHARM already run).
Pick the Brainsight file, m2m folder, coil, and an output *parent* folder; choose
M1 and the target sites (checkboxes) populated from the Brainsight file; enter rMT
and dI/dt; click Run. It writes a config and runs the tested engine
(`apex_pipeline.py`) under SimNIBS's python, streaming the log live, then opens the
E-field meshes (Gmsh), the sphere-on-brain views (FreeView), and the results CSV.

Launch it with a Python that has a WORKING tkinter (on this Mac: Homebrew's python
after `brew install python-tk`), from inside the v2_python folder — or just use
run_dose_tool.command, which finds it for you. The engine always runs under the
SimNIBS python set in the field below.
"""

import json
import os
import queue
import subprocess
import sys
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

HERE = Path(__file__).resolve().parent
ENGINE = HERE / "apex_pipeline.py"
DEFAULT_SIMNIBS_PY = "/Applications/SimNIBS-4.1/bin/simnibs_python"
DEFAULT_COIL = ("/Applications/SimNIBS-4.1/simnibs/resources/coil_models/"
                "Drakaki_BrainStim_2022/MagVenture_Cool-B65.ccd")
CONFIG_PATH = Path(os.path.expanduser("~")) / "_dose_tool_config.json"


def list_target_names(brainsight_txt):
    """Stdlib-only: return the target names in a Brainsight export (the block
    between the '# Target Name' header and the '# Sample Name' header)."""
    names, in_block = [], False
    try:
        with open(brainsight_txt, "r", encoding="utf-8-sig", errors="replace") as fh:
            for line in fh:
                s = line.strip()
                if not s:
                    continue
                if s.startswith("#"):
                    low = s.lower()
                    if "target name" in low:
                        in_block = True
                    elif "sample name" in low:
                        break
                    continue
                if not in_block:
                    continue
                parts = line.rstrip("\n").split("\t")
                if len(parts) >= 13:
                    try:
                        [float(x) for x in parts[1:13]]
                    except ValueError:
                        continue
                    names.append(parts[0].strip())
    except OSError:
        pass
    return names


class DoseToolGUI:
    def __init__(self, root):
        self.root = root
        root.title("APEX Dose Tool (short process)")
        root.geometry("900x800")
        self.q = queue.Queue()
        self.last_dir = os.path.expanduser("~")   # Browse remembers last location

        self.v = {
            "brainsight": tk.StringVar(),
            "m2m": tk.StringVar(),
            "coil": tk.StringVar(value=DEFAULT_COIL),
            "out": tk.StringVar(),
            "simpy": tk.StringVar(value=DEFAULT_SIMNIBS_PY),
            "rmt": tk.StringVar(value="48"),
            "didt": tk.StringVar(value="90"),
            "roi_mode": tk.StringVar(value="whole_gm"),
            "radius": tk.StringVar(value="10"),
            "pct": tk.StringVar(value="99.9"),
            "percents": tk.StringVar(value="100,120,135"),
            "custom": tk.StringVar(value="105"),
            "m1": tk.StringVar(),
        }
        self.opt = {k: tk.BooleanVar(value=True)
                    for k in ("open_meshes", "show_spheres", "open_csv")}
        self.opt["overwrite"] = tk.BooleanVar(value=False)
        self.tgt_vars = {}   # target name -> BooleanVar (checkboxes)

        self._build()
        self.root.after(120, self._drain)

    # ---- pickers (remember last dir) ----
    def _ask_open(self, caption, filetypes=None):
        p = filedialog.askopenfilename(title=caption, initialdir=self.last_dir,
                                       filetypes=filetypes or [("All", "*")])
        if p:
            self.last_dir = os.path.dirname(p)
        return p

    def _ask_dir(self, caption):
        p = filedialog.askdirectory(title=caption, initialdir=self.last_dir)
        if p:
            self.last_dir = p
        return p

    def _row(self, parent, label):
        f = tk.Frame(parent)
        f.pack(fill="x", padx=10, pady=2)
        tk.Label(f, text=label, width=20, anchor="w").pack(side="left")
        return f

    def _file_row(self, parent, label, var, kind="file", caption="Select"):
        f = self._row(parent, label)
        tk.Entry(f, textvariable=var).pack(side="left", fill="x", expand=True)

        def pick():
            p = self._ask_dir(caption) if kind == "dir" else self._ask_open(caption)
            if p:
                var.set(p)
        tk.Button(f, text="Browse…", command=pick).pack(side="left", padx=4)

    def _build(self):
        top = tk.Frame(self.root)
        top.pack(fill="x")
        tk.Label(top, text="APEX Dose Tool", font=("Helvetica", 16, "bold")).pack(
            side="left", padx=10, pady=6)
        tk.Label(top, text="short process · existing m2m", fg="#666").pack(
            side="left", pady=6)

        # Brainsight picker also loads target checkboxes
        f = self._row(self.root, "Brainsight .txt")
        tk.Entry(f, textvariable=self.v["brainsight"]).pack(side="left", fill="x", expand=True)
        tk.Button(f, text="Browse…", command=self._pick_brainsight).pack(side="left", padx=4)

        self._file_row(self.root, "m2m folder", self.v["m2m"], "dir", "Select m2m_<subject> folder")
        self._file_row(self.root, "Coil (.ccd)", self.v["coil"], "file", "Select coil file")
        self._file_row(self.root, "Output parent folder", self.v["out"], "dir",
                       "Pick any folder — an 'apex_<subject>' subfolder is created inside it")
        tk.Label(self.root, text="   (a fresh 'apex_<subject>' subfolder is created inside your "
                                 "choice — safe to pick any folder)", fg="#666").pack(anchor="w", padx=10)
        self._file_row(self.root, "simnibs_python", self.v["simpy"], "file", "Select simnibs_python")

        # targets: M1 dropdown + checkboxes
        tf = tk.LabelFrame(self.root, text="Targets (from Brainsight file)")
        tf.pack(fill="both", padx=10, pady=6)
        r1 = tk.Frame(tf); r1.pack(fill="x", pady=2)
        tk.Label(r1, text="M1 (rMT hotspot):", width=20, anchor="w").pack(side="left")
        self.m1_combo = ttk.Combobox(r1, textvariable=self.v["m1"], state="readonly")
        self.m1_combo.pack(side="left", fill="x", expand=True)

        r2 = tk.Frame(tf); r2.pack(fill="both", pady=2)
        tk.Label(r2, text="Stim targets (tick any):", width=20, anchor="nw").pack(side="left", anchor="n")
        # scrollable checkbox area
        canv = tk.Canvas(r2, height=150, highlightthickness=1, highlightbackground="#ccc")
        sb = tk.Scrollbar(r2, orient="vertical", command=canv.yview)
        self.tgt_inner = tk.Frame(canv)
        self.tgt_inner.bind("<Configure>",
                            lambda e: canv.configure(scrollregion=canv.bbox("all")))
        canv.create_window((0, 0), window=self.tgt_inner, anchor="nw")
        canv.configure(yscrollcommand=sb.set)
        canv.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        tk.Label(tf, text="Tip: pick M1 = where you measured rMT (often an FDI hotspot). "
                          "Each ticked target gets its own APEX dose.", fg="#666").pack(anchor="w", padx=6)

        # params
        pf = tk.LabelFrame(self.root, text="Parameters")
        pf.pack(fill="x", padx=10, pady=6)
        def param(lbl, var, width=8):
            g = tk.Frame(pf); g.pack(side="left", padx=6, pady=4)
            tk.Label(g, text=lbl).pack()
            tk.Entry(g, textvariable=var, width=width).pack()
        param("rMT (%MSO)", self.v["rmt"])
        param("dI/dt (A/µs)", self.v["didt"])
        g = tk.Frame(pf); g.pack(side="left", padx=6, pady=4)
        tk.Label(g, text="ROI mode").pack()
        ttk.Combobox(g, textvariable=self.v["roi_mode"], width=12, state="readonly",
                     values=["whole_gm", "coil_nearest", "coil_axis"]).pack()
        param("radius (mm)", self.v["radius"])
        param("percentile", self.v["pct"])
        param("percents", self.v["percents"], 10)
        param("custom %", self.v["custom"])

        # options
        of = tk.Frame(self.root); of.pack(fill="x", padx=10)
        for key, lbl in [("open_meshes", "Open E-field meshes (Gmsh)"),
                         ("show_spheres", "Show sphere on brain (FreeView)"),
                         ("open_csv", "Open results CSV"),
                         ("overwrite", "Overwrite existing output")]:
            tk.Checkbutton(of, text=lbl, variable=self.opt[key]).pack(side="left", padx=6)

        # run + log
        rf = tk.Frame(self.root); rf.pack(fill="x", padx=10, pady=6)
        self.run_btn = tk.Button(rf, text="Run", font=("Helvetica", 13, "bold"),
                                 bg="#2E7D32", fg="white", command=self._run)
        self.run_btn.pack(side="left")
        tk.Button(rf, text="Dry-run (validate only)",
                  command=lambda: self._run(dry=True)).pack(side="left", padx=6)

        self.log = tk.Text(self.root, height=14, bg="#1c1c2b", fg="#cdd5f2",
                           insertbackground="#cdd5f2", wrap="word")
        self.log.pack(fill="both", expand=True, padx=10, pady=(0, 10))

    # ---- target loading ----
    def _pick_brainsight(self):
        p = self._ask_open("Select Brainsight .txt export",
                           [("Text", "*.txt"), ("All", "*")])
        if p:
            self.v["brainsight"].set(p)
            self._load_targets(p)

    def _load_targets(self, path):
        names = list_target_names(path)
        for w in self.tgt_inner.winfo_children():
            w.destroy()
        self.tgt_vars = {}
        if not names:
            self._say(f"! No targets parsed from {Path(path).name}\n")
            return
        self.m1_combo["values"] = names
        for n in names:
            if "m1" in n.lower():
                self.v["m1"].set(n)
                break
        for n in names:
            var = tk.BooleanVar(value=False)
            self.tgt_vars[n] = var
            tk.Checkbutton(self.tgt_inner, text=n, variable=var, anchor="w").pack(
                fill="x", anchor="w")
        self._say(f"Loaded {len(names)} targets from {Path(path).name}\n")

    # ---- run ----
    def _validate(self):
        for key, lbl in [("brainsight", "Brainsight file"), ("m2m", "m2m folder"),
                         ("coil", "coil file"), ("out", "output parent folder"),
                         ("simpy", "simnibs_python")]:
            if not self.v[key].get().strip():
                messagebox.showerror("Missing", f"Please set: {lbl}")
                return None
        if not self.v["m1"].get().strip():
            messagebox.showerror("Missing", "Please choose the M1 target.")
            return None
        m1 = self.v["m1"].get()
        sel = [n for n, var in self.tgt_vars.items() if var.get() and n != m1]
        if not sel:
            messagebox.showerror("Missing", "Tick at least one stim target.")
            return None
        try:
            cfg = {
                "m2m_dir": self.v["m2m"].get().strip(),
                "brainsight_file": self.v["brainsight"].get().strip(),
                "coil_file": self.v["coil"].get().strip(),
                "out_dir": self.v["out"].get().strip(),
                "m1_target": m1,
                "targets": {s: s for s in sel},
                "didt_A_per_us": float(self.v["didt"].get()),
                "rmt_pct": float(self.v["rmt"].get()),
                "radius_mm": float(self.v["radius"].get()),
                "pct": float(self.v["pct"].get()),
                "roi_mode": self.v["roi_mode"].get(),
                "percents": [float(x) for x in self.v["percents"].get().split(",") if x.strip()],
                "custom_pct": float(self.v["custom"].get()) if self.v["custom"].get().strip() else None,
                "open_meshes": self.opt["open_meshes"].get(),
                "show_spheres": self.opt["show_spheres"].get(),
                "open_csv": self.opt["open_csv"].get(),
                "overwrite": self.opt["overwrite"].get(),
            }
        except ValueError as e:
            messagebox.showerror("Bad number", f"Check numeric fields:\n{e}")
            return None
        return cfg

    def _run(self, dry=False):
        cfg = self._validate()
        if cfg is None:
            return
        try:
            CONFIG_PATH.write_text(json.dumps(cfg, indent=2))
        except OSError as e:
            messagebox.showerror("Error", f"Could not write config:\n{e}")
            return
        cmd = [self.v["simpy"].get().strip(), str(ENGINE), str(CONFIG_PATH)]
        if dry:
            cmd.append("--dry-run")
        self.run_btn.config(state="disabled")
        self._say("\n$ " + " ".join(cmd) + "\n")
        threading.Thread(target=self._worker, args=(cmd,), daemon=True).start()

    def _worker(self, cmd):
        try:
            p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                 text=True, bufsize=1)
            for line in p.stdout:
                self.q.put(line)
            p.wait()
            self.q.put(f"\n[done] exit code {p.returncode}\n")
        except Exception as e:
            self.q.put(f"\n[error] {e}\n")
        finally:
            self.q.put("__ENABLE__")

    def _drain(self):
        try:
            while True:
                line = self.q.get_nowait()
                if line == "__ENABLE__":
                    self.run_btn.config(state="normal")
                else:
                    self._say(line)
        except queue.Empty:
            pass
        self.root.after(120, self._drain)

    def _say(self, text):
        self.log.insert("end", text)
        self.log.see("end")


if __name__ == "__main__":
    root = tk.Tk()
    DoseToolGUI(root)
    root.mainloop()
