"""Safe ffmpeg utilities with timeout protection.

All ffmpeg/ffprobe calls in the application should use these wrappers
to prevent hangs on corrupted or incomplete media files.
"""

import logging
import subprocess
from typing import Any, Dict, Optional, Tuple

logger = logging.getLogger(__name__)

# Default timeout for ffprobe calls (seconds)
FFPROBE_TIMEOUT = 30

# Default timeout for ffmpeg frame extraction (seconds)
FFMPEG_FRAME_TIMEOUT = 60


def probe_with_timeout(
    file_path: str, timeout: int = FFPROBE_TIMEOUT
) -> Optional[Dict[str, Any]]:
    """Run ffprobe on a file with a timeout.

    Returns the probe result dict, or None if the probe fails or times out.
    """
    try:
        import ffmpeg

        # ffmpeg.probe() internally uses subprocess. We can't pass timeout
        # to it directly, so we call ffprobe ourselves with a timeout.
        cmd = [
            "ffprobe",
            "-v",
            "quiet",
            "-print_format",
            "json",
            "-show_format",
            "-show_streams",
            str(file_path),
        ]
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        if result.returncode != 0:
            logger.debug(f"ffprobe returned {result.returncode} for {file_path}")
            return None

        import json

        return json.loads(result.stdout)

    except subprocess.TimeoutExpired:
        logger.warning(f"ffprobe timed out after {timeout}s for {file_path}")
        return None
    except Exception as e:
        logger.debug(f"ffprobe failed for {file_path}: {e}")
        return None


def extract_frame_with_timeout(
    file_path: str,
    ss: float,
    width: int,
    height: int,
    timeout: int = FFMPEG_FRAME_TIMEOUT,
) -> Optional[bytes]:
    """Extract a single raw video frame using ffmpeg with a timeout.

    Args:
        file_path: Path to the video file.
        ss: Timestamp in seconds to seek to.
        width: Expected frame width.
        height: Expected frame height.
        timeout: Maximum seconds to wait.

    Returns:
        Raw RGB24 frame bytes, or None on failure/timeout.
    """
    try:
        import ffmpeg

        process = (
            ffmpeg.input(file_path, ss=ss)
            .output("pipe:", format="rawvideo", pix_fmt="rgb24", vframes=1)
            .run_async(pipe_stdout=True, pipe_stderr=True, quiet=True)
        )
        try:
            out, err = process.communicate(timeout=timeout)
            if process.returncode != 0:
                err_text = err[:500] if err else "unknown error"
                logger.debug(
                    f"ffmpeg exited with code {process.returncode} "
                    f"for {file_path} at {ss}s: {err_text}"
                )
                return None
            return out
        except subprocess.TimeoutExpired:
            process.kill()
            process.communicate()  # Clean up
            logger.warning(
                f"ffmpeg frame extraction timed out after {timeout}s "
                f"for {file_path} at {ss}s"
            )
            return None

    except Exception as e:
        logger.debug(f"Failed to extract frame from {file_path} at {ss}s: {e}")
        return None
