#!/usr/bin/env bash
# Builds progress_stats<suffix>.so for the pybind11 demo -- see NATIVE_EXTENSIONS.md.
# Dev/editable-install only. Requires `pip install pybind11` in the active venv.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"

SUFFIX="$(python3-config --extension-suffix)"
c++ -O3 -Wall -Wextra -shared -std=c++17 -fPIC \
    $(python3 -m pybind11 --includes) \
    progress_stats.cpp \
    -o "progress_stats${SUFFIX}"

echo "Built progress_stats${SUFFIX}"
