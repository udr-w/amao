#!/usr/bin/env bash
# Builds diff_validator<suffix>.so -- CASE STUDY ONLY, see NATIVE_EXTENSIONS.md.
# Not wired into amao's real pipeline; this exists purely to be
# differentially fuzz-tested against the real Python validator.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"

SUFFIX="$(python3-config --extension-suffix)"
c++ -O3 -Wall -Wextra -shared -std=c++17 -fPIC \
    $(python3 -m pybind11 --includes) \
    diff_validator.cpp \
    -o "diff_validator${SUFFIX}"

echo "Built diff_validator${SUFFIX}"
