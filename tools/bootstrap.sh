#!/bin/sh
# A Python environment for the standard's tools beside the system's KiCad: the system python3 with its pcbnew module
# (KiCad's Debian/Ubuntu packages put it in dist-packages) plus kicad-python (the IPC API client) for tools/live.py.
# Debian and Ubuntu ship python3 without pip or ensurepip, so pip is bootstrapped from PyPA. Run from the repository root:
#     tools/bootstrap.sh
# and use .venv/bin/python for every tool.
set -e
cd "$(dirname "$0")/.."
python3 -c "import pcbnew" 2>/dev/null || { echo "the system python3 has no pcbnew module: install KiCad 9 or later"; exit 1; }
[ -d .venv ] || python3 -m venv --without-pip --system-site-packages .venv
.venv/bin/python -m pip --version >/dev/null 2>&1 || {
    tmp=$(mktemp); curl -sS https://bootstrap.pypa.io/get-pip.py -o "$tmp"; .venv/bin/python "$tmp" -q; rm -f "$tmp"; }
.venv/bin/python -m pip install -q --upgrade kicad-python
.venv/bin/python -c "import kipy, pcbnew, importlib.metadata as m; print('kicad-python', m.version('kicad-python'), '/ pcbnew', pcbnew.GetBuildVersion())" 2>&1 | grep -v "property.h\|Debug:"
