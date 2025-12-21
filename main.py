#!/usr/bin/env python3

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