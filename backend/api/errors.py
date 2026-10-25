"""HTTP errors more than one router answers with."""

from __future__ import annotations

import logging

from fastapi import HTTPException

logger = logging.getLogger(__name__)


def trash_failed_error(what: str, exc: OSError) -> HTTPException:
    """500 ``{code: "trash_failed", message}`` for a delete whose file could
    be neither moved to the OS trash nor deleted (``trash_file`` raised).
    The T2I / I2V deletes trash before their DB delete, so the rows were
    kept. JSON like every other error answer, so the client's
    ``res.json()`` reads the message instead of a bare 500."""
    logger.warning("%s: the file could not be removed (%s)", what, exc)
    return HTTPException(
        status_code=500,
        detail={
            "code": "trash_failed",
            "message": f"Could not move the file to the trash or delete it: {exc}",
        },
    )
