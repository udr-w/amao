"""pybind11 binding to native/progress_stats/ -- see NATIVE_EXTENSIONS.md.

Dev/editable-install only: adds `native/progress_stats/` (relative to this
file's location in the repo checkout) to sys.path and imports the compiled
extension module built by `native/progress_stats/build.sh`. Only resolves
for `pip install -e .` -- not built for distribution as a wheel. Exposes
`None` for both names when the extension isn't built/importable, so callers
fall back to the pure-Python implementation without this being a hard
dependency.
"""

from __future__ import annotations

import os
import sys
from typing import Any

_NATIVE_DIR = os.path.normpath(
    os.path.join(os.path.dirname(__file__), "..", "..", "native", "progress_stats")
)

MilestoneRow: Any = None
compute_progress_stats: Any = None

if _NATIVE_DIR not in sys.path:
    sys.path.insert(0, _NATIVE_DIR)
try:
    import progress_stats as _progress_stats

    MilestoneRow = _progress_stats.MilestoneRow
    compute_progress_stats = _progress_stats.compute_progress_stats
except ImportError:
    pass
