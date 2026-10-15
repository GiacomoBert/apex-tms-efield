#!/usr/bin/env bash
# Launch the unified ReCARE pipeline GUI on Linux / Windows-WSL.
#
# Picks a Python with a WORKING tkinter for the window; the heavy work runs under
# the SimNIBS python (auto-detected, or set it in the GUI field). On Windows 11
# WSL2 the window + FreeView + Gmsh display through WSLg with no X server needed.
#
# One-time setup on Ubuntu / WSL:
#     sudo apt install python3-tk          # gives tkinter for the GUI window
#     # make sure SimNIBS and FreeSurfer are on PATH / FREESURFER_HOME is set
#     chmod +x run_pipeline.sh
# Then:  ./run_pipeline.sh

cd "$(dirname "$0")" || exit 1

have_tk () { "$1" -c "import tkinter" >/dev/null 2>&1; }

GUIPY=""
for c in "$(command -v python3)" "$(command -v simnibs_python)" /usr/bin/python3; do
    if [ -n "$c" ] && have_tk "$c"; then GUIPY="$c"; break; fi
done

if [ -z "$GUIPY" ]; then
    echo "No Python with a working tkinter found."
    echo "On Ubuntu / WSL install it with:  sudo apt install python3-tk"
    exit 1
fi

echo "GUI python:    $GUIPY"
echo "engine python: $(command -v simnibs_python || echo '<not on PATH — set it in the GUI>')"
"$GUIPY" pipeline_gui.py
