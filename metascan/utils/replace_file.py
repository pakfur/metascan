"""``os.replace`` for a file another process may be reading.

On Windows, replacing a file fails with ``PermissionError`` while anything has
it open: the server polling a worker's ``progress.json``, an index being
loaded, a virus scanner. POSIX has no such window. Readers hold these files for
milliseconds, so a short retry is enough; losing the race must not fail a
training run. Elsewhere a ``PermissionError`` is a real one and raises at once.

Standard library only: the training worker imports this.
"""

import os
import sys
import time
from pathlib import Path
from typing import Callable, Tuple, Union

# 1.88 s in all, far longer than a reader holds a file open.
REPLACE_RETRY_DELAYS_S: Tuple[float, ...] = (0.01, 0.02, 0.05, 0.1, 0.2, 0.5, 1.0)


def replace_file(
    src: Union[str, Path],
    dst: Union[str, Path],
    *,
    platform: str = sys.platform,
    sleep: Callable[[float], None] = time.sleep,
) -> None:
    """Atomically move ``src`` over ``dst``, as ``os.replace`` does."""
    if platform == "win32":
        for delay in REPLACE_RETRY_DELAYS_S:
            try:
                os.replace(src, dst)
                return
            except PermissionError:
                sleep(delay)
    os.replace(src, dst)
