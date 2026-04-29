"""HEIC/HEIF support via pillow-heif.

Idempotent registration helper. Importers must call ``register_heif_opener()``
at module load time before any ``Image.open`` calls. Calling it more than once
is a no-op (pillow-heif keeps a single global registration).
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

_REGISTERED = False


def register_heif_opener() -> None:
    """Register pillow-heif with Pillow so HEIC/HEIF files are decodable.

    Safe to call multiple times. If pillow-heif isn't installed, logs a
    warning once and returns silently — HEIC files will then be skipped by
    the scanner instead of crashing the import.
    """
    global _REGISTERED
    if _REGISTERED:
        return
    try:
        from pillow_heif import register_heif_opener as _register

        _register()
        _REGISTERED = True
        logger.debug("pillow-heif registered.")
    except ImportError:
        _REGISTERED = True  # don't try again
        logger.warning(
            "pillow-heif not installed; HEIC/HEIF files will be skipped during scan."
        )
