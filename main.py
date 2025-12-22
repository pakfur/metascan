#!/usr/bin/env python3

# Pre-load PyTorch c10.dll on Windows to prevent DLL loading errors
# See: https://github.com/pytorch/pytorch/issues/166628
import os
import platform

if platform.system() == "Windows":
    import ctypes
    from importlib.util import find_spec

    try:
        if (
            (spec := find_spec("torch"))
            and spec.origin
            and os.path.exists(
                dll_path := os.path.join(os.path.dirname(spec.origin), "lib", "c10.dll")
            )
        ):
            ctypes.CDLL(os.path.normpath(dll_path))
    except Exception:
        pass

# Initialize startup profiler FIRST before any other imports
from metascan.utils.startup_profiler import (
    init_startup_profiler,
    log_startup,
    profile_phase,
)

init_startup_profiler()
log_startup("main.py: Starting imports")

with profile_phase("Importing main_window module"):
    from metascan.ui.main_window import main

if __name__ == "__main__":
    log_startup("main.py: Calling main()")
    main()
