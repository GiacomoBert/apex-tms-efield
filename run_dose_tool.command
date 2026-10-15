#!/bin/bash
# Double-click to launch the APEX Dose Tool GUI.
#
# The GUI window needs a Python with a WORKING tkinter. On this Mac:
#   - system python3    -> Tk framework needs a newer macOS build (Abort trap: 6)
#   - simnibs python    -> has _tkinter but no TCL_LIBRARY set (can't find init.tcl)
#   - Homebrew python   -> works after `brew install python-tk`
# So we auto-pick the first Python whose `import tkinter` succeeds, trying brew
# first, then simnibs (after setting Tcl/Tk paths), then system.
#
# The heavy SIMULATION always runs under SimNIBS's python — that's the
# "simnibs_python" field inside the GUI, independent of what runs the window.
#
# First time only: if Gatekeeper blocks the double-click, right-click -> Open -> Open,
# or run once:  chmod +x run_dose_tool.command

cd "$(dirname "$0")" || exit 1

SIMPY="/Applications/SimNIBS-4.1/bin/simnibs_python"
[ -x "$SIMPY" ] || SIMPY="$(command -v simnibs_python || true)"

have_tk () { "$1" -c "import tkinter" >/dev/null 2>&1; }

GUIPY=""

# 1) Homebrew python (brew install python-tk gives it a working Tk)
if command -v brew >/dev/null 2>&1; then
    BP="$(brew --prefix)/bin/python3"
    if [ ! -x "$BP" ]; then
        # pick the highest python3.N interpreter, excluding *-config / *-build helpers
        BP="$(ls "$(brew --prefix)"/bin/python3.[0-9]* 2>/dev/null | grep -vE -- '-(config|build)$' | sort -V | tail -1)"
    fi
    if [ -n "$BP" ] && have_tk "$BP"; then GUIPY="$BP"; fi
fi

# 2) SimNIBS python, after pointing Tk at its bundled Tcl/Tk libraries
if [ -z "$GUIPY" ] && [ -n "$SIMPY" ]; then
    SIMROOT="$(cd "$(dirname "$SIMPY")/.." && pwd)"
    f=$(find "$SIMROOT/simnibs_env/lib" "$SIMROOT/lib" -maxdepth 3 -name init.tcl 2>/dev/null | head -1)
    [ -n "$f" ] && export TCL_LIBRARY="$(dirname "$f")"
    f=$(find "$SIMROOT/simnibs_env/lib" "$SIMROOT/lib" -maxdepth 3 -name tk.tcl 2>/dev/null | head -1)
    [ -n "$f" ] && export TK_LIBRARY="$(dirname "$f")"
    if have_tk "$SIMPY"; then GUIPY="$SIMPY"; fi
fi

# 3) system python3 (last resort; may abort on older macOS)
if [ -z "$GUIPY" ] && have_tk /usr/bin/python3; then GUIPY="/usr/bin/python3"; fi

if [ -z "$GUIPY" ]; then
    echo "No Python with a working tkinter found. Try:  brew install python-tk"
    read -r -p "Press Return to close." ; exit 1
fi

echo "GUI python:    $GUIPY"
echo "engine python: ${SIMPY:-<not found>}"
"$GUIPY" dose_tool_gui.py
status=$?
if [ $status -ne 0 ]; then
    echo ""; echo "GUI exited with code $status. Send this output if it failed."
    read -r -p "Press Return to close."
fi
