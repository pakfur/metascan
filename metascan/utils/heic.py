"""HEIC/HEIF support via pillow-heif.

Idempotent registration helper. Importers must call ``register_heif_opener()``
at module load time before any ``Image.open`` calls. Calling it more than once
is a no-op — this module guards against repeated calls with a module-level flag.

On some platforms (notably ARM macOS with certain libheif builds), the native
library segfaults during encode or decode. A subprocess probe catches that
before registration so the main process stays alive.
"""

from __future__ import annotations

import logging
import subprocess
import sys
import textwrap
from functools import lru_cache

logger = logging.getLogger(__name__)

_REGISTERED = False

# Minimal 1x1 black HEIF image (436 bytes). Used by the decode probe so we
# never invoke the encoder (which segfaults on some ARM macOS builds).
_HEIF_1X1_B64 = (
    "AAAAGGZ0eXBoZWljAAAAAG1pZjFoZWljAAABaW1ldGEAAAAAAAAAIWhkbHIAAAAAAAAAAHBpY3QA"
    "AAAAAAAAAAAAAAAAAAAADnBpdG0AAAAAAAEAAAAiaWxvYwAAAABEQAABAAEAAAAAAYkAAQAAAAAA"
    "AAArAAAAI2lpbmYAAAAAAAEAAAAVaW5mZQIAAAAAAQAAaHZjMQAAAADpaXBycAAAAMppcGNvAAAA"
    "dmh2Y0MBA3AAAAAAAAAAAAAe8AD8/fj4AAAPAyAAAQAYQAEMAf//A3AAAAMAkAAAAwAAAwAeugJA"
    "IQABACpCAQEDcAAAAwCQAAADAAADAB6gIIEFluqumubgIaDAgAAAAwCAAAADAIQiAAEABkQBwXPB"
    "iQAAABRpc3BlAAAAAAAAAEAAAABAAAAAKGNsYXAAAAABAAAAAQAAAAEAAAAB////wQAAAAL////B"
    "AAAAAgAAABBwaXhpAAAAAAMICAgAAAAXaXBtYQAAAAAAAAABAAEEgQIEgwAAADNtZGF0AAAAJygB"
    "rxMhMZb4TlCn//1nhc0MlUxauU+lc90KUJNyfrj+8oeTxWKC4A=="
)


@lru_cache(maxsize=1)
def _heif_decode_probe() -> bool:
    """Return True if pillow_heif can decode a HEIF image in a subprocess.

    Runs a throw-away Python process that imports pillow_heif and decodes
    a minimal embedded HEIF image. If the subprocess exits non-zero
    (including SIGSEGV / SIGABRT), the probe returns False.

    Encoding is deliberately *not* tested — the scanner only decodes, and
    certain ARM macOS libheif builds segfault during encode while decode
    works fine.
    """
    script = textwrap.dedent(
        f"""\
        import sys, io, base64
        try:
            from pillow_heif import register_heif_opener
            register_heif_opener()
            from PIL import Image
            data = base64.b64decode({_HEIF_1X1_B64!r})
            img = Image.open(io.BytesIO(data))
            img.load()
        except Exception as exc:
            print(exc, file=sys.stderr)
            sys.exit(1)
    """
    )
    try:
        result = subprocess.run(
            [sys.executable, "-c", script],
            capture_output=True,
            timeout=30,
        )
        return result.returncode == 0
    except Exception as exc:
        logger.debug("HEIF decode probe subprocess failed: %s", exc)
        return False


@lru_cache(maxsize=1)
def _heif_encode_probe() -> bool:
    """Return True if pillow_heif can encode a HEIF image in a subprocess.

    Separated from the decode probe because encoding segfaults on some
    ARM macOS builds while decoding works fine. Only needed by tests that
    create HEIF fixtures via ``Image.save(..., 'HEIF')``.
    """
    script = textwrap.dedent(
        """\
        import sys, io
        try:
            from pillow_heif import register_heif_opener
            register_heif_opener()
            from PIL import Image
            img = Image.new("RGB", (1, 1), (0, 0, 0))
            buf = io.BytesIO()
            img.save(buf, "HEIF")
        except Exception as exc:
            print(exc, file=sys.stderr)
            sys.exit(1)
    """
    )
    try:
        result = subprocess.run(
            [sys.executable, "-c", script],
            capture_output=True,
            timeout=30,
        )
        return result.returncode == 0
    except Exception as exc:
        logger.debug("HEIF encode probe subprocess failed: %s", exc)
        return False


def register_heif_opener() -> None:
    """Register pillow-heif with Pillow so HEIC/HEIF files are decodable.

    Safe to call multiple times. If pillow-heif isn't installed or its
    native library crashes during decode (detected via a subprocess probe),
    logs a warning once and returns silently — HEIC files will then be
    skipped by the scanner instead of crashing the process.
    """
    global _REGISTERED
    if _REGISTERED:
        return
    try:
        import pillow_heif  # noqa: F401 — just test importability
    except ImportError:
        _REGISTERED = True  # don't try again
        logger.warning(
            "pillow-heif not installed; HEIC/HEIF files will be skipped during scan."
        )
        return

    if not _heif_decode_probe():
        _REGISTERED = True
        logger.warning(
            "pillow-heif is installed but its native library failed a "
            "decode probe (possible segfault); HEIC/HEIF support disabled."
        )
        return

    from pillow_heif import register_heif_opener as _register

    _register()
    _REGISTERED = True
    logger.debug("pillow-heif registered.")
