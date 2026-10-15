#!/bin/bash
# Double-click to launch the unified ReCARE pipeline GUI.
# Picks a Python with a WORKING tkinter for the window (brew python-tk first, then
# SimNIBS python, then system). The heavy work runs under the SimNIBS python set
# inside the GUI; recon-all/CHARM run as detached background shells.
#
# First time only: if Gatekeeper blocks the double-click, right-click -> Open -> Open,
# or run once:  chmod +x run_pipeline.command

cd "$(dirname "$0")" || exit 1

SIMPY="/Applications/SimNIBS-4.1/bin/simnibs_python"
[ -x "$SIMPY" ] || SIMPY="$(command -v simnibs_python || true)"
have_tk () { "$1" -c "import tkinter" >/dev/null 2>&1; }
GUIPY=""

# 1) Homebrew python (brew install python-tk)
if command -v brew >/dev/null 2>&1; then
    BP="$(brew --prefix)/bin/python3"
    if [ ! -x "$BP" ]; then
        BP="$(ls "$(brew --prefix)"/bin/python3.[0-9]* 2>/dev/null | grep -vE -- '-(config|build)$' | sort -V | tail -1)"
    fi
    if [ -n "$BP" ] && have_tk "$BP"; then GUIPY="$BP"; fi
fi
# 2) SimNIBS python (set Tcl/Tk libs it ships but doesn't export)
if [ -z "$GUIPY" ] && [ -n "$SIMPY" ]; then
    SIMROOT="$(cd "$(dirname "$SIMPY")/.." && pwd)"
    f=$(find "$SIMROOT/simnibs_env/lib" "$SIMROOT/lib" -maxdepth 3 -name init.tcl 2>/dev/null | head -1)
    [ -n "$f" ] && export TCL_LIBRARY="$(dirname "$f")"
    f=$(find "$SIMROOT/simnibs_env/lib" "$SIMROOT/lib" -maxdepth 3 -name tk.tcl 2>/dev/null | head -1)
    [ -n "$f" ] && export TK_LIBRARY="$(dirname "$f")"
    if have_tk "$SIMPY"; then GUIPY="$SIMPY"; fi
fi
# 3) system python3
if [ -z "$GUIPY" ] && have_tk /usr/bin/python3; then GUIPY="/usr/bin/python3"; fi

if [ -z "$GUIPY" ]; then
    echo "No Python with a working tkinter found. Try:  brew install python-tk"
    read -r -p "Press Return to close." ; exit 1
fi
echo "GUI python:    $GUIPY"
echo "engine python: ${SIMPY:-<not found>}"
"$GUIPY" pipeline_gui.py
status=$?
[ $status -ne 0 ] && { echo "GUI exited with code $status."; read -r -p "Press Return to close."; }
