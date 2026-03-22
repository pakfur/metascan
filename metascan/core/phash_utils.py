"""Lightweight perceptual hashing utilities for use during scanning.

Separated from embedding_manager to avoid pulling in torch/clip/faiss
during the scan pipeline. Only depends on Pillow and imagehash.
"""

import logging
from pathlib import Path
from typing import Optional

from PIL import Image

logger = logging.getLogger(__name__)

_imagehash = None


def _ensure_imagehash() -> None:
    global _imagehash
    if _imagehash is None:
        import imagehash

        _imagehash = imagehash


def compute_phash_for_file(file_path: Path) -> Optional[str]:
    """Compute a perceptual hash for an image or video file.

    For images: hashes the image directly.
    For videos: hashes the first frame extracted via ffmpeg.

    Returns the hex string representation, or None on error.
    """
    ext = file_path.suffix.lower()
    if ext in {".mp4", ".webm"}:
        return _compute_video_phash(file_path)
    else:
        return _compute_image_phash(file_path)


def _compute_image_phash(file_path: Path) -> Optional[str]:
    try:
        _ensure_imagehash()
        assert _imagehash is not None
        image = Image.open(file_path).convert("RGB")
        return str(_imagehash.phash(image))
    except Exception as e:
        logger.debug(f"pHash failed for {file_path}: {e}")
        return None


def _compute_video_phash(file_path: Path) -> Optional[str]:
    try:
        import ffmpeg
        import numpy as np

        probe = ffmpeg.probe(str(file_path))
        video_stream = next(
            (s for s in probe["streams"] if s["codec_type"] == "video"), None
        )
        if not video_stream:
            return None

        width = int(video_stream.get("width", 224))
        height = int(video_stream.get("height", 224))

        out, _ = (
            ffmpeg.input(str(file_path), ss=0)
            .output("pipe:", format="rawvideo", pix_fmt="rgb24", vframes=1)
            .run(capture_stdout=True, capture_stderr=True, quiet=True)
        )

        _ensure_imagehash()
        assert _imagehash is not None

        frame = np.frombuffer(out, np.uint8).reshape(height, width, 3)
        image = Image.fromarray(frame)
        return str(_imagehash.phash(image))

    except Exception as e:
        logger.debug(f"Video pHash failed for {file_path}: {e}")
        return None
