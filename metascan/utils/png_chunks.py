"""Read a PNG's text and EXIF without decoding its pixels.

Pillow decodes the whole image (``PngImageFile.load``) the moment a PNG's
``text`` or ``getexif()`` is read, because text and eXIf chunks may also come
after the image data. The metadata extractors read them through
``MetadataExtractor._get_exif_metadata`` three times per import (the Fooocus
check, the ComfyUI check and the ComfyUI extract), so every PNG was decoded
three times just to read its prompt. ComfyUI, A1111, Fooocus and SwarmUI write
their metadata before the image data, so ``png_text`` / ``png_exif`` read the
chunks Pillow parsed when it opened the file, and decode only a file that has
text or EXIF after its image data (``metadata_after_data``).

Whether a PNG imports is NOT decided here: the scanner still decodes a PNG
with no eXIf before its image data, once (``Scanner._read_image_info_and_exif``),
and that decode alone turns away a file whose image data is cut short or
corrupt. A cheaper check cannot agree with it -- Pillow never checks an IDAT
chunk's CRC and stops once every row is decoded, while a stream can keep every
CRC and still not decode.
"""

from __future__ import annotations

import functools
import os
import struct
from pathlib import Path
from typing import BinaryIO, Dict, Optional, Union

from PIL import Image

PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"

_DATA_CHUNKS = frozenset({b"IDAT", b"fdAT"})
_END_CHUNK = b"IEND"
# Chunks Pillow reads text or EXIF from (PngStream.chunk_tEXt / _zTXt / _iTXt
# / _eXIf), which it only finds after the image data by decoding.
_METADATA_CHUNKS = frozenset({b"tEXt", b"zTXt", b"iTXt", b"eXIf"})


def metadata_after_data(path: Union[str, Path]) -> bool:
    """Whether a text or EXIF chunk follows the first image-data chunk of
    the PNG at ``path``, found by walking the chunk headers (nothing is read
    but them). It only picks how the metadata is read, never whether the
    file is imported: a file that can't be read, isn't a PNG, or ends before
    any such chunk says False. Remembered per file version (path, size,
    mtime, ctime and inode: a copy that keeps size and mtime still changes
    ctime); a read that fails is not remembered, so the next one retries."""
    try:
        st = os.stat(path)
        return _metadata_after_data(
            os.fspath(path), st.st_size, st.st_mtime_ns, st.st_ctime_ns, st.st_ino
        )
    except OSError:
        return False


@functools.lru_cache(maxsize=256)
def _metadata_after_data(
    path: str, size: int, mtime_ns: int, ctime_ns: int, ino: int
) -> bool:
    # Raises OSError, which lru_cache does not remember.
    with open(path, "rb") as f:
        return _walk(f)


def _walk(f: BinaryIO) -> bool:
    if f.read(len(PNG_SIGNATURE)) != PNG_SIGNATURE:
        return False
    seen_data = False
    while True:
        header = f.read(8)
        if len(header) < 8:
            return False
        length, kind = struct.unpack(">I4s", header)
        if kind == _END_CHUNK:
            return False
        if kind in _DATA_CHUNKS:
            seen_data = True
        elif seen_data and kind in _METADATA_CHUNKS:
            return True
        f.seek(length + 4, os.SEEK_CUR)


def _needs_decoding_reads(img: Image.Image) -> bool:
    """Whether ``img`` (an open PNG) must be read Pillow's decoding way: it
    has text or EXIF after its image data, or it was not opened from a
    path, so its chunks can't be walked."""
    path = getattr(img, "filename", "")
    return not path or metadata_after_data(path)


def _text_parsed_at_open(img: Image.Image) -> Optional[Dict[str, str]]:
    """The text chunks Pillow parsed before the image data when it opened
    ``img`` (``PngStream.im_text``, what ``img.text`` is built from; not
    ``img.info``, which holds non-text entries too and loses a text key a
    later chunk of another kind reuses, such as a tEXt "gamma" before
    gAMA). None if this Pillow keeps them elsewhere."""
    seen = getattr(getattr(img, "png", None), "im_text", None)
    return dict(seen) if isinstance(seen, dict) else None


def png_text(img: Image.Image) -> Dict[str, str]:
    """The text chunks of an open PNG, exactly what ``img.text`` holds,
    without decoding unless the file has text or EXIF after its image data
    (or this Pillow keeps the parsed text where it can't be found)."""
    if not _needs_decoding_reads(img):
        text = _text_parsed_at_open(img)
        if text is not None:
            return text
    return dict(img.text)  # type: ignore[attr-defined]


def png_exif(img: Image.Image) -> Image.Exif:
    """``img.getexif()`` for an open PNG without decoding it: the EXIF
    before the image data (an eXIf chunk, a "Raw profile type exif" text
    chunk, an XMP orientation), through Pillow's format-independent
    ``Image.getexif``. Pillow's own PNG ``getexif`` (which decodes) when the
    file has text or EXIF after its image data. The answer is cached on
    ``img``: read the EXIF of an image this returned for only through this
    function, or a later ``img.getexif()`` misses EXIF after the data."""
    if _needs_decoding_reads(img):
        return img.getexif()
    return Image.Image.getexif(img)
