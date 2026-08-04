"""ctypes binding to native/duration/libduration.so -- see NATIVE_EXTENSIONS.md.

Dev/editable-install only: looks for the compiled library relative to this
file's location in the repo checkout (`../../native/duration/libduration.so`
from `src/amao/`), which only resolves correctly for `pip install -e .` -- not
built for distribution as a wheel. Returns None everywhere the library isn't
built or loadable so callers can fall back to the pure-Python implementation
without this being a hard new dependency.
"""

from __future__ import annotations

import ctypes
import os

_LIB_PATH = os.path.normpath(
    os.path.join(os.path.dirname(__file__), "..", "..", "native", "duration", "libduration.so")
)

_lib: ctypes.CDLL | None = None
_load_attempted = False


def _get_lib() -> ctypes.CDLL | None:
    global _lib, _load_attempted
    if not _load_attempted:
        _load_attempted = True
        try:
            lib = ctypes.CDLL(_LIB_PATH)
            lib.format_duration.argtypes = [ctypes.c_double, ctypes.c_char_p, ctypes.c_int]
            lib.format_duration.restype = None
            _lib = lib
        except OSError:
            _lib = None
    return _lib


def format_duration_native(seconds: float) -> str | None:
    lib = _get_lib()
    if lib is None:
        return None
    buf = ctypes.create_string_buffer(32)
    lib.format_duration(seconds, buf, len(buf))
    return buf.value.decode()
